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

## Data store

`config.json` (watch list) and `state.json` (poll state) are **not** in this
repo.  Watch ids embed the route and travel date — `서울-대전-20260101-a1b2` —
so committing them to a public repo published when the user would be away.
Both live in the `STATE` KV namespace under the keys `config` and `current`,
and are served only against a bearer token:

| route | who | token |
|---|---|---|
| `GET /config` | PWA, watcher | `APP_TOKEN` or `REMINDER_TOKEN` |
| `PUT /config` | PWA only | `APP_TOKEN` |
| `GET /config/backups` | PWA | `APP_TOKEN` |
| `GET /state` | PWA, watcher | `APP_TOKEN` or `REMINDER_TOKEN` |
| `PUT /state` | watcher only | `REMINDER_TOKEN` |
| `GET /mode` | PWA, watcher | `APP_TOKEN` or `REMINDER_TOKEN` |
| `PUT /mode` | PWA, iOS Shortcut | `APP_TOKEN` |

`/mode` is `home` or `away` and decides whether an urgent alert may override
the phone's mute switch.  Only the phone knows where the user is, so it pushes
the value here; anything unreadable falls back to `home`, because a missed 3am
cancellation costs more than a stray alert.  `PUT` accepts `?mode=away` as well
as a JSON body — Shortcuts is much easier to set up against a bare query
string.

`PUT /config` keeps the previous 5 versions in a backup ring — KV has no
history of its own and this is now the only copy of the watch list.

Set the app token once:

```bash
npx wrangler secret put APP_TOKEN
```

Use the same value in the PWA's setup screen.

## Deploy

Normally you don't: pushing a change under `cloudflare-worker/` to `main`
triggers `.github/workflows/deploy-worker.yml`, which deploys and then checks
`/health`.  That workflow needs one repo secret, `CLOUDFLARE_API_TOKEN`
(Cloudflare → My Profile → API Tokens → "Edit Cloudflare Workers" template),
plus `CLOUDFLARE_ACCOUNT_ID` only if your login has several accounts.

To deploy by hand — from any machine, `wrangler login` is browser OAuth and
existing Worker secrets survive a redeploy:

```bash
npx wrangler login
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
