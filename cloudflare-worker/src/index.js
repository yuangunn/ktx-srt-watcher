// KTX/SRT Watcher · Cloudflare Worker.
//
// Two crons fire on this Worker:
//   "*/3 * * * *"   → dispatchWatcher: GHA repository_dispatch every 3 min.
//                     Trigger cadence only — the watcher's throttle decides
//                     whether a tick reaches Korail/SRT, and a throttled one
//                     exits before pip install. Polls land on tick boundaries,
//                     so the spacing quantises the configured interval upward;
//                     */3 keeps a randomised range spread instead of piling it
//                     onto two values the way */5 did.
//   "*/1 * * * *"   → processReminders: reads the REMINDERS KV (single key)
//                     and fires any due payment-deadline reminders to Telegram.
//                     Uses a single .get() instead of .list() to stay within
//                     the free tier (1000 list ops/day limit).
//
// HTTP surface (auth is Bearer; APP_TOKEN = PWA, REMINDER_TOKEN = GHA):
//   GET  /health             plain liveness check, public
//   POST /dispatch           manually trigger a watcher run
//   POST /reminder/schedule  worker.main calls this after a successful
//                            auto-reservation; REMINDER_TOKEN
//   GET  /config             watch list; APP_TOKEN or REMINDER_TOKEN
//   PUT  /config             PWA writes the watch list; APP_TOKEN
//   GET  /config/backups     last CONFIG_BACKUPS versions; APP_TOKEN
//   GET  /reminder/done      one-tap "I paid, stop the reminders"; HMAC token
//                            in the query, since a notification link cannot POST
//   GET  /state              poll state; APP_TOKEN or REMINDER_TOKEN
//   PUT  /state              GHA watcher writes state; REMINDER_TOKEN
//
// config.json and state.json used to be committed to this (public) repo.
// Watch ids embed the route and travel date — "서울-대전-20260101-a1b2" —
// so anyone could read when the user's home would be empty.  Both now live
// in the STATE KV namespace and are served only against a token.

// /health is public by design (liveness), so a wildcard origin costs nothing.
// The data routes below are browser-facing too, but gated on a bearer token —
// wildcard is still safe there because the token is an explicit header, never
// an ambient credential a hostile page could ride on.
const CORS = { "Access-Control-Allow-Origin": "*" };
const CORS_PREFLIGHT = {
  ...CORS,
  "Access-Control-Allow-Methods": "GET, PUT, POST, OPTIONS",
  "Access-Control-Allow-Headers": "authorization, content-type",
  "Access-Control-Max-Age": "86400",
};

// KV keys inside the STATE namespace.
const STATE_KEY = "current";
const CONFIG_KEY = "config";
// "home" | "away" — decides whether an urgent alert is allowed to override
// the phone's mute switch.  Only the phone knows where the user is, so it
// pushes the value here (iOS Shortcuts automation, or the PWA toggle).
const MODE_KEY = "alert_mode";
const MODE_HOME = "home";
const MODE_AWAY = "away";

// How long a manual flip of the toggle outranks the Shortcut automation.
// The user knows something the geofence does not — staying late, going out
// without the phone noticing — so their choice wins. Bounded, though: an
// override that never expired would silently mute alerts for good once
// forgotten.
const MANUAL_TTL_MS = 6 * 60 * 60 * 1000;

// Stored as JSON. Older deployments wrote a bare "home"/"away" string, so
// read through both shapes.
async function readModeRecord(env) {
  const raw = await env.STATE.get(MODE_KEY);
  if (!raw) return { mode: MODE_HOME, manual_until: 0 };
  try {
    const v = JSON.parse(raw);
    if (v && typeof v === "object") {
      return {
        mode: v.mode === MODE_AWAY ? MODE_AWAY : MODE_HOME,
        manual_until: Number(v.manual_until) || 0,
      };
    }
  } catch {
    // fall through to the legacy bare-string form
  }
  return { mode: raw === MODE_AWAY ? MODE_AWAY : MODE_HOME, manual_until: 0 };
}

