"""Round 3: pykorail live test — the one question left is whether it clears
the 코레일+ MACRO rejection with a real account.

Public log discipline: generic probe routes only (서울→부산, 수서→부산,
tomorrow), counts and type names only, user config never read. Also dumps
pykorail's API reference so the adapter rewrite needs no further rounds.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta

PROBE = os.environ.get("PROBE") == "1"
UID, PW = os.environ.get("KORAIL_ID"), os.environ.get("KORAIL_PW")


def sh(cmd: str) -> None:
    print(f"$ {cmd}", flush=True)
    subprocess.run(cmd, shell=True, text=True)


print("=" * 20, "pykorail: reference docs", "=" * 20)
subprocess.run(["git", "clone", "--depth", "1",
                "https://github.com/devgyurak/pykorail", "/tmp/pyk"],
               check=True, capture_output=True)
sh("git -C /tmp/pyk rev-parse HEAD")
sh("sed -n 1,260p /tmp/pyk/docs/reference.md")
sh("sed -n 60,200p /tmp/pyk/README.md")

print("=" * 20, "pykorail: install from PyPI", "=" * 20)
r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "pykorail"], text=True)
print("INSTALL", "OK" if r.returncode == 0 else "FAILED")
sh(f"{sys.executable} -m pip show pykorail | grep -E '^(Name|Version|License|Requires)'")

if not (PROBE and UID and PW):
    print("live probe skipped")
    sys.exit(0)

print("=" * 20, "pykorail: live", "=" * 20)
from pykorail import Korail  # noqa: E402

tomorrow = (datetime.now() + timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
try:
    with Korail.logged_in(UID, PW) as korail:
        print("login OK")
        for dep, arr in (("서울", "부산"), ("수서", "부산"), ("부산", "수서")):
            try:
                trains = korail.trains.search(dep, arr, depart_after=tomorrow)
                kinds: set[str] = set()
                for t in trains:
                    for attr in ("train_type_name", "train_type", "train_name", "category"):
                        v = getattr(t, attr, None)
                        if v:
                            kinds.add(str(v))
                            break
                print(f"search {dep}→{arr}: {len(trains)} trains, types={sorted(kinds)}")
                if trains:
                    t0 = trains[0]
                    print("  train attrs:", ", ".join(sorted(a for a in dir(t0) if not a.startswith('_'))))
            except Exception as e:
                print(f"search {dep}→{arr} FAILED: {type(e).__name__}: {e}")
        # Surfaces we need for parity with the old adapter:
        for name in ("reservations", "tickets", "reserve"):
            print(f"korail.{name}:", type(getattr(korail, name, None)).__name__)
except Exception as e:
    print(f"live FAILED: {type(e).__name__}: {e}")
