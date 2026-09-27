"""The Open Grid Data claim has to survive being checked against the bundled data."""

from __future__ import annotations

import pandas as pd
import pytest

from gridsignal import backtest, dam, insight
from gridsignal.prices import PriceTrace
from gridsignal.signals import Signal


@pytest.fixture(scope="module")
def results() -> list[insight.DayInsight]:
    return insight.analyze()


def trace_from(prices: list[float], dam_prices: list[float]) -> PriceTrace:
    """A synthetic day: 96 real-time intervals and the 24-hour day-ahead curve."""
    starts = pd.date_range("2030-01-01", periods=len(prices), freq="15min")
    hours = pd.date_range("2030-01-01", periods=len(dam_prices), freq="h")
    return PriceTrace(
        location="TEST",
        market="REAL_TIME_15_MIN",
        date="2030-01-01",
        source="synthetic",
        frame=pd.DataFrame(
            {
                "interval_start": starts,
                "interval_end": starts + pd.Timedelta(minutes=15),
                "spp": prices,
            }
        ),
        dam=pd.DataFrame(
            {
                "interval_start": hours,
                "interval_end": hours + pd.Timedelta(hours=1),
                "spp": dam_prices,
            }
        ),
    )


def test_every_bundled_day_is_scored(results: list[insight.DayInsight]) -> None:
    assert len(results) == len(insight.bundled_traces()) >= 15
    assert len({r.date for r in results}) == len(results)


def test_foresight_is_a_ceiling_the_day_ahead_plan_cannot_beat(
    results: list[insight.DayInsight],
) -> None:
    """A plan built without tomorrow's prints can never beat knowing them."""
    for r in results:
        assert r.day_ahead_usd <= r.foresight_usd + 1e-6, r.date
        assert 0.0 <= r.visible_share <= 1.0


def test_foresight_buys_before_it_sells_and_respects_the_battery() -> None:
    prices = [10.0] * 48 + [500.0] * 48
    plan = insight.foresight_plan(trace_from(prices, [10.0] * 12 + [500.0] * 12).frame)
    charge = plan.index[plan["signal"] == Signal.CHARGE.value]
    export = plan.index[plan["signal"] == Signal.EXPORT.value]
    assert max(charge) < min(export)
    # 13.5 kWh through a 90% round trip at 1.25 kWh per interval: buy 12, sell 11.
    assert len(charge) == round(insight.STUDY_KWH / backtest.ROUND_TRIP_EFFICIENCY / 1.25)
    assert len(export) == -(-insight.STUDY_KWH // 1.25)


def test_a_flat_day_has_nothing_to_capture() -> None:
    flat = insight.analyze_day(trace_from([40.0] * 96, [40.0] * 24))
    assert flat.foresight_usd <= 0.0
    assert flat.visible_share == 0.0
    assert flat.divergent_intervals == 0


def test_a_spike_the_day_ahead_curve_never_priced_shows_up_as_divergence() -> None:
    prices = [20.0] * 96
    prices[70] = 20.0 * (dam.DEVIATION_MULTIPLE + 5)
    day = insight.analyze_day(trace_from(prices, [20.0] * 24))
    assert day.divergent_intervals == 1
    assert day.max_divergence > dam.DEVIATION_MULTIPLE
    # The flat day-ahead curve plans nothing, so the spike is pure real-time value.
    assert day.day_ahead_usd == 0.0
    assert day.only_real_time_usd > 0


def test_a_day_the_curve_called_correctly_is_mostly_visible() -> None:
    curve = [20.0] * 18 + [200.0] * 6
    day = insight.analyze_day(trace_from([p for p in curve for _ in range(4)], curve))
    assert day.visible_share > 0.9
    assert day.divergent_intervals == 0


def test_scarcity_days_hide_more_of_their_value_than_ordinary_days(
    results: list[insight.DayInsight],
) -> None:
    summary = insight.summarize(results)
    assert summary.scarcity_days >= 2
    assert summary.scarcity_visible_share < summary.ordinary_visible_share
    # ...on the legacy unit the study is scored on. The bigger default unit rides more
    # of the evening out of the day-ahead plan, so the gap does not survive it, and the
    # CLI prints both rather than letting the published share drift with the hardware.
    bigger = insight.summarize(
        insight.analyze(kwh=backtest.DEFAULT_KWH, power_kw=backtest.DEFAULT_POWER_KW)
    )
    assert bigger.scarcity_visible_share > summary.scarcity_visible_share
    # The claim on the card: the missing money dwarfs an ordinary day's whole upside.
    assert summary.scarcity_blind_usd > 10 * summary.ordinary_day_usd


def test_divergent_intervals_are_a_scarcity_phenomenon(
    results: list[insight.DayInsight],
) -> None:
    summary = insight.summarize(results)
    assert summary.divergent_intervals > 0
    assert summary.ordinary_divergent_intervals == 0
    assert summary.divergent_share < 0.05


def test_headline_states_only_what_was_computed(results: list[insight.DayInsight]) -> None:
    summary = insight.summarize(results)
    assert f"{summary.scarcity_days} real ERCOT scarcity days" in summary.headline
    assert f"{summary.scarcity_visible_share:.0%}" in summary.headline
    assert f"{summary.intervals:,}" in summary.subhead


def test_frame_has_a_row_per_day(results: list[insight.DayInsight]) -> None:
    frame = insight.as_frame(results)
    assert len(frame) == len(results)
    assert set(frame.columns) >= {"date", "visible_share", "only_real_time_usd"}


def test_a_day_without_a_day_ahead_curve_is_refused() -> None:
    trace = trace_from([20.0] * 96, [20.0] * 24)
    bare = PriceTrace(
        location=trace.location,
        market=trace.market,
        date=trace.date,
        source=trace.source,
        frame=trace.frame,
        dam=None,
    )
    with pytest.raises(ValueError, match="no bundled day-ahead curve"):
        insight.day_ahead_plan(bare)
    assert insight.analyze([bare]) == []


def test_summarize_refuses_an_empty_set() -> None:
    with pytest.raises(ValueError, match="no bundled days"):
        insight.summarize([])
