"""Unpack, patch and repack the vendored pykorail wheel — standard library only.

pykorail is gone from PyPI and its upstream repository is gone too, so when
KORAIL changes something the fix has to be made here. A wheel is a zip of the
package plus a ``.dist-info`` folder whose RECORD lists a hash for every file;
this script keeps those consistent so pip installs the patched build cleanly.

    python scripts/pykorail_wheel.py unpack                  # → build/pykorail-src
    python scripts/pykorail_wheel.py bump-app --display 7.2.0 --api 261201001
    #   …or edit any file under build/pykorail-src by hand…
    python scripts/pykorail_wheel.py pack --version 0.6.1+ktx.1 --update-requirements
    python scripts/pykorail_wheel.py verify                  # RECORD ↔ contents

Patched builds carry a local version (``+ktx.N``) so they can never be mistaken
for an upstream release. Packing is deterministic (sorted entries, fixed
timestamps): the same tree always yields the same bytes and sha256.

Procedure, symptom table and limits: docs/PYKORAIL_MAINTENANCE.md.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"
VENDOR = ROOT / "vendor"
DEFAULT_SRC = ROOT / "build" / "pykorail-src"
NAME = "pykorail"
_REQ_LINE = re.compile(r"^\./vendor/(pykorail-[^/\s]+\.whl)\s*$", re.M)
_VERSION_OK = re.compile(r"^\d+(\.\d+)*(\+[A-Za-z0-9]+(\.[A-Za-z0-9]+)*)?$")
_FIXED_TIME = (2020, 2, 2, 0, 0, 0)  # what the upstream wheel uses


def current_wheel() -> Path:
    """The wheel requirements.txt installs."""
    m = _REQ_LINE.search(REQUIREMENTS.read_text(encoding="utf-8"))
    if m is None:
        raise ValueError("requirements.txt has no ./vendor/pykorail-*.whl line")
    return VENDOR / m.group(1)


def _record_hash(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _dist_info(names: list[str]) -> str:
    found = sorted({n.split("/", 1)[0] for n in names if n.split("/", 1)[0].endswith(".dist-info")})
    if len(found) != 1:
        raise ValueError(f"expected one .dist-info folder, found {found}")
    return found[0]


def verify(wheel: Path) -> list[str]:
    """Problems with the wheel's RECORD (empty list = consistent)."""
    with zipfile.ZipFile(wheel) as z:
        names = z.namelist()
        record_name = f"{_dist_info(names)}/RECORD"
        rows = [r.split(",") for r in z.read(record_name).decode("utf-8").splitlines() if r]
        listed = {r[0] for r in rows}
        problems = [f"not in RECORD: {n}" for n in names if n not in listed]
        for path, digest, size in rows:
            if path == record_name:
                continue
            if path not in names:
                problems.append(f"missing from wheel: {path}")
                continue
            data = z.read(path)
            if digest != _record_hash(data) or size != str(len(data)):
                problems.append(f"hash/size mismatch: {path}")
    return problems


def unpack(wheel: Path, dest: Path) -> Path:
    """Extract ``wheel`` into an empty ``dest``."""
    if dest.exists() and any(dest.iterdir()):
        raise ValueError(f"{dest} is not empty — remove it or pass --src elsewhere")
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel) as z:
        for name in z.namelist():
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError(f"refusing unsafe path in wheel: {name}")
        z.extractall(dest)
    return dest


def _set_version(src: Path, version: str) -> str:
    """Rename the dist-info folder and rewrite the version in METADATA and
    _version.py. Returns the new dist-info folder name."""
    old = _dist_info([p.name for p in src.iterdir()])
    new = f"{NAME}-{version}.dist-info"
    if old != new:
        (src / old).rename(src / new)
    meta = src / new / "METADATA"
    meta.write_text(re.sub(r"(?m)^Version: .*$", f"Version: {version}",
                           meta.read_text(encoding="utf-8"), count=1), encoding="utf-8")
    vfile = src / NAME / "_version.py"
    if vfile.exists():
        vfile.write_text(re.sub(r"(?m)^__version__ = version = .*$",
                                f"__version__ = version = {version!r}",
                                vfile.read_text(encoding="utf-8"), count=1), encoding="utf-8")
    return new


def _is_junk(filename: str) -> bool:
    """Editor/patch leftovers that must not ship (``constants.py.orig`` …)."""
    return (filename.startswith(".") or filename.endswith(("~", ".orig", ".rej", ".swp", ".pyc")))


def _members(src: Path, dist_info: str) -> list[str]:
    """Package files first, dist-info last, RECORD at the very end (wheel spec)."""
    files = [p.relative_to(src).as_posix() for p in src.rglob("*")
             if p.is_file() and "__pycache__" not in p.parts and not _is_junk(p.name)]
    record = f"{dist_info}/RECORD"
    body = sorted(f for f in files if not f.startswith(dist_info + "/"))
    meta = sorted(f for f in files if f.startswith(dist_info + "/") and f != record)
    return body + meta