// Default home: a missed 3am cancellation costs a trip, a stray alert in a
// lecture costs embarrassment.  Fail toward the louder mistake.
async function readMode(env) {
  return (await readModeRecord(env)).mode;
}

// Two callers, two secrets.  APP_TOKEN lives in the PWA (extractable from the
// user's own device); REMINDER_TOKEN lives in GHA secrets.  Keeping them
// separate means a leaked app token can't schedule reminders or write state.
function authorized(request, env, ...allowed) {
  const auth = request.headers.get("authorization") || "";
  return allowed.some(t => t && auth === `Bearer ${t}`);
}

function json(body, status = 200) {
  return new Response(typeof body === "string" ? body : JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      ...CORS,
    },
  });
}

// Depth of the config backup ring.  Small on purpose: this guards against a
// bad write from the app, not against long-term data loss.
const CONFIG_BACKUPS = 5;

// Shift bak:0..n-1 down one slot and store the previous config at bak:0.
async function rotateConfigBackups(env, prevBody) {
  for (let i = CONFIG_BACKUPS - 1; i > 0; i--) {
    const older = await env.STATE.get(`${CONFIG_KEY}:bak:${i - 1}`);
    if (older) await env.STATE.put(`${CONFIG_KEY}:bak:${i}`, older);
  }
  await env.STATE.put(`${CONFIG_KEY}:bak:0`, prevBody);
}

