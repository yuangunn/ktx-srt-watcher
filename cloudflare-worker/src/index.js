// KTX/SRT Watcher · Cloudflare Worker.
//
// Two crons fire on this Worker:
//   "*/30 * * * *"  → dispatchWatcher: GHA repository_dispatch every 30 min,
//                     working around the GHA private/free-tier scheduled-
//                     workflow throttle (which we measured at ~48 min).
//   "*/1 * * * *"   → processReminders: walks the REMINDERS KV and fires
//                     any due payment-deadline reminders to Telegram.
//
// HTTP surface:
//   GET  /health             plain liveness check
//   POST /dispatch           manually trigger a watcher run
//   POST /reminder/schedule  worker.main calls this after a successful
//                            auto-reservation; auth: Bearer REMINDER_TOKEN

export default {
  async scheduled(event, env, ctx) {
    if (event.cron === "*/30 * * * *") {
      ctx.waitUntil(dispatchWatcher(env, "cron"));
    } else if (event.cron === "*/1 * * * *") {
      ctx.waitUntil(processReminders(env));
    }
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/health") {
      return text("OK · ktx-srt-watcher cron bridge\n");
    }
    if (url.pathname === "/dispatch" && request.method === "POST") {
      try {
        await dispatchWatcher(env, "manual");
        return text("dispatched\n", 202);
      } catch (e) {
        return text(`error: ${e.message}\n`, 500);
      }
    }
    if (url.pathname === "/reminder/schedule" && request.method === "POST") {
      const auth = request.headers.get("authorization") || "";
      if (auth !== `Bearer ${env.REMINDER_TOKEN}`) {
        return text("unauthorized\n", 401);
      }
      try {
        const body = await request.json();
        await scheduleReminder(env, body);
        return text("scheduled\n", 202);
      } catch (e) {
        return text(`error: ${e.message}\n`, 500);
      }
    }
    return text("ktx-srt-watcher cron bridge\n");
  },
};

async function dispatchWatcher(env, source) {
  const url = `https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "ktx-srt-watcher-cf-bridge",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      event_type: env.EVENT_TYPE,
      client_payload: { source, ts: new Date().toISOString() },
    }),
  });
  if (res.status !== 204) {
    const body = await res.text();
    throw new Error(`GitHub dispatch failed: ${res.status} ${body}`);
  }
  console.log(`[${new Date().toISOString()}] dispatched ${env.EVENT_TYPE} (source=${source})`);
}

// REMINDER STATE in KV
//   key:   reservation_id (e.g. "320260442500832")
//   value: {
//     id, route, train, date, deadline_iso,    // metadata for messages
//     reminders: [
//       { trigger_at_ms, remaining_min, kind: "warn"|"final", sent: false }
//     ]
//   }
// TTL: deadline + 5min so KV self-cleans after the reservation is past.

const REMINDER_OFFSETS_MIN = [5, 10, 15, 19];

async function scheduleReminder(env, body) {
  if (!body || !body.reservation_id || !body.deadline_iso) {
    throw new Error("reservation_id and deadline_iso required");
  }
  const deadline = new Date(body.deadline_iso).getTime();
  if (!Number.isFinite(deadline)) {
    throw new Error("deadline_iso unparseable");
  }
  const now = Date.now();
  const reminders = [];
  for (const offsetMin of REMINDER_OFFSETS_MIN) {
    const triggerAtMs = now + offsetMin * 60_000;
    if (triggerAtMs >= deadline - 30_000) continue; // skip if too close to deadline
    const remainingMin = Math.round((deadline - triggerAtMs) / 60_000);
    reminders.push({
      trigger_at_ms: triggerAtMs,
      remaining_min: remainingMin,
      kind: offsetMin === Math.max(...REMINDER_OFFSETS_MIN) ? "final" : "warn",
      sent: false,
    });
  }
  const record = {
    id: body.reservation_id,
    route: body.route || "",
    train: body.train || "",
    date: body.date || "",
    deadline_iso: body.deadline_iso,
    provider: body.provider || "korail",
    reminders,
  };
  const ttlSec = Math.max(60, Math.floor((deadline - now) / 1000) + 300);
  await env.REMINDERS.put(body.reservation_id, JSON.stringify(record), {
    expirationTtl: ttlSec,
  });
  console.log(`[${new Date().toISOString()}] scheduled ${reminders.length} reminders for ${body.reservation_id}`);
}

async function processReminders(env) {
  const list = await env.REMINDERS.list();
  const now = Date.now();
  for (const key of list.keys) {
    const raw = await env.REMINDERS.get(key.name);
    if (!raw) continue;
    let record;
    try { record = JSON.parse(raw); } catch { continue; }
    let dirty = false;
    for (const r of record.reminders) {
      if (r.sent || r.trigger_at_ms > now) continue;
      try {
        await sendTelegram(env, formatReminder(record, r));
        r.sent = true;
        dirty = true;
      } catch (e) {
        console.error(`reminder send failed for ${record.id}: ${e.message}`);
        // leave sent=false; will retry on next minute
      }
    }
    if (dirty) {
      const allSent = record.reminders.every(r => r.sent);
      if (allSent) {
        await env.REMINDERS.delete(key.name);
      } else {
        const ttlSec = Math.max(60, Math.floor((new Date(record.deadline_iso).getTime() - now) / 1000) + 300);
        await env.REMINDERS.put(key.name, JSON.stringify(record), { expirationTtl: ttlSec });
      }
    }
  }
}

function formatReminder(rec, reminder) {
  const app = rec.provider === "srt" ? "SR 앱" : "코레일톡";
  const head = reminder.kind === "final"
    ? `⛔ 결제 마감 약 ${reminder.remaining_min}분 남음 — 마지막 기회`
    : `🔔 결제 마감 약 ${reminder.remaining_min}분 남음`;
  return [
    head,
    "",
    `${rec.route} ${rec.date}`,
    rec.train,
    `예약번호 ${rec.id}`,
    "",
    `${app}에서 결제하세요.`,
  ].join("\n");
}

async function sendTelegram(env, body) {
  const url = `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify({
      chat_id: env.TELEGRAM_CHAT_ID,
      text: body,
      disable_web_page_preview: true,
    }),
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(`telegram ${res.status}: ${t}`);
  }
}

function text(body, status = 200) {
  return new Response(body, {
    status,
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
}