def pack(src: Path, version: str, out_dir: Path) -> Path:
    """Build ``pykorail-<version>-py3-none-any.whl`` from ``src``."""
    if not _VERSION_OK.match(version):
        raise ValueError(f"not a PEP 440 version: {version!r}")
    dist_info = _set_version(src, version)
    out = out_dir / f"{NAME}-{version}-py3-none-any.whl"
    rows: list[str] = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in _members(src, dist_info):
            data = (src / name).read_bytes()
            _write(z, name, data)
            rows.append(f"{name},{_record_hash(data)},{len(data)}")
        rows.append(f"{dist_info}/RECORD,,")
        _write(z, f"{dist_info}/RECORD", ("\n".join(rows) + "\n").encode("utf-8"))
    return out


def _write(z: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=_FIXED_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    z.writestr(info, data)


#: The app identity KORAIL's version gate checks; stale values get -8204
#: ("최신 버전으로 업데이트해 주세요"). APP_DISPLAY_VERSION is sent as the
#: ``AppVersion`` form field (login and most calls), APP_VERSION as ``Version``
#: (every call, including search).
APP_FIELDS: dict[str, str] = {"APP_DISPLAY_VERSION": r"\d+\.\d+\.\d+", "APP_VERSION": r"\d{9}"}


def _check_app_values(values: dict[str, str]) -> None:
    for const, value in values.items():
        if const not in APP_FIELDS:
            raise ValueError(f"unknown app field {const!r}")
        if not re.fullmatch(APP_FIELDS[const], value):
            raise ValueError(f"{const} {value!r} does not look like {APP_FIELDS[const]}")


def bump_app(src: Path, display: str | None, api: str | None) -> list[str]:
    """Rewrite the app version constants in the unpacked source. Returns changes."""
    values = {k: v for k, v in (("APP_DISPLAY_VERSION", display), ("APP_VERSION", api)) if v}
    _check_app_values(values)
    path = src / NAME / "constants.py"
    text = path.read_text(encoding="utf-8")
    for const, value in values.items():
        text, n = re.subn(rf'(?m)^({const}: Final = )"[^"]*"', rf'\g<1>"{value}"', text)
        if n != 1:
            raise ValueError(f"{const} not found exactly once in constants.py")
    path.write_text(text, encoding="utf-8")
    return [f"{k} = {v}" for k, v in values.items()]


def patch_loaded_app_versions(values: dict[str, str]) -> list[str]:
    """In-memory bump-app, for trying candidate values in api-probe before
    building a wheel. pykorail imports the constants *by name* into each
    request builder, so every loaded pykorail module is rebound, not just
    ``pykorail.constants``. Returns the modules touched."""
    _check_app_values(values)
    touched: set[str] = set()
    for name, mod in list(sys.modules.items()):
        if mod is None or not (name == NAME or name.startswith(NAME + ".")):
            continue
        for const, value in values.items():
            if hasattr(mod, const):
                setattr(mod, const, value)
                touched.add(name)
    return sorted(touched)


def update_requirements(wheel: Path) -> None:
    text = REQUIREMENTS.read_text(encoding="utf-8")
    new, n = _REQ_LINE.subn(f"./vendor/{wheel.name}", text)
    if n != 1:
        raise ValueError("requirements.txt: pykorail line not found exactly once")
    REQUIREMENTS.write_text(new, encoding="utf-8")


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("unpack", help="extract the vendored wheel for editing")
    u.add_argument("--wheel", type=Path)
    u.add_argument("--src", type=Path, default=DEFAULT_SRC)
    b = sub.add_parser("bump-app", help="set APP_DISPLAY_VERSION / APP_VERSION")
    b.add_argument("--src", type=Path, default=DEFAULT_SRC)
    b.add_argument("--display", help="store version, e.g. 7.2.0")
    b.add_argument("--api", help="the 9-digit Version form field, e.g. 261201001")
    k = sub.add_parser("pack", help="build a wheel from the edited tree")
    k.add_argument("--src", type=Path, default=DEFAULT_SRC)
    k.add_argument("--version", required=True, help="e.g. 0.6.1+ktx.1")
    k.add_argument("--out", type=Path, default=VENDOR)
    k.add_argument("--update-requirements", action="store_true")
    v = sub.add_parser("verify", help="check a wheel's RECORD against its contents")
    v.add_argument("--wheel", type=Path)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _run(args)
    except ValueError as e:
        raise SystemExit(f"error: {e}") from None


def _run(args: argparse.Namespace) -> int:
    if args.cmd == "unpack":
        print(f"unpacked → {unpack(args.wheel or current_wheel(), args.src)}")
    elif args.cmd == "bump-app":
        print("\n".join(bump_app(args.src, args.display, args.api)) or "nothing to change")
    elif args.cmd == "pack":
        wheel = pack(args.src, args.version, args.out)
        if args.update_requirements:
            update_requirements(wheel)
        print(f"{wheel}\nsha256 {sha256_of(wheel)}")
    else:
        problems = verify(args.wheel or current_wheel())
        print("\n".join(problems) or "RECORD consistent")
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
