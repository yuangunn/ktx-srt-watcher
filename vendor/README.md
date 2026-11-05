# vendor/

## pykorail-0.6.1-py3-none-any.whl

The 코레일+ client the watcher is built on (`worker/adapters/korail.py`).
Requires Python 3.11+.

**Why it lives here.** The `pykorail` project was deleted from PyPI on
2026-11-02 around 19:05 KST (`https://pypi.org/simple/pykorail/` → 404), and
the upstream repository (`devgyurak/pykorail`) went with it. No reason was
published, and no malware report exists for it (OSV). The home runner
reinstalls dependencies on every poll, so from that tick on every run failed
at `pip install`. `requirements.txt` now installs this file by path: the
install no longer depends on PyPI, and a stranger re-registering the freed
name on PyPI cannot get a build installed here.

**Why 0.6.1 and not the 0.2.2 we ran before.** KORAIL tightened the app's
anti-bot checks on 2026-10-21 (app 7.0.10). Since then 0.2.2 logs in but every
search is refused with `-8204` ("최신 버전으로 업데이트해 주세요"). Verified
on `korail-home` with `api-probe` on 2026-11-02: 0.2.2 → both probe searches
`-8204`; 0.6.1 → login OK, 서울→부산 and 수서→부산 both return trains. 0.6.0
(2026-10-28) rebuilt the DynaPath token for app 7.0.10; 0.6.1 was published
2026-11-02 03:44 UTC, hours before the project was deleted.

**Device identity.** Since 0.6 the `android_id` is embedded in the anti-bot
token. `pykorail.device.profile_by_id(id)` mints a random one unless the saved
one is passed back, so the adapter persists both `korail_device_profile` and
`korail_android_id` in state. Without that, every poll would look like a new
phone.

**License — not plain MIT any more.** Releases after 0.3.0 ship the
"pykorail License": MIT terms plus a *Commercial Gain Restriction*. No reselling
tickets above face value, no fees/subscriptions/ad revenue for booking
services built on it, no selling the software. Expressly allowed: booking for
your own travel, booking for others at face value without a fee, and
non-commercial personal projects. That covers this watcher; it does not cover
running it as a paid service for others. The full text is in the wheel at
`pykorail-0.6.1.dist-info/licenses/LICENSE` and must travel with any copy.

**Provenance.** This is the exact file PyPI served, recovered from PyPI
mirrors that had not yet dropped it:

| | |
|---|---|
| sha256 | `5d1055a41d19061682b541dbc07fe11b764c9cad28321e8e5014c249769c61b8` |
| blake2b-256 | `56b53bc07459a866861584a2e3e22ce12f81bd3a2a438edfd99d1f9c2fe0007b` |
| Index sha256 agrees | `mirrors.aliyun.com`, `mirrors.cloud.tencent.com`, `pypi.doubanio.com` |
| Downloaded from | Aliyun and Tencent (byte-identical) |

The blake2b matches PyPI's storage path
`packages/56/b5/3bc07459…/pykorail-0.6.1-py3-none-any.whl`. The wheel was
scanned before vendoring: it only talks to `smart.letskorail.com` and
`nf.letskorail.com`, and has no subprocess/eval/exec/env access.
Check with `sha256sum vendor/pykorail-0.6.1-py3-none-any.whl`.

**Replacing or patching it.** There is no upstream to upgrade from, and
Dependabot cannot see this pin. If 코레일+ changes again (a "감시 중단" alert,
or `-8204` in an `api-probe` run), the fix is made here: unpack this wheel,
patch it, repack it as `0.6.1+ktx.N` with `scripts/pykorail_wheel.py`, and
dispatch `api-probe` on `korail-home` from the branch before merging.
Symptom table and step-by-step: [docs/PYKORAIL_MAINTENANCE.md](../docs/PYKORAIL_MAINTENANCE.md).
A newer build found elsewhere is verified the way this one was: the same
sha256 from more than one source, then a scan.

**Local patches.** None yet: the wheel above is the upstream file byte for
byte. Each patched build adds a row here (version, what changed, why, sha256)
and keeps the upstream sha256 above as the base it was built from.

| Version | Change | Why | sha256 |
|---|---|---|---|
| — | — | — | — |
