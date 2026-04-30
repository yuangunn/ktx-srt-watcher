"""Tests for worker.notifier — Telegram message formatting + sending."""
from __future__ import annotations

import pytest

from worker.models import Train, Watch
from worker import notifier


def _watch(**overrides) -> Watch:
    base = {
        "id": "w1",
        "provider": "korail",
        "from": "서울",
        "to": "부산",
        "date": "2026-05-15",
        "time_min": "09:00",
        "time_max": "12:00",
        "train_types": ["KTX"],
    }
    base.update(overrides)
    return Watch.model_validate(base)


def _train(**overrides) -> Train:
    base = dict(
        provider="korail",
        train_no="045",
        train_type="KTX",
        dep_station="서울",
        arr_station="부산",
        date="2026-05-15",
        dep_time="09:35",
        arr_time="12:10",
        seats_general=2,
        seats_special=0,
        raw_id="2026-05-15-045-09:35",
        booking_url="https://www.letskorail.com/abc",
    )
    base.update(overrides)
    return Train(**base)


class FakeResponse:
    def __init__(self, status: int = 200):
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, response: FakeResponse | None = None):
        self.response = response or FakeResponse()
        self.calls: list[dict] = []

    def post(self, url: str, *, json: dict, timeout: int) -> FakeResponse:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return self.response


class TestFormatMessage:
    def test_includes_route_and_date_header(self):
        msg = notifier.format_message(_watch(), [_train()])
        assert "서울→부산" in msg
        assert "2026-05-15" in msg
        assert "🚄" in msg

    def test_includes_train_details(self):
        train = _train(train_no="045", dep_time="09:35", train_type="KTX", seats_general=1)
        msg = notifier.format_message(_watch(), [train])
        assert "09:35" in msg
        assert "KTX" in msg
        assert "045" in msg
        assert "일반" in msg

    def test_special_seats_only(self):
        train = _train(seats_general=0, seats_special=1)
        msg = notifier.format_message(_watch(), [train])
        assert "특실" in msg
        # 일반 (any class) shouldn't appear in the seat line for this train
        seat_line = next(l for l in msg.split("\n") if "발" in l and train.train_no in l)
        assert "일반" not in seat_line

    def test_both_seat_classes(self):
        train = _train(seats_general=1, seats_special=1)
        msg = notifier.format_message(_watch(), [train])
        assert "일반" in msg
        assert "특실" in msg

    def test_count_disclaimer_present(self):
        train = _train()
        msg = notifier.format_message(_watch(), [train])
        assert "정확한 잔여석" in msg
        assert "앱에서 확인" in msg

    def test_no_fake_seat_count_in_message(self):
        # 옛 포맷("일반 N석")이 다시 들어오지 않게 회귀 테스트
        train = _train(seats_general=1, seats_special=1)
        msg = notifier.format_message(_watch(), [train])
        # "1석" / "2석" 등 가짜 카운트 금지
        import re
        assert not re.search(r"일반 \d+석", msg)
        assert not re.search(r"특실 \d+석", msg)

    def test_includes_booking_url(self):
        train = _train(booking_url="https://example.com/reserve/x")
        msg = notifier.format_message(_watch(), [train])
        assert "https://example.com/reserve/x" in msg

    def test_omits_url_line_when_missing(self):
        train = _train(booking_url="")
        msg = notifier.format_message(_watch(), [train])
        assert "예매:" not in msg

    def test_multiple_trains_listed(self):
        trains = [
            _train(train_no="045", dep_time="09:35", raw_id="a"),
            _train(train_no="047", dep_time="10:00", raw_id="b"),
        ]
        msg = notifier.format_message(_watch(), trains)
        assert "045" in msg
        assert "047" in msg

    def test_empty_train_list_returns_minimal_header(self):
        msg = notifier.format_message(_watch(), [])
        assert "서울→부산" in msg


