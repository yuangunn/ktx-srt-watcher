"""One-off: does the 코레일+ login endpoint process logins from a GH runner?

Uses a FABRICATED account only — no real credential is read or sent, so no
account can be locked. If the server answers the same generic rejection it
answers a wrong password with, the endpoint accepts logins from this IP and
geo-blocking is ruled out as the cause of the real account's rejection.
"""
from __future__ import annotations

import subprocess
import sys
import time

subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "pykorail"], check=True)

from pykorail import Korail, LoginFailedError  # noqa: E402

FAKE_ID = "definitely-not-a-real-account-x9q@example.com"
FAKE_PW = "wrong-password-123!"

for attempt in (1, 2):
    try:
        t0 = time.monotonic()
        Korail.logged_in(FAKE_ID, FAKE_PW)
        print("?? fake login SUCCEEDED — should be impossible")
        break
    except LoginFailedError as e:
        print(f"attempt {attempt}: LoginFailedError in {time.monotonic()-t0:.1f}s: {e}")
        break
    except Exception as e:
        print(f"attempt {attempt}: {type(e).__name__} in {time.monotonic()-t0:.1f}s: {e}")
        time.sleep(5)
