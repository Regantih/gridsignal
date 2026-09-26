"""Q8 degradation-aware dispatch: the wear of a cycle, priced and gated on."""

from pathlib import Path

import pandas as pd
import pytest

from gridsignal import backtest, dam, degradation, detect, forecast, holdout
from gridsignal.control_room.models import UnitType
from gridsignal.degradation import WEAR_MODELS, gate_plan, model_for, required_export_mwh
from gridsignal.signals import Signal

LEGACY = model_for(UnitType.LEGACY)
BASE_CORE = model_for(UnitType.BASE_CORE)


def plan_for(trace):
    detections = detect.detect_spikes(trace.frame)
    prob = forecast.forecast_spike_probability(forecast.build_features(detections))
    return dam.signals_for(detections, prob, trace.dam)


def test_wear_cost_is_replacement_over_cycle_life():
    assert LEGACY.wear_usd_per_kwh == pytest.approx(400.0 / 4_000)
    assert LEGACY.wear_usd_per_mwh == 100.0
    # The cheaper, longer-lived pack is cheaper to cycle; that is the whole point of
    # reporting this per battery type.
    assert BASE_CORE.wear_usd_per_mwh < LEGACY.wear_usd_per_mwh
    for model in WEAR_MODELS:
        assert "assumption" in model.assumption


def test_the_export_a_cycle_needs_covers_the_energy_and_the_wear():
    plan = pd.DataFrame(
        {
            "planned": [Signal.CHARGE.value, Signal.EXPORT.value],
            "dam_mwh": [18.0, 90.0],
        }
    )
    needed = required_export_mwh(plan, LEGACY, efficiency=0.9)
    assert needed == pytest.approx(18.0 / 0.9 + 100.0, abs=0.01)


def test_a_day_whose_best_export_cannot_pay_the_wear_never_charges():
    flat = pd.DataFrame(
        {
            "planned": [Signal.CHARGE.value, Signal.EXPORT.value],
            "signal": [Signal.CHARGE.value, Signal.EXPORT.value],
            "reason": ["", ""],
            "dam_mwh": [20.0, 40.0],
            "observed_mwh": [20.0, 40.0],
        }
    )
    gated = gate_plan(flat, LEGACY)
    assert list(gated["signal"]) == [Signal.HOLD.value, Signal.HOLD.value]
    assert all("skip the cycle" in note for note in gated["wear_gate"])


def test_a_taken_cycle_keeps_the_exports_that_beat_the_wear():
    day = pd.DataFrame(
        {
            "planned": [Signal.CHARGE.value] + [Signal.EXPORT.value] * 2,
            "signal": [Signal.CHARGE.value] + [Signal.EXPORT.value] * 2,
            "reason": [""] * 3,
            "dam_mwh": [20.0, 60.0, 900.0],
            "observed_mwh": [20.0, 60.0, 900.0],
        }
    )
    gated = gate_plan(day, LEGACY)
    # The cycle pays at $900, so it is taken; the $60 export is under the $100/MWh
    # wear cost, so that energy is held for the interval that pays.
    assert list(gated["signal"]) == [
        Signal.CHARGE.value,
        Signal.HOLD.value,
        Signal.EXPORT.value,
    ]
    assert "wear cost" in gated.loc[1, "reason"]


def test_the_gate_never_reads_a_price_before_it_settles():
    """Only the day-ahead hour and the last settled print may decide an export."""
    trace = holdout.load_holdout()[0]
    plan = plan_for(trace)
    lied_to = plan.copy()
    # Scramble every interval's own real-time print; the gate must not notice.
    lied_to["spp"] = lied_to["spp"].astype(float).iloc[::-1].to_numpy()
    assert list(gate_plan(plan, LEGACY)["signal"]) == list(gate_plan(lied_to, LEGACY)["signal"])


def test_gating_only_ever_removes_dispatch():
    for trace in holdout.load_holdout():
        plan = plan_for(trace)
        gated = gate_plan(plan, LEGACY)
        for before, after in zip(plan["signal"], gated["signal"], strict=True):
            assert after in (before, Signal.HOLD.value)


def test_a_skipped_cycle_is_a_cycle_saved_and_costs_no_wear():
    quiet = min(holdout.load_holdout(), key=lambda t: t.peak_mwh)
    ungated = degradation.score_day(quiet, LEGACY, gated=False)
    gated = degradation.score_day(quiet, LEGACY, gated=True)
    assert gated.cycles < ungated.cycles
    assert gated.wear_usd <= ungated.wear_usd


def test_net_dollars_are_gross_minus_wear():
    result = degradation.score_day(holdout.load_holdout()[0], BASE_CORE, gated=True)
    assert result.net_uplift_usd == pytest.approx(
        result.gross_uplift_usd - result.wear_usd, abs=0.01
    )


def test_both_splits_are_scored_for_both_battery_types():
    results = degradation.evaluate_all()
    assert {(r.split, r.model.unit_type) for r in results} == {
        (split, model.unit_type) for split in ("tuning", "held-out") for model in WEAR_MODELS
    }
    for result in results:
        assert result.days == len(result.gated) == len(result.ungated) > 0
        assert result.cycles_saved == pytest.approx(result.cycles - result.gated_cycles, abs=0.01)


def test_the_expensive_pack_is_where_the_gate_pays():
    """The honest per-type finding, and the reason the report is split by type."""
    legacy = degradation.evaluate("held-out", LEGACY)
    core = degradation.evaluate("held-out", BASE_CORE)
    assert legacy.gated_net_usd > legacy.net_usd
    assert legacy.cycles_saved > core.cycles_saved
    assert legacy.wear_usd > core.wear_usd


def test_the_report_prints_the_assumption_and_both_splits():
    text = degradation.report()
    assert "assumption" in text
    for split in ("tuning", "held-out"):
        assert split in text
    for model in WEAR_MODELS:
        assert model.label in text
    assert "cycles" in text and "saved" in text


def test_the_ungated_score_matches_the_published_holdout_policy():
    """Scoring with wear switched off must not quietly change the frozen policy."""
    trace = holdout.load_holdout()[0]
    published = holdout.score_day(trace, kwh=LEGACY.usable_kwh)
    ungated = degradation.score_day(trace, LEGACY, gated=False)
    assert ungated.gross_uplift_usd == pytest.approx(published.uplift_usd, abs=0.01)


def test_readme_wear_numbers_come_from_the_code():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    for model in WEAR_MODELS:
        assert f"**${model.wear_usd_per_mwh:,.2f}/MWh** of throughput" in readme
    for result in degradation.evaluate_all():
        legacy = result.model.unit_type is UnitType.LEGACY
        row = (
            f"| {result.split} | {'legacy' if legacy else 'Base Core-style'} "
            f"| {result.gross_usd:+,.2f} | {result.wear_usd:,.2f} | {result.net_usd:+,.2f} "
            f"| **{result.gated_net_usd:+,.2f}** | {result.cycles:,.2f} "
            f"| {result.gated_cycles:,.2f} | {result.cycles_saved:,.2f} |"
        )
        assert row in readme


def test_default_wear_model_matches_the_backtest_battery():
    assert LEGACY.usable_kwh == backtest.DEFAULT_KWH
    assert LEGACY.power_kw == backtest.DEFAULT_POWER_KW
