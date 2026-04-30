/**
 * KTX/SRT Watcher · Cloudflare Worker cron bridge.
 *
 * GitHub Actions throttles scheduled workflows on private free-tier repos
 * to ~30–60 min intervals (we measured ~48 min for `*/10 * * * *`).  This
 * Worker runs on Cloudflare's reliable cron and fires a
 * `repository_dispatch` event at GitHub, which immediately starts the
 * `watch.yml` workflow with no throttling.
 *
 * - scheduled handler   → fires on the cron in wrangler.toml
 * - fetch handler       → small HTTP surface for /health and /dispatch
 *
 * Required env (see wrangler.toml):
 *   GITHUB_REPO  e.g. "yuangunn/ktx-srt-watcher"
 *   EVENT_TYPE   e.g. "cron-tick"
 *   GITHUB_TOKEN (secret) PAT with Contents:rw + Actions:rw on the repo
 */

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(dispatch(env, "cron"));
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/health") {
      return text("OK · ktx-srt-watcher cron bridge\n");
    }
    if (url.pathname === "/dispatch" && request.method === "POST") {
      try {
        await dispatch(env, "manual");
        return text("dispatched\n", 202);
      } catch (e) {
        return text(`error: ${e.message}\n`, 500);
      }
    }
    return text("ktx-srt-watcher cron bridge\n");
  },
};

async function dispatch(env, source) {
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
  console.log(
    `[${new Date().toISOString()}] dispatched ${env.EVENT_TYPE} (source=${source})`,
  );
}

function text(body, status = 200) {
  return new Response(body, {
    status,
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
}
