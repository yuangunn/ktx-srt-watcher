"""Deep login diagnostic — fabricated account only, so verbose is safe.

The user re-verified their credentials by typing them into the app, and the
server still answers "아이디 또는 비밀번호가 올바르지 않습니다" — so suspicion
moves to the request itself. Run the FAKE login with pykorail's verbose DEBUG
logging (requests/responses in full) on 0.2.0 and 0.1.2, to see the actual
exchange: key issuance, dynapath acceptance, and the login response envelope
(h_msg_cd). No real credential is read; everything printed derives from a
fabricated account.
"""
from __future__ import annotations

import logging
import subprocess
import sys

FAKE_ID = "definitely-not-a-real-account-x9q@example.com"
FAKE_PW = "wrong-password-123!"

logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s %(message)s")
for noisy in ("urllib3", "curl_cffi", "charset_normalizer"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

for version in ("0.2.0", "0.1.2"):
    print(f"\n{'='*20} pykorail=={version} {'='*20}", flush=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                    "--force-reinstall", f"pykorail=={version}"], check=True)
    # re-import cleanly per version
    for mod in [m for m in list(sys.modules) if m.startswith("pykorail")]:
        del sys.modules[mod]
    from pykorail import Korail  # noqa: E402
    try:
        k = Korail(verbose=True)
        k.login(FAKE_ID, FAKE_PW)
        print("?? fake login SUCCEEDED")
    except Exception as e:
        print(f"result: {type(e).__name__}: {e}", flush=True)