export default {
  async scheduled(event, env, ctx) {
    if (event.cron === "*/3 * * * *") {
      ctx.waitUntil(dispatchWatcher(env, "cron"));
    } else if (event.cron === "*/1 * * * *") {
      ctx.waitUntil(processReminders(env));
    } else if (event.cron === "0 */6 * * *") {
      ctx.waitUntil(heartbeatCheck(env));
    } else if (event.cron === "0 0 * * *") {
      ctx.waitUntil(checkPATExpiration(env));
    } else if (event.cron === "0 12 * * SUN") {
      ctx.waitUntil(weeklySummary(env));
    }
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/health") {
      // CORS is required: the PWA health card fetches this cross-origin from
      // Pages.  Without the header the browser rejects the response and the
      // card reports the Worker dead while it is answering 200 just fine.
      return text("OK · ktx-srt-watcher cron bridge\n", 200, CORS);
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
    // Browsers preflight any request carrying an Authorization header.
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS_PREFLIGHT });
    }

    // state.json now lives here, not in the public repo — watch ids embed the
    // route and travel date, so this is no longer readable without a token.
    if (url.pathname === "/state" && request.method === "GET") {
      if (!authorized(request, env, env.APP_TOKEN, env.REMINDER_TOKEN)) {
        return text("unauthorized\n", 401);
      }
      const stored = await env.STATE.get(STATE_KEY);
      if (!stored) return text("no state yet\n", 404);
      return json(stored);
    }
    // The GHA watcher is the only writer.
    if (url.pathname === "/state" && request.method === "PUT") {
      if (!authorized(request, env, env.REMINDER_TOKEN)) {
        return text("unauthorized\n", 401);
      }
      const body = await request.text();
      try {
        JSON.parse(body);
      } catch (e) {
        return text(`invalid JSON: ${e.message}\n`, 400);
      }
      await env.STATE.put(STATE_KEY, body);
      return text("stored\n", 202);
    }

    // config.json lives here too.  Read by both the PWA and the watcher.
    if (url.pathname === "/config" && request.method === "GET") {
      if (!authorized(request, env, env.APP_TOKEN, env.REMINDER_TOKEN)) {
        return text("unauthorized\n", 401);
      }
      const stored = await env.STATE.get(CONFIG_KEY);
      if (!stored) return text("no config yet\n", 404);
      return json(stored);
    }
    // The PWA is the only writer.  Previous versions are kept in a small ring
    // so a bad write from the app doesn't destroy the watch list outright —
    // KV has no history of its own and this is now the only copy.
    if (url.pathname === "/config" && request.method === "PUT") {
      if (!authorized(request, env, env.APP_TOKEN)) {
        return text("unauthorized\n", 401);
      }
      const body = await request.text();
      try {
        JSON.parse(body);
      } catch (e) {
        return text(`invalid JSON: ${e.message}\n`, 400);
      }
      const prev = await env.STATE.get(CONFIG_KEY);
      if (prev) await rotateConfigBackups(env, prev);
      await env.STATE.put(CONFIG_KEY, body);
      return text("stored\n", 202);
    }
    if (url.pathname === "/mode" && request.method === "GET") {
      if (!authorized(request, env, env.APP_TOKEN, env.REMINDER_TOKEN)) {
        return text("unauthorized\n", 401);
      }
      const rec = await readModeRecord(env);
      return json({
        mode: rec.mode,
        manual_until: rec.manual_until,
        manual: rec.manual_until > Date.now(),
      });
    }
    // Written by the PWA toggle and by an iOS Shortcuts automation on
    // arriving/leaving home.  Accepts the value in a JSON body or as ?mode=,
    // because Shortcuts is far easier to set up with a bare query string.
    //
    // ?src=auto marks the write as coming from the automation. A manual flip
    // outranks it for MANUAL_TTL_MS: the geofence does not know the user
    // stayed late or stepped out without their phone, and having the
    // automation quietly undo a deliberate choice is worse than being
    // slightly stale.
    if (url.pathname === "/mode" && request.method === "PUT") {
      if (!authorized(request, env, env.APP_TOKEN)) {
        return text("unauthorized\n", 401);
      }
      let mode = url.searchParams.get("mode");
      let src = url.searchParams.get("src");
      if (!mode) {
        try {
          const body = await request.json();
          mode = body.mode;
          src = src || body.src;
        } catch {
          return text("mode required\n", 400);
        }
      }
      if (mode !== MODE_HOME && mode !== MODE_AWAY) {
        return text(`mode must be "${MODE_HOME}" or "${MODE_AWAY}"\n`, 400);
      }
      const now = Date.now();
      const current = await readModeRecord(env);
      if (src === "auto") {
        if (current.manual_until > now) {
          return json({
            mode: current.mode,
            manual_until: current.manual_until,
            skipped: "manual override active",
          });
        }
        await env.STATE.put(MODE_KEY, JSON.stringify({ mode, manual_until: 0 }));
        return json({ mode, manual_until: 0 });
      }
      // "clear" releases the override without changing the mode, handing
      // control back to the automation.
      const manual_until = url.searchParams.get("hold") === "clear" ? 0 : now + MANUAL_TTL_MS;
      await env.STATE.put(MODE_KEY, JSON.stringify({ mode, manual_until }));
      return json({ mode, manual_until });
    }
    // Tapped from a reminder once the user has paid. A mutating GET is the
    // deliberate choice: it has to survive being a plain link in a Telegram
    // message and a Pushover notification, where nothing can send a POST.
    // The HMAC token is single-purpose and unguessable, so the exposure is a
    // link that can only silence one reservation's reminders.
    if (url.pathname === "/reminder/done" && request.method === "GET") {
      const id = url.searchParams.get("id") || "";
      const given = url.searchParams.get("t") || "";
      if (!id || !given || !tokensEqual(given, await cancelToken(env, id))) {
        return html("<h1>링크가 올바르지 않습니다</h1><p>알림을 중지하지 못했습니다.</p>", 403);
      }
      const all = await getAllReminders(env);
      const existed = Boolean(all[id]);
      if (existed) {
        delete all[id];
        await saveAllReminders(env, all);
      }
      return html(
        existed
          ? "<h1>알림을 중지했습니다</h1><p>이 예약의 결제 마감 알림이 더 이상 오지 않습니다.</p>"
          : "<h1>이미 중지되어 있습니다</h1><p>보낼 알림이 남아 있지 않습니다.</p>",
      );
    }
    // Read-only view of the backup ring, newest first, for manual recovery.
    if (url.pathname === "/config/backups" && request.method === "GET") {
      if (!authorized(request, env, env.APP_TOKEN)) {
        return text("unauthorized\n", 401);
      }
      const out = [];
      for (let i = 0; i < CONFIG_BACKUPS; i++) {
        const v = await env.STATE.get(`${CONFIG_KEY}:bak:${i}`);
        if (v) out.push(JSON.parse(v));
      }
      return json(out);
    }
    return text("ktx-srt-watcher cron bridge\n", 200, CORS);
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
//   key:   "all"  (single JSON blob — avoids KV list operations)
//   value: { [reservation_id]: {
//     id, route, train, date, deadline_iso,
//     provider,
//     reminders: [
//       { trigger_at_ms, remaining_min, kind: "warn"|"final", sent: false }
//     ]
//   } }
//
// Using a single key instead of per-reservation keys eliminates `.list()`
// calls entirely. The free tier allows 10M reads/day vs only 1000 lists/day,
// so one `.get("all")` per minute (1440/day) stays well within free limits.

