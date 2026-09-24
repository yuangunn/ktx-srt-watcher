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

subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "pykorail"], check=True)

from pykorail import Korail, LoginFailedError, NoResultsError  # noqa: E402

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
