"""The vendored copy must stay a copy.

worker/vendor/korail2 exists because the fork we used to install from was
deleted mid-flight and took every poll down with it. Vendoring only helps if
"is this still upstream 0.4.0?" has an answer, so pin the hashes: an
accidental edit fails here instead of quietly turning the copy into a private
fork nobody knows they are maintaining.

Patching it deliberately is fine — update these hashes in the same commit and
say what changed in worker/vendor/README.md.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

VENDOR = Path(__file__).resolve().parent.parent / "worker" / "vendor" / "korail2"

# sha256 of the files in the PyPI sdist korail2-0.4.0.tar.gz, which is itself
# byte-identical to carpedm20/korail2 master (diff is empty).
UPSTREAM_SHA256 = {
    "__init__.py": "9f4f0e98bd10fb629f817ac21056f10063213c5115efa66a778beb8dbfe06aba",
    "constants.py": "f989bc60ab44f69b962a963bd32b49ce081d61b6e97cc60fe9bdfc1cbf1e889b",
    "korail2.py": "2601824ef45cb29404484de5937b9da479f1bf368e36e7ec4e563ff8f46d120a",
}


@pytest.mark.parametrize("name", sorted(UPSTREAM_SHA256))
def test_file_matches_upstream(name):
    digest = hashlib.sha256((VENDOR / name).read_bytes()).hexdigest()
    assert digest == UPSTREAM_SHA256[name], (
        f"{name} no longer matches korail2 0.4.0. If that is deliberate, update "
        f"UPSTREAM_SHA256 and worker/vendor/README.md in the same commit."
    )


def test_license_is_kept():
    # BSD 3-Clause requires the copyright notice travel with the source.
    text = (VENDOR / "LICENSE").read_text(encoding="utf-8")
    assert "Copyright (c) 2014 by Taehoon Kim" in text
    assert "Redistributions of source code must retain" in text


def test_adapter_imports_the_vendored_copy_not_a_site_package():
    from worker.adapters import korail

    assert Path(korail.Korail.__module__.replace(".", "/")).name == "korail2"
    assert korail.Korail.__module__.startswith("worker.vendor.korail2")
