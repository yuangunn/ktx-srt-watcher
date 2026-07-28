"""Tests for worker.remote — config/state I/O against the CF Worker KV."""
from __future__ import annotations

import json
import urllib.error

import pytest

from worker import remote


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CF_WORKER_URL", "https://worker.example/")
    monkeypatch.setenv("REMINDER_TOKEN", "tok")


class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capture(monkeypatch, body: bytes = b"{}"):
    """Patch urlopen, recording the Request it was handed."""
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["auth"] = req.headers.get("Authorization")
        seen["body"] = req.data
        return _Resp(body)

    monkeypatch.setattr(remote.urllib.request, "urlopen", fake_urlopen)
    return seen


def _http_error(code: int):
    def raiser(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, code, "err", {}, None)
    return raiser


class TestFetchConfig:
    def test_returns_parsed_json(self, monkeypatch):
        _capture(monkeypatch, json.dumps({"watches": [{"id": "w1"}]}).encode())
        assert remote.fetch_config()["watches"][0]["id"] == "w1"

    def test_sends_bearer_token_to_config_endpoint(self, monkeypatch):
        seen = _capture(monkeypatch)
        remote.fetch_config()
        assert seen["url"] == "https://worker.example/config"
        assert seen["auth"] == "Bearer tok"
        assert seen["method"] == "GET"

    def test_trailing_slash_does_not_double_up(self, monkeypatch):
        seen = _capture(monkeypatch)
        remote.fetch_config()
        assert "//config" not in seen["url"].removeprefix("https://")

    def test_raises_on_http_error(self, monkeypatch):
        monkeypatch.setattr(remote.urllib.request, "urlopen", _http_error(401))
        with pytest.raises(remote.RemoteError, match="401"):
            remote.fetch_config()

    def test_missing_config_returns_none_not_an_error(self, monkeypatch):
        # No watches added yet is a normal state; raising here painted every
        # 5-minute tick red.
        monkeypatch.setattr(remote.urllib.request, "urlopen", _http_error(404))
        assert remote.fetch_config() is None

    def test_server_error_still_raises(self, monkeypatch):
        # 5xx means we cannot tell whether there is anything to poll.
        monkeypatch.setattr(remote.urllib.request, "urlopen", _http_error(503))
        with pytest.raises(remote.RemoteError):
            remote.fetch_config()

    def test_raises_on_malformed_json(self, monkeypatch):
        _capture(monkeypatch, b"not json{{")
        with pytest.raises(remote.RemoteError, match="valid JSON"):
            remote.fetch_config()

    def test_raises_when_url_missing(self, monkeypatch):
        monkeypatch.delenv("CF_WORKER_URL")
        with pytest.raises(remote.RemoteError, match="CF_WORKER_URL"):
            remote.fetch_config()

    def test_raises_when_token_missing(self, monkeypatch):
        monkeypatch.delenv("REMINDER_TOKEN")
        with pytest.raises(remote.RemoteError, match="REMINDER_TOKEN"):
            remote.fetch_config()


class TestFetchState:
    def test_returns_parsed_json(self, monkeypatch):
        _capture(monkeypatch, json.dumps({"last_run": "2026-07-27T00:00:00Z"}).encode())
        assert remote.fetch_state()["last_run"] == "2026-07-27T00:00:00Z"

    def test_missing_state_is_empty_default_not_an_error(self, monkeypatch):
        # Fresh install: no state stored yet. Polling must still proceed.
        monkeypatch.setattr(remote.urllib.request, "urlopen", _http_error(404))
        assert remote.fetch_state() == {"last_run": None, "watches": {}}

    def test_other_http_errors_still_raise(self, monkeypatch):
        monkeypatch.setattr(remote.urllib.request, "urlopen", _http_error(500))
        with pytest.raises(remote.RemoteError):
            remote.fetch_state()

    def test_corrupt_state_degrades_to_default(self, monkeypatch):
        # Losing dedup history re-notifies a seat; losing the run is worse.
        _capture(monkeypatch, b"{{{")
        assert remote.fetch_state() == {"last_run": None, "watches": {}}


class TestPushState:
    def test_puts_json_body(self, monkeypatch):
        seen = _capture(monkeypatch)
        remote.push_state({"last_run": "t", "watches": {}})
        assert seen["method"] == "PUT"
        assert seen["url"] == "https://worker.example/state"
        assert json.loads(seen["body"].decode())["last_run"] == "t"

    def test_keeps_korean_unescaped(self, monkeypatch):
        seen = _capture(monkeypatch)
        remote.push_state({"watches": {"부산행": {}}})
        assert "부산행" in seen["body"].decode("utf-8")

    def test_raises_on_http_error(self, monkeypatch):
        monkeypatch.setattr(remote.urllib.request, "urlopen", _http_error(401))
        with pytest.raises(remote.RemoteError):
            remote.push_state({})


class TestUserAgent:
    def test_sends_explicit_user_agent(self, monkeypatch):
        # Cloudflare bot protection 403s the default "Python-urllib/x.y" before
        # the request reaches the Worker, which silently killed every call.
        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["ua"] = req.headers.get("User-agent")
            return _Resp(b"{}")

        monkeypatch.setattr(remote.urllib.request, "urlopen", fake_urlopen)
        remote.fetch_config()
        assert seen["ua"] == remote.USER_AGENT
        assert "urllib" not in (seen["ua"] or "").lower()


class TestFetchMode:
    def test_returns_away(self, monkeypatch):
        _capture(monkeypatch, json.dumps({"mode": "away"}).encode())
        assert remote.fetch_mode() == "away"

    def test_returns_home(self, monkeypatch):
        _capture(monkeypatch, json.dumps({"mode": "home"}).encode())
        assert remote.fetch_mode() == "home"

    def test_unreachable_falls_back_to_home(self, monkeypatch):
        # Failing quiet would mean sleeping through a 3am cancellation.
        monkeypatch.setattr(remote.urllib.request, "urlopen", _http_error(500))
        assert remote.fetch_mode() == "home"

    def test_garbage_body_falls_back_to_home(self, monkeypatch):
        _capture(monkeypatch, b"not json")
        assert remote.fetch_mode() == "home"

    def test_unknown_value_falls_back_to_home(self, monkeypatch):
        _capture(monkeypatch, json.dumps({"mode": "elsewhere"}).encode())
        assert remote.fetch_mode() == "home"
