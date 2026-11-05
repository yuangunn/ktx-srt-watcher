"""The vendored pykorail wheel and the tool that patches it.

pykorail has no upstream any more (deleted from PyPI and GitHub 2026-11-02),
so when KORAIL changes something the fix is a patched wheel built by
scripts/pykorail_wheel.py. These tests keep that path working before it is
needed in a hurry. Procedure: docs/PYKORAIL_MAINTENANCE.md.
"""
from __future__ import annotations

import importlib
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import pykorail_wheel as pw  # noqa: E402

_APP_MODULES = [
    "pykorail.constants", "pykorail.client",
    "pykorail._sync.api", "pykorail._sync.client", "pykorail._sync.resources.trains",
    "pykorail._async.api", "pykorail._async.client", "pykorail._async.resources.trains",
]


def _contents(wheel: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(wheel) as z:
        return {n: z.read(n) for n in z.namelist() if not n.endswith("/RECORD")}


@pytest.fixture
def src(tmp_path: Path) -> Path:
    return pw.unpack(pw.current_wheel(), tmp_path / "src")


class TestVendoredWheel:
    def test_requirements_points_at_a_vendored_wheel(self):
        assert pw.current_wheel().is_file()

    def test_record_matches_contents(self):
        # pip refuses a wheel whose RECORD disagrees with its files.
        assert pw.verify(pw.current_wheel()) == []


class TestRepack:
    def test_round_trip_keeps_every_file_byte_for_byte(self, src, tmp_path):
        original = pw.current_wheel()
        version = original.name.split("-")[1]
        rebuilt = pw.pack(src, version, tmp_path)
        assert _contents(rebuilt) == _contents(original)
        assert pw.verify(rebuilt) == []

    def test_pack_is_deterministic(self, src, tmp_path):
        # Same tree, same bytes: a patched wheel's sha256 can be reproduced.
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        a = pw.pack(src, "0.6.1+ktx.1", tmp_path / "a")
        b = pw.pack(src, "0.6.1+ktx.1", tmp_path / "b")
        assert a.read_bytes() == b.read_bytes()

    def test_local_version_is_stamped_everywhere(self, src, tmp_path):
        wheel = pw.pack(src, "0.6.1+ktx.1", tmp_path)
        assert wheel.name == "pykorail-0.6.1+ktx.1-py3-none-any.whl"
        files = _contents(wheel)
        assert "Version: 0.6.1+ktx.1" in files["pykorail-0.6.1+ktx.1.dist-info/METADATA"].decode()
        assert "'0.6.1+ktx.1'" in files["pykorail/_version.py"].decode()
        assert not any(n.startswith("pykorail-0.6.1.dist-info/") for n in files)
        assert pw.verify(wheel) == []

    def test_editor_leftovers_do_not_ship(self, src, tmp_path):
        (src / "pykorail" / "constants.py.orig").write_text("x")
        (src / "pykorail" / "constants.py~").write_text("x")
        wheel = pw.pack(src, "0.6.1+ktx.1", tmp_path)
        assert not any(n.endswith((".orig", "~")) for n in _contents(wheel))

    def test_rejects_non_pep440_version(self, src, tmp_path):
        with pytest.raises(ValueError):
            pw.pack(src, "0.6.1-ktx", tmp_path)

    def test_unpack_refuses_a_non_empty_folder(self, src):
        with pytest.raises(ValueError):
            pw.unpack(pw.current_wheel(), src)


class TestBumpApp:
    def test_rewrites_both_constants(self, src):
        pw.bump_app(src, "7.2.0", "261201001")
        text = (src / "pykorail" / "constants.py").read_text(encoding="utf-8")
        assert 'APP_DISPLAY_VERSION: Final = "7.2.0"' in text
        assert 'APP_VERSION: Final = "261201001"' in text

    def test_one_field_leaves_the_other(self, src):
        def line(const: str) -> str:
            text = (src / "pykorail" / "constants.py").read_text(encoding="utf-8")
            [found] = [ln for ln in text.splitlines() if ln.startswith(f"{const}: Final")]
            return found

        display_before = line("APP_DISPLAY_VERSION")
        assert pw.bump_app(src, None, "261201001") == ["APP_VERSION = 261201001"]
        assert line("APP_DISPLAY_VERSION") == display_before
        assert line("APP_VERSION") == 'APP_VERSION: Final = "261201001"'

    @pytest.mark.parametrize("display,api", [("7.2", None), (None, "26120100"), ('7.2.0"', None)])
    def test_rejects_malformed_values(self, src, display, api):
        with pytest.raises(ValueError):
            pw.bump_app(src, display, api)


class TestPatchLoaded:
    @pytest.fixture
    def restore(self, monkeypatch):
        # Register every rebinding with monkeypatch so the real values come
        # back for the rest of the suite.
        for name in _APP_MODULES:
            mod = importlib.import_module(name)
            for const in pw.APP_FIELDS:
                if hasattr(mod, const):
                    monkeypatch.setattr(mod, const, getattr(mod, const))

    def test_reaches_every_request_builder(self, restore):
        touched = pw.patch_loaded_app_versions({"APP_DISPLAY_VERSION": "7.2.0", "APP_VERSION": "261201001"})
        assert set(_APP_MODULES) <= set(touched)
        from pykorail._sync.api import ApiClient
        payload = ApiClient.common_payload(None)  # reads module globals only
        assert (payload["AppVersion"], payload["Version"]) == ("7.2.0", "261201001")

    def test_bad_value_changes_nothing(self, restore):
        from pykorail import constants
        before = (constants.APP_DISPLAY_VERSION, constants.APP_VERSION)
        with pytest.raises(ValueError):
            pw.patch_loaded_app_versions({"APP_DISPLAY_VERSION": "7.2.0", "APP_VERSION": "nope"})
        assert (constants.APP_DISPLAY_VERSION, constants.APP_VERSION) == before
