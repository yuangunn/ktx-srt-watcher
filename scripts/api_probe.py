"""Round 4: why does pykorail reject a login that korail2 accepts?

Hypothesis: id-shape auto-detection. pykorail classifies korail_id by shape
(email / phone 010-1234-5678 / membership number); a hyphen-less phone number
reads as a membership number and fails with "아이디 또는 비밀번호가 올바르지
않습니다" — while korail2's older endpoint may accept it.

Public log discipline: the id itself is never printed; only coarse shape
flags (has @, all digits) and which variant number succeeded.
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
    print("probe skipped"); sys.exit(0)

print(f"id shape: has_at={'@' in UID} all_digits={UID.isdigit()} has_hyphen={'-' in UID}")

variants: list[str] = [UID]
if UID.isdigit() and len(UID) == 11 and UID.startswith("01"):
    variants.append(f"{UID[:3]}-{UID[3:7]}-{UID[7:]}")  # phone with hyphens
if UID.isdigit() and len(UID) == 10:
    variants.append(f"{UID[:3]}-{UID[3:6]}-{UID[6:]}")  # 010-less legacy phone? unlikely

korail = None
for i, u in enumerate(variants):
    try:
        korail = Korail.logged_in(u, PW)
        print(f"login OK with variant {i} of {len(variants)}")
        break
    except LoginFailedError as e:
        print(f"variant {i} failed: {e}")
    except Exception as e:
        print(f"variant {i} error: {type(e).__name__}: {e}")

if korail is None:
    print("all login variants failed")
    sys.exit(0)

with korail:
    tomorrow = (datetime.now() + timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
    for dep, arr in (("서울", "부산"), ("수서", "부산"), ("부산", "동탄")):
        try:
            trains = korail.trains.search(dep, arr, depart_after=tomorrow, include_no_seats=True)
            kinds = sorted({t.train_type_name for t in trains})
            n_seat = sum(1 for t in trains if t.has_seat())
            print(f"search {dep}→{arr}: {len(trains)} trains ({n_seat} with seats), types={kinds}")
        except NoResultsError:
            print(f"search {dep}→{arr}: NoResultsError (no trains match)")
        except Exception as e:
            print(f"search {dep}→{arr} FAILED: {type(e).__name__}: {e}")
    try:
        print("reservations.all():", len(korail.reservations.all()), "holds")
        print("tickets.all():", len(korail.tickets.all()), "tickets")
    except Exception as e:
        print(f"reservations/tickets FAILED: {type(e).__name__}: {e}")
