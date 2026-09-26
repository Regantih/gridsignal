"""The held-out set must stay held out: real days, frozen thresholds, honest totals."""

from __future__ import annotations

import json

import pytest

from gridsignal import backtest, detect, forecast, holdout, signals

TUNED_DATES = {"2026-09-22", "2023-09-06"}


@pytest.fixture(scope="module")
def results() -> list[holdout.DayResult]:
    return holdout.evaluate()


def test_at_least_five_days_are_bundled() -> None:
    assert len(holdout.holdout_paths()) >= 5


def test_every_day_ships_provenance() -> None:
    for path in holdout.holdout_paths():
        meta = json.loads(path.with_suffix(".json").read_text())
        assert meta["location"] == "LZ_HOUSTON"
        assert meta["market"] == "REAL_TIME_15_MIN"
        assert meta["units"] == "$/MWh"
        assert meta["source"].startswith("https://www.ercot.com/")
        assert meta["intervals"] == 96


def test_days_span_multiple_years_and_price_regimes(results: list[holdout.DayResult]) -> None:
    assert len({r.date[:4] for r in results}) >= 3
    assert any(r.peak_mwh > 1000 for r in results), "no high-price day held out"
    assert any(r.peak_mwh < 200 for r in results), "no ordinary day held out"


def test_scenario_days_are_not_scored_as_held_out(results: list[holdout.DayResult]) -> None:
    assert TUNED_DATES.isdisjoint({r.date for r in results})


def test_one_result_per_bundled_day(results: list[holdout.DayResult]) -> None:
    dates = [r.date for r in results]
    assert len(dates) == len(holdout.holdout_paths()) == len(set(dates))
    assert dates == sorted(dates)


def test_uplift_is_signal_minus_naive(results: list[holdout.DayResult]) -> None:
    for r in results:
        assert r.uplift_usd == pytest.approx(r.signal_usd - r.naive_usd, abs=0.01)


def test_scoring_uses_the_frozen_policy_constants() -> None:
    """A change to any threshold must move the held-out numbers, not be bypassed."""
    assert (detect.BASELINE_INTERVALS, detect.MIN_SPREAD_MWH) == (16, 2.0)
    assert (signals.CHARGE_MULTIPLE, signals.SPIKE_THRESHOLD) == (1.15, 0.5)
    assert (backtest.DEFAULT_KWH, backtest.DEFAULT_POWER_KW) == (13.5, 5.0)

    trace = holdout.load_holdout()[0]
    detections = detect.detect_spikes(trace.frame)
    prob = forecast.forecast_spike_probability(forecast.build_features(detections))

    def dollars(**overrides: float) -> float:
        plan = signals.make_signals(detections, prob, **overrides)
        return backtest.summarize(backtest.value_captured(plan, trace.frame)).signal_usd

    scored = holdout.score_day(trace)
    assert scored.signal_usd == dollars()
    assert scored.signal_usd != dollars(retention=0.1)


def test_losing_days_are_reported_not_hidden(results: list[holdout.DayResult]) -> None:
    summary = holdout.summarize(results)
    assert summary.days == len(results)
    assert summary.days_won == sum(r.won for r in results)
    assert summary.worst_uplift_usd == min(r.uplift_usd for r in results)
    assert summary.total_uplift_usd == pytest.approx(sum(r.uplift_usd for r in results), abs=0.01)
    # the frozen policy does lose on some held-out days; the scorecard must keep them
    assert summary.days_won < summary.days


def test_frame_columns_and_rows(results: list[holdout.DayResult]) -> None:
    frame = holdout.as_frame(results)
    assert list(frame.columns) == [
        "date",
        "peak_mwh",
        "mean_mwh",
        "spikes",
        "signal_usd",
        "naive_usd",
        "uplift_usd",
    ]
    assert len(frame) == len(results)


def test_fleet_scaling_uses_mean_daily_uplift(results: list[holdout.DayResult]) -> None:
    summary = holdout.summarize(results)
    assert summary.fleet_usd(10_000) == pytest.approx(summary.mean_uplift_usd * 10_000, abs=0.01)


def test_cli_prints_every_day(capsys: pytest.CaptureFixture[str]) -> None:
    holdout.main()
    out = capsys.readouterr().out
    for result in holdout.evaluate():
        assert result.date in out
    assert "beat the naive schedule" in out