// Payment-deadline reminders, in minutes after the hold was placed (~20 min
// window). Dense near the end so a sleeping user gets several chances: the
// final stretch fires every minute rather than once.
const REMINDER_OFFSETS_MIN = [3, 6, 9, 12, 14, 15, 16, 17, 18, 19];
const ALL_KEY = "all";

// One-tap "I paid, stop" link carried in every reminder. Derived by HMAC from
// the reservation id so both sides can compute it without storing or passing
// anything, and so it cannot be guessed for someone else's reservation.
async function cancelToken(env, reservationId) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw", enc.encode(env.REMINDER_TOKEN || ""),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(String(reservationId)));
  return [...new Uint8Array(sig)].map(b => b.toString(16).padStart(2, "0")).join("").slice(0, 16);
}

// Constant-time compare so a wrong token can't be narrowed down by timing.
function tokensEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function getAllReminders(env) {
  const raw = await env.REMINDERS.get(ALL_KEY);
  return raw ? JSON.parse(raw) : {};
}

async function saveAllReminders(env, data) {
  await env.REMINDERS.put(ALL_KEY, JSON.stringify(data));
}

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
      // Anything inside the last 5 minutes reads as urgent, not just the
      // single latest offset — these are the ones that must cut through.
      kind: remainingMin <= 5 ? "final" : "warn",
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
  const all = await getAllReminders(env);
  all[body.reservation_id] = record;
  await saveAllReminders(env, all);
  console.log(`[${new Date().toISOString()}] scheduled ${reminders.length} reminders for ${body.reservation_id}`);
}

async function processReminders(env) {
  const all = await getAllReminders(env);
  const ids = Object.keys(all);
  if (!ids.length) return;
  const now = Date.now();
  let dirty = false;
  for (const id of ids) {
    const record = all[id];
    // Auto-cleanup expired reservations (deadline + 5min passed)
    const deadline = new Date(record.deadline_iso).getTime();
    if (deadline + 300_000 < now) {
      delete all[id];
      dirty = true;
      continue;
    }
    for (const r of record.reminders) {
      if (r.sent || r.trigger_at_ms > now) continue;
      try {
        const cancelUrl = env.WORKER_URL
          ? `${env.WORKER_URL.replace(/\/$/, "")}/reminder/done?id=${encodeURIComponent(record.id)}&t=${await cancelToken(env, record.id)}`
          : "";
        const body = formatReminder(record, r, cancelUrl);
        await sendTelegram(env, body);
        // Telegram cannot override the iOS mute switch, which is how a
        // payment deadline gets slept through. Pushover can, so the last
        // stretch goes out as an emergency alert that repeats until it is
        // acknowledged in the app.
        // Away: still alert, but at a priority that respects the mute
        // switch — a lecture is the wrong place to be rung at full volume.
        const away = (await readMode(env)) === MODE_AWAY;
        await sendPushover(env, {
          title: r.kind === "final" ? "⛔ 결제 마감 임박" : "🔔 결제 마감 알림",
          message: body,
          priority: r.kind === "final" && !away ? 2 : 1,
          url: cancelUrl,
          urlTitle: "결제 완료 — 알림 중지",
        });
        r.sent = true;
        dirty = true;
      } catch (e) {
        console.error(`reminder send failed for ${record.id}: ${e.message}`);
        // leave sent=false; will retry on next minute
      }
    }
    // Remove once all reminders sent
    if (record.reminders.every(r => r.sent)) {
      delete all[id];
      dirty = true;
    }
  }
  if (dirty) await saveAllReminders(env, all);
}

