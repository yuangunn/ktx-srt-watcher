"""Manual live diagnostic for the pykorail-based adapter.

Dispatch the api-probe workflow (run_probe=true) to run this on a runner with
the real KORAIL secrets. Public log discipline: generic probe routes only
(서울→부산, 수서→부산, tomorrow), counts and train-type names only; the
user's config is never read and the id is never printed.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Same install as the watcher: pykorail is gone from PyPI, so it comes from
# the vendored wheel that requirements.txt points at (paths resolve from root).
ROOT = Path(__file__).resolve().parent.parent
subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "-r", "requirements.txt"],
               check=True, cwd=ROOT)

from pykorail import Korail, LoginFailedError, NoResultsError  # noqa: E402
from pykorail import __version__ as PYKORAIL_VERSION  # noqa: E402
from pykorail import constants as pk_constants  # noqa: E402
from pykorail_wheel import patch_loaded_app_versions  # noqa: E402  (scripts/ is on sys.path)

# -8204 triage: try candidate app versions in memory, without building a
# wheel. Dispatch with try_app_version / try_api_version; if the searches then
# succeed, bake the same values in (docs/PYKORAIL_MAINTENANCE.md).
TRIAL = {const: os.environ.get(env, "").strip()
         for const, env in (("APP_DISPLAY_VERSION", "TRY_APP_DISPLAY_VERSION"),
                            ("APP_VERSION", "TRY_APP_VERSION"))}
TRIAL = {k: v for k, v in TRIAL.items() if v}
if TRIAL:
    try:
        patch_loaded_app_versions(TRIAL)
    except ValueError as e:
        print(f"trial app version rejected: {e}")
        sys.exit(1)

print(f"python {sys.version.split()[0]} | pykorail {PYKORAIL_VERSION} | "
      f"app {pk_constants.APP_DISPLAY_VERSION} / Version {pk_constants.APP_VERSION}"
      f"{' (TRIAL, in memory only)' if TRIAL else ''}")

UID, PW = os.environ.get("KORAIL_ID", ""), os.environ.get("KORAIL_PW", "")
if os.environ.get("PROBE") != "1" or not UID or not PW:
    print("probe skipped (PROBE!=1 or credentials absent)")
    sys.exit(0)

print(f"id shape: has_at={'@' in UID} all_digits={UID.isdigit()} has_hyphen={'-' in UID}")
try:
    korail = Korail.logged_in(UID, PW)
except LoginFailedError as e:
    # pykorail's hyphenless-phone guard embeds the number in its message, so
    # never print the exception text of a login failure verbatim unless it is
    # the server's generic rejection.
    msg = str(e)
    print("login FAILED:",
          msg if "올바르지 않습니다" in msg or "필요합니다" in msg else f"({type(e).__name__}, 상세 생략)")
    sys.exit(0)
except Exception as e:
    print(f"login error: {type(e).__name__}")
    sys.exit(0)

with korail:
    print("login OK")
    tomorrow = (datetime.now() + timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
    for dep, arr in (("서울", "부산"), ("수서", "부산")):
        try:
            trains = korail.trains.search(dep, arr, depart_after=tomorrow, include_no_seats=True)
            kinds = sorted({t.train_type_name for t in trains})
            n_seat = sum(1 for t in trains if t.has_seat())
            print(f"search {dep}→{arr}: {len(trains)} trains ({n_seat} with seats), types={kinds}")
        except NoResultsError:
            print(f"search {dep}→{arr}: no trains match")
        except Exception as e:
            print(f"search {dep}→{arr} FAILED: {type(e).__name__}: {e}")
    try:
        print("reservations:", len(korail.reservations.all()), "| tickets:", len(korail.tickets.all()))
    except Exception as e:
        print(f"reservations/tickets FAILED: {type(e).__name__}: {e}")

# The same search through the watcher's own adapter, so a library bump is
# checked against our field mapping and filters too, not just pykorail's API.
# A generic probe watch (never the user's config); counts only.
sys.path.insert(0, str(ROOT))
from worker.adapters.korail import KorailProvider  # noqa: E402
from worker.models import Watch  # noqa: E402

probe_watch = Watch.model_validate({
    "id": "probe", "provider": "korail", "from": "수서", "to": "부산",
    "date": tomorrow.strftime("%Y-%m-%d"), "time_min": "06:00", "time_max": "23:59",
    "train_types": ["KTX", "KTX-산천"],
})
try:
    provider = KorailProvider()
    provider.login(UID, PW)
    found = provider.search(probe_watch)
    print(f"adapter search 수서→부산: {len(found)} trains with seats, "
          f"types={sorted({t.train_type for t in found})}")
except Exception as e:
    print(f"adapter search FAILED: {type(e).__name__}: {e}")
