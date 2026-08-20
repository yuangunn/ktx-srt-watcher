# Vendored third-party source

## Why this directory exists

`requirements.txt` used to install korail2 straight from a personal GitHub
fork. On 2026-08-20 that repository was deleted, and every poll started dying
at `pip install` — git asks for credentials when a repo 404s, a runner cannot
answer, so the watcher never even started. The pinned commit was fork-only, so
whatever it changed is gone for good.

Moving to PyPI fixed the outage. Vendoring fixes the underlying exposure: the
one library this project cannot run without is now in the repo, not fetched
from a third party at install time.

korail2 is also dormant — last release 2024-03-08, no commits upstream since.
If Korail changes its API, nobody upstream is going to fix it. Having the
source here is what makes fixing it ourselves possible.

## What is NOT vendored, and why

Everything else stays on PyPI. The risk that bit us was a **personal fork on
GitHub**, not packages in general:

| Package | Kept because |
|---|---|
| `SRTrain` | Actively maintained — 61 releases, latest 2025-01. Vendoring would mean giving up upstream fixes for a library that still gets them. |
| `requests`, `pydantic`, `pycryptodome`, `pytest` | Mainstream, many maintainers. Vendoring these buys nothing and costs security updates. |

`pycryptodome` in particular cannot be dropped: korail2 uses AES to encrypt the
password at login.

## korail2

- **Upstream**: https://github.com/carpedm20/korail2 (`carpedm20/korail2`)
- **Version**: 0.4.0, from the PyPI sdist `korail2-0.4.0.tar.gz`
- **License**: BSD 3-Clause — see `korail2/LICENSE`. Copyright (c) 2014 Taehoon Kim.
- **Modifications**: none. The files are byte-identical to the sdist.

Upstream master and the 0.4.0 sdist are themselves identical (`diff` is empty),
so this is also the last public state of the library.

Verify the copy has not drifted:

```sh
sha256sum worker/vendor/korail2/{__init__,constants,korail2}.py
```

```
9f4f0e98bd10fb629f817ac21056f10063213c5115efa66a778beb8dbfe06aba  __init__.py
f989bc60ab44f69b962a963bd32b49ce081d61b6e97cc60fe9bdfc1cbf1e889b  constants.py
2601824ef45cb29404484de5937b9da479f1bf368e36e7ec4e563ff8f46d120a  korail2.py
```

`tests/test_vendor.py` checks these on every CI run, so an accidental edit
fails the build rather than quietly becoming a local fork. Deliberate patches
are fine — update the hashes in the same commit and record what changed under
**Modifications** above, so "have we diverged from upstream" stays answerable.