class TestSendTelegram:
    def test_posts_to_telegram_api(self):
        session = FakeSession()
        notifier.send_telegram("BOTTOK", "12345", "hello", session=session)
        assert len(session.calls) == 1
        call = session.calls[0]
        assert call["url"] == "https://api.telegram.org/botBOTTOK/sendMessage"
        assert call["json"]["chat_id"] == "12345"
        assert call["json"]["text"] == "hello"

    def test_raises_on_http_error(self):
        session = FakeSession(FakeResponse(status=400))
        with pytest.raises(RuntimeError):
            notifier.send_telegram("BOTTOK", "12345", "hello", session=session)

    def test_uses_timeout(self):
        session = FakeSession()
        notifier.send_telegram("BOTTOK", "12345", "hello", session=session)
        assert session.calls[0]["timeout"] >= 5


class TestNotify:
    def test_reads_env_vars_and_sends(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TOKEN1")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "CHAT1")
        session = FakeSession()
        notifier.notify(_watch(), [_train()], session=session)
        assert len(session.calls) == 1
        assert session.calls[0]["url"].endswith("/botTOKEN1/sendMessage")
        assert session.calls[0]["json"]["chat_id"] == "CHAT1"

    def test_raises_when_token_missing(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "CHAT1")
        with pytest.raises((KeyError, RuntimeError)):
            notifier.notify(_watch(), [_train()], session=FakeSession())

    def test_does_not_send_when_train_list_empty(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TOKEN1")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "CHAT1")
        session = FakeSession()
        notifier.notify(_watch(), [], session=session)
        assert session.calls == []


class TestFormatSummary:
    def test_empty_watch_list_returns_inactive_message(self):
        msg = notifier.format_summary([])
        assert "활성 워치 없음" in msg

    def test_summary_lists_each_watch_route_and_window(self):
        watches = [
            _watch(),
            _watch(**{"id": "w2", "from": "수서", "to": "부산", "provider": "srt", "train_types": ["SRT"]}),
        ]
        msg = notifier.format_summary(watches)
        assert "확인 완료" in msg
        assert "잔여 0건" in msg
        assert "서울→부산" in msg
        assert "수서→부산" in msg
        assert "[KTX]" in msg
        assert "[SRT]" in msg


class TestNotifySummary:
    def test_sends_telegram(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")
        session = FakeSession()
        notifier.notify_summary([_watch()], session=session)
        assert len(session.calls) == 1
        assert "확인 완료" in session.calls[0]["json"]["text"]

    def test_sends_even_when_watches_empty(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")
        session = FakeSession()
        notifier.notify_summary([], session=session)
        assert len(session.calls) == 1


def _reservation():
    from worker.models import Reservation
    return Reservation(
        provider="korail",
        reservation_id="ABC123",
        train_no="045",
        expires_at="2026-04-30T19:55:00Z",
    )


class TestFormatReservation:
    def test_success_includes_route_train_and_deadline(self):
        msg = notifier.format_reservation_success(_watch(), _train(), _reservation())
        assert "✅" in msg
        assert "임시예약" in msg
        assert "ABC123" in msg
        assert "045" in msg
        assert "서울→부산" in msg
        assert "19:55" in msg
        assert "결제 마감" in msg or "결제" in msg

    def test_success_warns_about_manual_payment(self):
        msg = notifier.format_reservation_success(_watch(), _train(), _reservation())
        assert "결제는 코레일톡" in msg or "직접 결제" in msg or "앱에서" in msg

    def test_failure_includes_route_train_and_reason(self):
        msg = notifier.format_reservation_failure(_watch(), _train(), "좌석 매진")
        assert "❌" in msg or "실패" in msg
        assert "045" in msg
        assert "좌석 매진" in msg


class TestNotifyReservation:
    def test_success_sends_telegram(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")
        session = FakeSession()
        notifier.notify_reservation_success(_watch(), _train(), _reservation(), session=session)
        assert len(session.calls) == 1
        assert "ABC123" in session.calls[0]["json"]["text"]

    def test_failure_sends_telegram(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")
        session = FakeSession()
        notifier.notify_reservation_failure(_watch(), _train(), "로그인 실패", session=session)
        assert len(session.calls) == 1
        assert "로그인 실패" in session.calls[0]["json"]["text"]
