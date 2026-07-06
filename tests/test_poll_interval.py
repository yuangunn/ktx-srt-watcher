"""Tests for the randomized poll-interval throttle (worker.main helpers)."""

from worker import main


def test_roll_fixed_floors_at_min():
    assert main._roll_interval_min("fixed", 5, {}) == 10   # below floor -> 10
    assert main._roll_interval_min("fixed", 30, {}) == 30
    assert main._roll_interval_min("fixed", 0, {}) == 0     # 0 = no throttle


def test_roll_range_within_bounds():
    for _ in range(100):
        v = main._roll_interval_min("range", 0, {"poll_interval_range": [27, 29]})
        assert 27 <= v <= 29


def test_roll_range_floors_and_normalizes_order():
    # both below floor -> clamped to 10
    for _ in range(50):
        assert main._roll_interval_min("range", 0, {"poll_interval_range": [3, 8]}) == 10
    # reversed bounds are normalized, not empty
    for _ in range(50):
        v = main._roll_interval_min("range", 0, {"poll_interval_range": [40, 20]})
        assert 20 <= v <= 40


def test_roll_choices_only_valid_values():
    seen = set()
    for _ in range(300):
        v = main._roll_interval_min("choices", 0, {"poll_interval_choices": [27, 42, 36, 5]})
        assert v in (27, 42, 36)   # 5 dropped (below floor)
        seen.add(v)
    assert seen == {27, 42, 36}


def test_choices_empty_falls_back_to_fixed():
    assert main._roll_interval_min("choices", 30, {"poll_interval_choices": []}) == 30


def test_should_throttle_fixed_bucket():
    state = {"last_run": "2026-05-30T10:00:00Z"}
    assert main._should_throttle("fixed", 30, {}, state, "2026-05-30T10:20:00Z") is True
    assert main._should_throttle("fixed", 30, {}, state, "2026-05-30T10:35:00Z") is False


def test_should_throttle_fixed_no_interval_never_skips():
    state = {"last_run": "2026-05-30T10:00:00Z"}
    assert main._should_throttle("fixed", 0, {}, state, "2026-05-30T10:01:00Z") is False


def test_should_throttle_random_uses_next_poll_at():
    s = {"poll_interval_range": [27, 29]}
    state = {"next_poll_at": "2026-05-30T10:27:00Z"}
    assert main._should_throttle("range", 0, s, state, "2026-05-30T10:20:00Z") is True
    assert main._should_throttle("range", 0, s, state, "2026-05-30T10:28:00Z") is False
    # first run (no next_poll_at) proceeds
    assert main._should_throttle("range", 0, s, {}, "2026-05-30T10:00:00Z") is False


def test_set_next_poll_random_stores_and_fixed_clears():
    state = {}
    main._set_next_poll("range", {"poll_interval_range": [27, 27]}, state, "2026-05-30T10:00:00Z")
    assert state["next_poll_at"] == "2026-05-30T10:27:00Z"
    main._set_next_poll("fixed", {"poll_interval_min": 30}, state, "2026-05-30T10:30:00Z")
    assert "next_poll_at" not in state
