from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import notifier
from . import state as state_mod
from .adapters.base import Provider
from .adapters.korail import KorailProvider
from .adapters.srt import SRTProvider
from .matcher import find_new_trains
from .models import Train, Watch

log = logging.getLogger("ticket_watcher")

CONFIG_PATH = Path("config.json")
STATE_PATH = Path("state.json")

NotifyFn = Callable[[Watch, list[Train]], None]


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cfg = load_config(CONFIG_PATH)
    s = state_mod.read_state(STATE_PATH)
    creds = load_credentials()
    providers: dict[str, Provider] = {
        "korail": KorailProvider(),
        "srt": SRTProvider(),
    }
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_watches(
        cfg, s,
        providers=providers,
        creds=creds,
        notify_fn=notifier.notify,
        now_iso=now_iso,
    )
    state_mod.prune_past_dates(s, today=now_iso[:10])
    state_mod.write_state(STATE_PATH, s)
    return 0


def run_watches(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    providers: dict[str, Provider],
    creds: dict[str, tuple[str, str]],
    notify_fn: NotifyFn,
    now_iso: str,
) -> None:
    state_mod.mark_run(state, now_iso)

    active = [Watch.model_validate(w) for w in config.get("watches", []) if w.get("active", True)]
    by_provider: dict[str, list[Watch]] = {}
    for w in active:
        by_provider.setdefault(w.provider, []).append(w)

    for provider_name, watches in by_provider.items():
        provider = providers.get(provider_name)
        if provider is None:
            log.error("unknown provider: %s", provider_name)
            continue
        user, password = creds.get(provider_name, ("", ""))
        try:
            provider.login(user, password)
        except Exception as e:
            log.exception("[%s] login failed: %s", provider_name, e)
            continue
        for watch in watches:
            try:
                _process_watch(watch, provider, state, notify_fn, now_iso)
            except Exception as e:
                log.exception("[%s] watch %s failed: %s", provider_name, watch.id, e)


def _process_watch(
    watch: Watch,
    provider: Provider,
    state: dict[str, Any],
    notify_fn: NotifyFn,
    now_iso: str,
) -> None:
    state_mod.mark_check(state, watch.id, now_iso)
    state_mod.set_watch_date(state, watch.id, watch.date)

    notified = state_mod.get_notified_ids(state, watch.id)
    trains = provider.search(watch)
    new_trains = find_new_trains(watch, trains, notified)

    if not new_trains:
        log.info("[%s] watch %s: no new seats (%d searched)", provider.name, watch.id, len(trains))
        return

    log.info("[%s] watch %s: %d new seat(s)", provider.name, watch.id, len(new_trains))
    notify_fn(watch, new_trains)
    state_mod.add_notified_ids(state, watch.id, [t.raw_id for t in new_trains])


def load_config(path: Path | str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_credentials() -> dict[str, tuple[str, str]]:
    return {
        "korail": (os.environ.get("KORAIL_ID", ""), os.environ.get("KORAIL_PW", "")),
        "srt": (os.environ.get("SRT_ID", ""), os.environ.get("SRT_PW", "")),
    }


if __name__ == "__main__":
    sys.exit(main())