function formatReminder(rec, reminder, cancelUrl) {
  const app = rec.provider === "srt" ? "SR 앱" : "코레일톡";
  const head = reminder.kind === "final"
    ? `⛔ 결제 마감 약 ${reminder.remaining_min}분 남음 — 마지막 기회`
    : `🔔 결제 마감 약 ${reminder.remaining_min}분 남음`;
  const lines = [
    head,
    "",
    `${rec.route} ${rec.date}`,
    rec.train,
    `예약번호 ${rec.id}`,
    "",
    `${app}에서 결제하세요.`,
  ];
  // Nothing here knows whether the user has already paid — Korail/SR are only
  // reachable from the Python watcher, which may not run again inside the
  // 20-minute window. So the message carries its own off switch.
  if (cancelUrl) {
    lines.push("", `결제를 마쳤다면 눌러서 알림 중지 → ${cancelUrl}`);
  }
  return lines.join("\n");
}

// Pushover is the channel that can actually wake the user: iOS only lets an
// app bypass the mute switch and Do Not Disturb with Apple's Critical Alerts
// entitlement, which Telegram does not have.  Silent no-op when unconfigured,
// and never throws — Telegram has already delivered by the time we get here,
// so a Pushover outage must not mark the reminder unsent and re-fire it.
const PUSHOVER_RETRY_SEC = 60;
const PUSHOVER_EXPIRE_SEC = 900;
// Whoever the phone wakes may have no idea what Pushover is — a partner, a
// parent — and without this the only way to stop the ringing is to find
// someone who does. The button is English in the app, so quote it verbatim.
const PUSHOVER_STOP_HINT =
  "\n\n🔁 이 알림은 확인할 때까지 반복됩니다.\n" +
  "멈추려면 Pushover 앱을 열고 [Acknowledge] 버튼을 누르세요.";

async function sendPushover(env, { title, message, priority = 1, url, urlTitle }) {
  if (!env.PUSHOVER_TOKEN || !env.PUSHOVER_USER) return;
  const form = new URLSearchParams({
    token: env.PUSHOVER_TOKEN,
    user: env.PUSHOVER_USER,
    title,
    // Only the repeating priority needs the hint.
    message: priority === 2 ? message + PUSHOVER_STOP_HINT : message,
    priority: String(priority),
  });
  if (url) {
    form.set("url", url);
    if (urlTitle) form.set("url_title", urlTitle);
  }
  if (priority === 2) {
    form.set("retry", String(PUSHOVER_RETRY_SEC));
    form.set("expire", String(PUSHOVER_EXPIRE_SEC));
  }
  try {
    const res = await fetch("https://api.pushover.net/1/messages.json", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: form,
    });
    if (!res.ok) console.error(`pushover ${res.status}: ${await res.text()}`);
  } catch (e) {
    console.error(`pushover failed: ${e.message}`);
  }
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

// HEARTBEAT: alert if the watcher hasn't actually polled in too long.
// We read state.json from main and compare its last_run against now;
// state.json::last_run is bumped at the start of every non-skipped
// run_watches, so a value older than HEARTBEAT_STALE_HOURS means the
// worker has been silently broken (CF Worker dead, PAT expired, GHA
// suspended, repository_dispatch malformed, etc.).
const HEARTBEAT_STALE_HOURS = 6;

