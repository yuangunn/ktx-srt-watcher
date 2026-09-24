"""Diagnostic for the 코레일+ migration: inspect and live-test korail-mobile-api.

Runs on the Actions runner (the sandbox cannot clone external repos). Prints
into a PUBLIC log, so the live probe reports counts and train-type names only
and never touches the user's config — no route or date of theirs can leak.

Stages:
  1. clone + print ground truth (packaging, license, README) — always
  2. pip install + import — always
  3. live login + generic searches (서울→부산, 수서→부산) — only with
     PROBE=1 and KORAIL_ID/KORAIL_PW in the environment
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta

REPO = "https://github.com/yakisoba0728/korail-mobile-api"
DEST = "/tmp/kma"


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, text=True, **kw)


def stage1_inspect() -> None:
    sh(["git", "clone", "--depth", "1", REPO, DEST], check=True)
    head = subprocess.run(["git", "-C", DEST, "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    print(f"== HEAD == {head}")
    sh(["ls", "-la", DEST])
    print("== packaging/license files ==")
    sh(["find", DEST, "-maxdepth", "3", "-iname", "pyproject.toml", "-o",
        "-maxdepth", "3", "-iname", "setup.*", "-o", "-iname", "license*"])
    print("== README (first 250 lines) ==")
    sh(["sed", "-n", "1,250p", f"{DEST}/README.md"])
    for p in ("pyproject.toml", "src/pyproject.toml", "setup.py"):
        if os.path.exists(f"{DEST}/{p}"):
            print(f"== {p} =="); sh(["cat", f"{DEST}/{p}"])


def stage2_install() -> bool:
    r = sh([sys.executable, "-m", "pip", "install", "--quiet", DEST])
    print("INSTALL", "OK" if r.returncode == 0 else "FAILED")
    if r.returncode != 0:
        return False
    try:
        import korail_mobile_api as k  # noqa
        print("IMPORT OK, version:", getattr(k, "__version__", "?"))
        print("top-level:", ", ".join(sorted(n for n in dir(k) if not n.startswith("_"))[:80]))
        return True
    except Exception as e:
        print("IMPORT FAILED:", e)
        return False


def stage3_probe() -> None:
    uid, pw = os.environ.get("KORAIL_ID"), os.environ.get("KORAIL_PW")
    if os.environ.get("PROBE") != "1" or not uid or not pw:
        print("probe: skipped (PROBE!=1 or credentials absent)")
        return
    import korail_mobile_api as k
    print("probe: client symbols:", [n for n in dir(k) if "lient" in n or "orail" in n])
    # The client API is learned from stage 1's README dump; this block is
    # updated once that is known. Guarded so a wrong guess prints, not raises.
    try:
        client = k.KorailClient(uid, pw)  # best guess; refine after stage 1
        print("login OK")
        date = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")
        for dep, arr in (("서울", "부산"), ("수서", "부산")):
            trains = client.search_train(dep=dep, arr=arr, date=date, time="060000")
            types = sorted({getattr(t, "train_type_name", getattr(t, "train_name", "?")) for t in trains})
            print(f"search {dep}→{arr} {date}: {len(trains)} trains, types={types}")
    except Exception as e:
        print(f"probe FAILED at: {type(e).__name__}: {e}")


if __name__ == "__main__":
    stage1_inspect()
    if stage2_install():
        stage3_probe()
