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