async function heartbeatCheck(env) {
  const stateJson = await readStateKV(env);
  if (!stateJson) {
    console.error("heartbeat: no state in KV");
    return;
  }

  const lastRun = stateJson?.last_run;
  if (!lastRun) {
    // Never run yet — not a stale-watcher case, just nothing to alert on
    return;
  }
  const elapsedHours = (Date.now() - new Date(lastRun).getTime()) / 3_600_000;
  if (elapsedHours < HEARTBEAT_STALE_HOURS) return;

  const watchCount = Object.keys(stateJson.watches || {}).length;
  await sendTelegram(
    env,
    `⏰ 워커가 ${Math.floor(elapsedHours)}시간 동안 폴링되지 않았습니다.\n\n` +
      `마지막 실행: ${lastRun}\n` +
      `활성 워치: ${watchCount}건\n\n` +
      `점검 대상:\n` +
      `- CF Worker tail (\`npx wrangler tail\`)\n` +
      `- GitHub Actions runs 페이지\n` +
      `- PAT 만료 여부 (Settings → Developer settings)`,
  );
  console.log(`[${new Date().toISOString()}] heartbeat alert: ${elapsedHours.toFixed(1)}h since last_run`);
}

// PAT EXPIRATION: GitHub responds to fine-grained PAT requests with a
// 'github-authentication-token-expiration' header (RFC 1123 timestamp).
// We read it once a day; if the worker's GITHUB_TOKEN is < 7 days from
// expiry we Telegram a reminder. The same PAT is used by /reminder/
// schedule auth so its expiry would silently break that path too.
const PAT_WARN_DAYS = 7;

async function checkPATExpiration(env) {
  let res;
  try {
    res = await fetch("https://api.github.com/user", {
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ktx-srt-watcher-cf-bridge",
      },
    });
  } catch (e) {
    console.error(`PAT check: network error: ${e.message}`);
    return;
  }
  if (!res.ok) {
    if (res.status === 401) {
      // Token already invalid — alert immediately
      await sendTelegram(
        env,
        `🚨 CF Worker GITHUB_TOKEN 인증 실패 (401).\n\n` +
          `토큰이 만료/회수됐을 가능성. 즉시 갱신 필요:\n` +
          `1. github.com/settings/personal-access-tokens 에서 새 PAT 발급\n` +
          `   (Contents:rw + Actions:rw on yuangunn/ktx-srt-watcher)\n` +
          `2. \`npx wrangler secret put GITHUB_TOKEN\` 으로 갱신`,
      );
    } else {
      console.error(`PAT check: HTTP ${res.status}`);
    }
    return;
  }
  const expiry = res.headers.get("github-authentication-token-expiration");
  if (!expiry) {
    // Classic PAT or no-expiry PAT — nothing to warn about
    return;
  }
  const expiryDate = new Date(expiry);
  const daysLeft = (expiryDate.getTime() - Date.now()) / 86_400_000;
  if (daysLeft > PAT_WARN_DAYS) {
    console.log(`PAT check: ${daysLeft.toFixed(1)} days left, no alert`);
    return;
  }
  const dayLabel = Math.max(0, Math.ceil(daysLeft));
  await sendTelegram(
    env,
    `⚠️ CF Worker GITHUB_TOKEN 만료 임박\n\n` +
      `남은 일수: ${dayLabel}일 (${expiryDate.toISOString().slice(0, 16).replace("T", " ")} UTC)\n\n` +
      `갱신 절차:\n` +
      `1. github.com/settings/personal-access-tokens 의 토큰 → Regenerate\n` +
      `   (권한 그대로: Contents:rw + Actions:rw)\n` +
      `2. \`cd cloudflare-worker && npx wrangler secret put GITHUB_TOKEN\``,
  );
  console.log(`[${new Date().toISOString()}] PAT expiry alert: ${dayLabel}d left`);
}

