from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

DEFAULT_STATE: dict[str, Any] = {"last_run": None, "watches": {}}


def read_state(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return _default()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _default()


def write_state(path: Path | str, state: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def get_notified_ids(state: dict[str, Any], watch_id: str) -> set[str]:
    return set(state.get("watches", {}).get(watch_id, {}).get("notified_train_ids", []))


def add_notified_ids(state: dict[str, Any], watch_id: str, train_ids: Iterable[str]) -> None:
    entry = _entry(state, watch_id)
    merged = set(entry.get("notified_train_ids", []))
    merged.update(train_ids)
    entry["notified_train_ids"] = sorted(merged)


def mark_run(state: dict[str, Any], when: str) -> None:
    state["last_run"] = when


def mark_check(state: dict[str, Any], watch_id: str, when: str) -> None:
    _entry(state, watch_id)["last_check"] = when


def set_watch_date(state: dict[str, Any], watch_id: str, watch_date: str) -> None:
    _entry(state, watch_id)["watch_date"] = watch_date


def prune_past_dates(state: dict[str, Any], today: str) -> None:
    watches = state.get("watches", {})
    expired = [wid for wid, entry in watches.items() if entry.get("watch_date") and entry["watch_date"] < today]
    for wid in expired:
        del watches[wid]


def _entry(state: dict[str, Any], watch_id: str) -> dict[str, Any]:
    watches = state.setdefault("watches", {})
    return watches.setdefault(watch_id, {"last_check": None, "notified_train_ids": []})


def _default() -> dict[str, Any]:
    return {"last_run": None, "watches": {}}


def merge_states(ours: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any]:
    """Three-way semantic merge of two state.json snapshots.

    Used by the workflow's commit step to resolve the situation where two
    runs (e.g. CF cron + GHA fallback, or back-to-back ticks) both committed
    state.json before either could push.  git's line-merge can't reconcile
    different edits to the same JSON keys, so we do it here:

      - last_run: max (most recent timestamp wins)
      - watches keys: union (both runs' watch IDs survive)
      - per watch:
        - last_check: max
        - notified_train_ids: union (sorted, deduped)
        - watch_date: prefer non-empty (ours falls back to remote)

    The merge is symmetric and idempotent on identical inputs.
    """
    result: dict[str, Any] = {}

    result["last_run"] = _later(ours.get("last_run"), remote.get("last_run"))

    o_watches = ours.get("watches") or {}
    r_watches = remote.get("watches") or {}
    merged_watches: dict[str, dict[str, Any]] = {}
    for wid in set(o_watches) | set(r_watches):
        o_w = o_watches.get(wid) or {}
        r_w = r_watches.get(wid) or {}
        merged_watches[wid] = {
            "last_check": _later(o_w.get("last_check"), r_w.get("last_check")),
            "notified_train_ids": sorted(
                set(o_w.get("notified_train_ids") or [])
                | set(r_w.get("notified_train_ids") or [])
            ),
        }
        watch_date = o_w.get("watch_date") or r_w.get("watch_date")
        if watch_date:
            merged_watches[wid]["watch_date"] = watch_date
    result["watches"] = merged_watches

    # Carry over any top-level keys we don't know about (forward compat)
    for k, v in {**remote, **ours}.items():
        if k not in result:
            result[k] = v

    return result


def _later(a: str | None, b: str | None) -> str | None:
    if not a:
        return b
    if not b:
        return a
    return a if a >= b else b


if __name__ == "__main__":
    # CLI: python -m worker.state merge OUR_STATE_PATH REMOTE_STATE_PATH
    # Reads both files, writes the merged result to REMOTE_STATE_PATH
    # (the file the workflow will commit). Used by .github/workflows/watch.yml
    # when concurrent runs collide.
    import sys

    def _fail(msg: str, code: int = 2) -> None:
        print(msg, file=sys.stderr)
        sys.exit(code)

    if len(sys.argv) != 4 or sys.argv[1] != "merge":
        _fail("usage: python -m worker.state merge OUR_STATE REMOTE_STATE")

    ours_path = Path(sys.argv[2])
    remote_path = Path(sys.argv[3])
    if not ours_path.exists() or not remote_path.exists():
        _fail(f"missing state file: ours={ours_path.exists()}, remote={remote_path.exists()}")

    ours = json.loads(ours_path.read_text(encoding="utf-8"))
    remote = json.loads(remote_path.read_text(encoding="utf-8"))
    merged = merge_states(ours, remote)
    remote_path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"merged into {remote_path} ({len(merged.get('watches', {}))} watches)")
