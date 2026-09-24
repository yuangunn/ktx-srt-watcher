"""Round 2: bake-off of 코레일+ client candidates, on the runner, one log.

Public log discipline: counts, train-type names, method signatures and
license names only. Generic probe routes (서울→부산, 수서→부산); the user's
config is never read.

Candidates:
  kma       yakisoba0728/korail-mobile-api — unlicensed, unpackaged source
            drop; imported via sys.path, never redistributed
  pykorail  devgyurak/pykorail
  letskorail bsangmin/letskorail
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta

PROBE = os.environ.get("PROBE") == "1"
UID, PW = os.environ.get("KORAIL_ID"), os.environ.get("KORAIL_PW")
DATE = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")
ROUTES = (("서울", "부산"), ("수서", "부산"))


def sh(cmd: str) -> None:
    print(f"$ {cmd}", flush=True)
    subprocess.run(cmd, shell=True, text=True)


def clone(repo: str, dest: str) -> None:
    subprocess.run(["git", "clone", "--depth", "1", f"https://github.com/{repo}", dest],
                   check=True, capture_output=True, text=True)
    head = subprocess.run(["git", "-C", dest, "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    print(f"== {repo} @ {head}")


def section(title: str) -> None:
    print(f"\n{'='*20} {title} {'='*20}", flush=True)


# ---------- korail-mobile-api: learn its surface, then try it ----------
section("korail-mobile-api: API surface")
clone("yakisoba0728/korail-mobile-api", "/tmp/kma")
sh("ls /tmp/kma/checks/")
sh("grep -n 'def \\|^class ' /tmp/kma/src/korail_mobile_api/client.py | head -80")
sh("grep -n 'def \\|^class ' /tmp/kma/src/korail_mobile_api/session.py | head -30")
print("== a checks/ script for usage reference (first 120 lines of the most probe-like) ==")
sh("for f in /tmp/kma/checks/*; do echo \"--- $f\"; done")
sh("sed -n 1,120p $(ls /tmp/kma/checks/* | head -1)")

if PROBE and UID and PW:
    section("korail-mobile-api: live")
    sys.path.insert(0, "/tmp/kma/src")
    try:
        import korail_mobile_api as kma
        names = [n for n in dir(kma) if not n.startswith("_")]
        print("exports:", ", ".join(sorted(names)))
        client_cls = None
        for cand in ("KorailClient", "Client", "Korail", "KorailMobileClient"):
            client_cls = getattr(kma, cand, None)
            if client_cls:
                print("client class:", cand)
                break
        if client_cls:
            try:
                c = client_cls()
                for login_name in ("login",):
                    fn = getattr(c, login_name, None)
                    if fn:
                        fn(UID, PW)
                        print("login OK")
                        break
                for meth in ("search_train", "search_trains", "search"):
                    s = getattr(c, meth, None)
                    if s:
                        for dep, arr in ROUTES:
                            try:
                                trains = s(dep, arr, DATE, "060000")
                                kinds = sorted({str(getattr(t, "train_type_name",
                                                getattr(t, "train_name", "?"))) for t in trains})
                                print(f"{meth} {dep}→{arr}: {len(trains)} trains, types={kinds}")
                            except Exception as e:
                                print(f"{meth} {dep}→{arr} FAILED: {type(e).__name__}: {e}")
                        break
            except Exception as e:
                print(f"kma live FAILED: {type(e).__name__}: {e}")
    except Exception as e:
        print(f"kma import FAILED: {type(e).__name__}: {e}")

# ---------- alternatives: license + packaging + live ----------
for repo, mod in (("devgyurak/pykorail", "pykorail"), ("bsangmin/letskorail", "letskorail")):
    section(repo)
    dest = f"/tmp/{mod}"
    try:
        clone(repo, dest)
    except Exception as e:
        print("clone FAILED:", e)
        continue
    sh(f"ls {dest}")
    sh(f"find {dest} -maxdepth 2 -iname 'license*' -exec sh -c 'echo {{}}; head -3 {{}}' \;")
    sh(f"find {dest} -maxdepth 2 -name 'pyproject.toml' -o -maxdepth 2 -name 'setup.py' | head")
    sh(f"sed -n 1,60p {dest}/README.md")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", dest], text=True)
    print("INSTALL", "OK" if r.returncode == 0 else "FAILED")