// WEEKLY SUMMARY: every Sunday 21:00 KST, send a digest of the last 7
// days' worth of Actions runs grouped by event and the current state
// snapshot. Doubles as a "system is alive" signal — silence on Sunday
// evening means the bridge died.
async function weeklySummary(env) {
  const sinceIso = new Date(Date.now() - 7 * 86_400_000)
    .toISOString().slice(0, 10);

  // Fetch up to 100 most recent runs filtered by created date.  GitHub
  // tops out at 100 per page; for a 30-min cadence we get max 7*48=336
  // runs/week, but the dominant case (with the user's poll-interval
  // throttle and skip filter) is around 50-150, well within one page.
  let runs = [];
  try {
    const url = `https://api.github.com/repos/${env.GITHUB_REPO}/actions/runs?per_page=100&created=%3E%3D${sinceIso}`;
    const res = await fetch(url, { headers: ghHeaders(env) });
    if (res.ok) {
      const data = await res.json();
      runs = data.workflow_runs || [];
    }
  } catch (e) {
    console.error(`weekly: runs fetch failed: ${e.message}`);
  }

  const byEvent = {};
  let success = 0, failure = 0, other = 0;
  for (const r of runs) {
    byEvent[r.event] = (byEvent[r.event] || 0) + 1;
    if (r.conclusion === "success") success++;
    else if (r.conclusion === "failure") failure++;
    else other++;
  }

  // State snapshot for watch count
  let watchCount = 0;
  let totalNotified = 0;
  const state = await readStateKV(env);
  if (state) {
    const watches = state.watches || {};
    watchCount = Object.keys(watches).length;
    for (const w of Object.values(watches)) {
      totalNotified += (w.notified_train_ids || []).length;
    }
  }

  const eventLabel = { schedule: "cron(GHA)", workflow_dispatch: "수동", repository_dispatch: "CF cron", push: "config 변경" };
  const eventLines = Object.entries(byEvent)
    .sort((a, b) => b[1] - a[1])
    .map(([evt, n]) => `  ${eventLabel[evt] || evt}: ${n}회`);

  const lines = [
    "📊 주간 요약 (지난 7일)",
    "",
    `Actions 총 실행: ${runs.length}회`,
    `  성공: ${success}, 실패: ${failure}${other ? `, 진행 중/취소: ${other}` : ""}`,
  ];
  if (eventLines.length) {
    lines.push("", "이벤트별:", ...eventLines);
  }
  lines.push("", `활성 워치: ${watchCount}건`);
  if (totalNotified > 0) {
    lines.push(`누적 알림 좌석 ID: ${totalNotified}건`);
  }
  lines.push("", "이상 동작 없음 = 시스템 정상.");

  await sendTelegram(env, lines.join("\n"));
  console.log(`[${new Date().toISOString()}] weekly summary sent (${runs.length} runs, ${watchCount} watches)`);
}

// Shared by the cron handlers: state lives in KV, never in the repo.
async function readStateKV(env) {
  const stored = await env.STATE.get(STATE_KEY);
  if (!stored) return null;
  try {
    return JSON.parse(stored);
  } catch (e) {
    console.error(`state KV parse error: ${e.message}`);
    return null;
  }
}

function ghHeaders(env) {
  return {
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "ktx-srt-watcher-cf-bridge",
  };
}

// CORS goes on *every* response, including 401/404. Without it the browser
// discards the response before the PWA can read the status, so "no config
// stored yet" and "wrong token" both surface as an opaque network error —
// which is exactly how a correct app token got reported as invalid.
// Opened from a phone by tapping a notification, so it gets a real page
// rather than a bare string.
function html(body, status = 200) {
  return new Response(
    `<!doctype html><meta charset="utf-8">` +
    `<meta name="viewport" content="width=device-width,initial-scale=1">` +
    `<style>body{font:16px/1.6 -apple-system,system-ui,sans-serif;margin:0;` +
    `min-height:100dvh;display:flex;flex-direction:column;justify-content:center;` +
    `align-items:center;text-align:center;padding:32px;color:#191F28;background:#F2F4F6}` +
    `h1{font-size:20px;margin:0 0 8px}p{margin:0;color:#6B7684;font-size:14px}</style>` +
    body,
    { status, headers: { "Content-Type": "text/html; charset=utf-8", ...CORS } },
  );
}

function text(body, status = 200, extraHeaders) {
  return new Response(body, {
    status,
    headers: { "Content-Type": "text/plain; charset=utf-8", ...CORS, ...extraHeaders },
  });
}
