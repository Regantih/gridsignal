"""Live ERCOT fetch: parsing recorded ercot.com pages, and failing safe without a network."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from gridsignal import live
from gridsignal.control_room.engine import ControlRoomEngine
from gridsignal.demo_numbers import FOCUS_DEVICE_ID

FIXTURES = Path(__file__).parent / "fixtures" / "ercot_live"
RT = (FIXTURES / "20260926_real_time_spp.html").read_text()
DAM = (FIXTURES / "20260926_dam_spp.html").read_text()
PARTIAL = (FIXTURES / "20260927_real_time_spp_partial.html").read_text()


def _serve(pages: dict[str, str]):
    def fake_get(url: str) -> str:
        for name, html in pages.items():
            if url.endswith(name):
                return html
        raise live.LiveDataError("not found")

    return fake_get


def test_a_recorded_real_time_page_parses_to_a_full_day_of_15_minute_prices() -> None:
    frame = live.parse_real_time(RT)
    assert len(frame) == 96
    assert str(frame["interval_start"].iloc[0]) == "2026-09-26 00:00:00"
    assert str(frame["interval_end"].iloc[-1]) == "2026-09-27 00:00:00"
    assert frame["spp"].max() == pytest.approx(44.26)


def test_a_recorded_day_ahead_page_parses_to_24_hours() -> None:
    frame = live.parse_day_ahead(DAM)
    assert len(frame) == 24
    assert (frame["interval_end"] - frame["interval_start"]).dt.total_seconds().eq(3600).all()


def test_the_live_trace_prices_the_same_incident_workflow(monkeypatch) -> None:
    monkeypatch.setattr(live, "_get", _serve({"_real_time_spp.html": RT, "_dam_spp.html": DAM}))
    trace = live.fetch_live_trace("2026-09-26")
    assert trace.live and trace.dam is not None
    eng = ControlRoomEngine(price_trace=trace, fleet_size=10_000)
    assert "fetched live from ercot.com" in eng.grid_event.price_source
    incident = eng.trigger_device_failure(FOCUS_DEVICE_ID)
    eng.approve_recovery()
    assert len(incident.cohort) == 166
    assert 0 < incident.dollars_recovered <= incident.dollars_at_risk
    assert eng.snapshot().coverage_pct == pytest.approx(100, abs=0.5)


def test_a_day_that_has_not_finished_is_refused_not_half_priced(monkeypatch) -> None:
    monkeypatch.setattr(live, "_get", _serve({"_real_time_spp.html": PARTIAL}))
    with pytest.raises(live.LiveDataError, match="of 96 intervals"):
        live.fetch_live_trace("2026-09-27")


def test_no_network_raises_a_clean_live_data_error(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(live.httpx, "get", boom)
    with pytest.raises(live.LiveDataError, match="could not reach ERCOT"):
        live.fetch_live_trace("2026-09-26")


def test_a_page_without_a_price_table_is_rejected() -> None:
    with pytest.raises(live.LiveDataError):
        live.parse_real_time("<html><body>maintenance</body></html>")


def test_bundled_traces_still_say_cached() -> None:
    eng = ControlRoomEngine()
    assert "cached Parquet" in eng.grid_event.price_source
