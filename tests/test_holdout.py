"""The held-out set must stay held out: real days, frozen thresholds, honest totals."""

from __future__ import annotations

import json

import pytest

from gridsignal import backtest, dam, detect, forecast, holdout, signals

TUNED_DATES = {"2026-09-22", "2023-09-06"}


@pytest.fixture(scope="module")
def results() -> list[holdout.DayResult]:
    return holdout.evaluate()


def test_at_least_five_days_are_bundled() -> None:
    assert len(holdout.holdout_paths()) >= 5


def test_tuning_and_holdout_splits_never_share_a_day() -> None:
    tuning = {t.date for t in holdout.load_tuning()}
    assert tuning, "no tuning days bundled"
    assert tuning.isdisjoint({t.date for t in holdout.load_holdout()})
    assert tuning.isdisjoint(TUNED_DATES)


def test_every_scored_day_has_a_day_ahead_curve() -> None:
    for trace in holdout.load_holdout() + holdout.load_tuning():
        assert trace.dam is not None, f"{trace.date} has no bundled DAM curve"
        assert len(trace.dam) == 24


def test_tuning_split_scores_too() -> None:
    results = holdout.evaluate_tuning()
    assert len(results) == len(holdout.tuning_paths())
    assert all(r.date not in TUNED_DATES for r in results)


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
    assert (dam.CHARGE_HOURS, dam.EXPORT_HOURS, dam.MIN_SPREAD) == (5, 6, 1.4)
    assert (dam.DEVIATION_MULTIPLE, dam.CHARGE_CEILING, dam.EXPORT_FLOOR) == (5.0, 5.0, 0.8)

    trace = holdout.load_holdout()[0]
    detections = detect.detect_spikes(trace.frame)
    prob = forecast.forecast_spike_probability(forecast.build_features(detections))

    def dollars(**overrides: float) -> float:
        plan = dam.deviate_from_plan(detections, prob, trace.dam, **overrides)
        return backtest.summarize(backtest.value_captured(plan, trace.frame)).signal_usd

    # Scored grid-only, the day matches the frozen policy exactly.
    scored = holdout.score_day(trace, serve_home=False)
    assert scored.signal_usd == dollars()
    assert scored.signal_usd != dollars(charge_hours=1, export_hours=1)
    # Home-first runs the same plan but pays the house first, so it can only earn less.
    assert holdout.score_day(trace).signal_usd <= scored.signal_usd


def test_losing_days_are_reported_not_hidden(results: list[holdout.DayResult]) -> None:
    summary = holdout.summarize(results)
    assert summary.days == len(results)
    assert summary.days_won == sum(r.won for r in results)
    assert summary.worst_uplift_usd == min(r.uplift_usd for r in results)
    assert summary.total_uplift_usd == pytest.approx(sum(r.uplift_usd for r in results), abs=0.01)
    # the frozen policy still fails to beat naive on some held-out days; keep them
    assert summary.days_won < summary.days
    assert summary.worst_uplift_usd <= 0
    # grid-only, before home load nets against export, it loses outright on a day
    assert holdout.summarize(holdout.evaluate(serve_home=False)).worst_uplift_usd < 0


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
        "member_savings_usd",
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
