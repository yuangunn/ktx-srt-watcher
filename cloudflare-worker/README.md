# KTX/SRT Watcher · Cloudflare Worker Cron Bridge

Replaces the unreliable GHA-throttled cron with a precise CF cron that fires
GitHub `repository_dispatch` events to wake the watcher workflow.

**Why**: GHA scheduled workflows on free-tier private repos throttle to
~30–60 min between runs (we observed ~48 min for `*/10 * * * *`).  CF cron
triggers fire to-the-second.

**Cost**: free.  At `*/5 * * * *` the worker invokes 288 times/day, well
under the 100k/day free request quota.

## One-time setup

```bash
cd cloudflare-worker
npm install
npx wrangler login          # opens browser, log in to your CF account
```

### Issue a fine-grained PAT (separate from the PWA token)

[github.com/settings/personal-access-tokens/new](https://github.com/settings/personal-access-tokens/new)

| Field | Value |
|---|---|
| Token name | `ktx-srt-watcher-cf-bridge` |
| Expiration | 1 year |
| Repository access | Only select repositories → `ktx-srt-watcher` |
| Permissions → Repository | **Contents: Read and write** + **Actions: Read and write** |

Copy the `github_pat_…` and store as a Worker secret (paste when prompted):

```bash
npx wrangler secret put GITHUB_TOKEN
```

## Deploy

```bash
npx wrangler deploy
```

Worker URL appears in the output (e.g. `https://ktx-srt-watcher-cron.<account>.workers.dev`).
The cron starts firing on the next 5-min boundary.

## Verify

```bash
npx wrangler tail               # live logs from the deployed worker
```

Or trigger manually:

```bash
curl -X POST https://ktx-srt-watcher-cron.<account>.workers.dev/dispatch
```

Then check GitHub Actions: a `repository_dispatch` run with type `cron-tick`
should appear within seconds.

## Adjust cron interval

Edit `wrangler.toml`:

```toml
[triggers]
crons = ["*/5 * * * *"]   # change to "*/3", "*/10", etc.
```

Then `npx wrangler deploy`.  Avoid going below `*/3` — Korail/SR anti-bot
checks may flag too-frequent polls.

## Rotate / revoke

```bash
# Revoke at GitHub: Settings → Developer settings → Personal access tokens → revoke
# Replace the secret:
npx wrangler secret put GITHUB_TOKEN
# Or undeploy entirely:
npx wrangler delete
```

After undeploy, the GHA `schedule:` trigger still exists as a
~hourly fallback in `.github/workflows/watch.yml`.
