"""Smoke tests for adapters — full integration requires live accounts (manual)."""
from __future__ import annotations

import pytest

from worker.adapters import korail, srt
from worker.adapters.base import Provider
from worker.models import Passengers


class TestModuleImports:
    def test_korail_provider_is_instantiable(self):
        provider = korail.KorailProvider()
        assert provider.name == "korail"

    def test_srt_provider_is_instantiable(self):
        provider = srt.SRTProvider()
        assert provider.name == "srt"

    def test_korail_satisfies_provider_protocol(self):
        provider: Provider = korail.KorailProvider()
        assert callable(provider.login)
        assert callable(provider.search)
        assert callable(provider.reserve)

    def test_srt_satisfies_provider_protocol(self):
        provider: Provider = srt.SRTProvider()
        assert callable(provider.login)
        assert callable(provider.search)
        assert callable(provider.reserve)


class TestKorailHelpers:
    def test_format_time_pads_six_digit(self):
        assert korail._fmt_time("093500") == "09:35"

    def test_format_time_pads_short(self):
        assert korail._fmt_time("93500") == "09:35"

    def test_build_passengers_uses_adult_count(self):
        result = korail._build_passengers(Passengers(adult=2, child=0, senior=0))
        assert len(result) == 1
        assert result[0].count == 2

    def test_build_passengers_combines_classes(self):
        result = korail._build_passengers(Passengers(adult=1, child=1, senior=1))
        assert len(result) == 3

    def test_build_passengers_falls_back_to_one_adult_when_all_zero(self):
        result = korail._build_passengers(Passengers(adult=0, child=0, senior=0))
        assert len(result) == 1


class TestSRTHelpers:
    def test_format_time_pads_six_digit(self):
        assert srt._fmt_time("180000") == "18:00"

    def test_build_passengers_uses_adult_count(self):
        result = srt._build_passengers(Passengers(adult=2, child=0, senior=0))
        assert len(result) == 1

    def test_build_passengers_falls_back_to_one_adult(self):
        result = srt._build_passengers(Passengers(adult=0, child=0, senior=0))
        assert len(result) == 1


class TestUnauthenticatedSearchRaises:
    def test_korail_search_without_login_raises(self):
        provider = korail.KorailProvider()
        from worker.models import Watch
        watch = Watch.model_validate({
            "id": "x", "provider": "korail", "from": "서울", "to": "부산",
            "date": "2026-05-15", "time_min": "09:00", "time_max": "12:00",
            "train_types": ["KTX"],
        })
        with pytest.raises(RuntimeError):
            provider.search(watch)

    def test_srt_search_without_login_raises(self):
        provider = srt.SRTProvider()
        from worker.models import Watch
        watch = Watch.model_validate({
            "id": "x", "provider": "srt", "from": "수서", "to": "부산",
            "date": "2026-05-15", "time_min": "18:00", "time_max": "22:00",
            "train_types": ["SRT"],
        })
        with pytest.raises(RuntimeError):
            provider.search(watch)
