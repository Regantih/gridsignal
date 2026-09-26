"""The Why page states the case for the product, so it is the easiest place to lie.

These tests hold it to one rule: every number on the screen comes from the code that
produces it, recomputed on load. A figure typed into the prose, or a claim that quietly
keeps an old value after the engine changes, fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

from gridsignal import ancillary, deliverability_report, holdout, judgment_report, replay, why
from gridsignal.jev import evaluate

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "src" / "gridsignal" / "why.py").read_text(encoding="utf-8")
#: A dollar amount or a percentage written out in the source rather than computed.
TYPED_IN = re.compile(r"\$\s?\d|\d+(?:\.\d+)?\s?%(?!\})")


def test_no_dollar_figure_or_percentage_is_typed_into_the_page() -> None:
    """Prose may name a command; it may not name a number the code has to agree with."""
    prose = [
        line
        for line in SOURCE.splitlines()
        if TYPED_IN.search(line) and ":.0%" not in line and ":,.2f" not in line
    ]
    assert not prose, prose


def test_every_claim_carries_a_value_a_reason_and_a_way_to_reproduce_it() -> None:
    page = why.build(fleet_size=48)
    assert page.sections, "the page needs its three sections"
    for section in page.sections:
        assert section.lead.endswith(".")
        assert section.claims
        for claim in section.claims:
            assert claim.label and claim.value and claim.detail
            assert claim.command.startswith("python -m gridsignal.")
            assert any(char.isdigit() for char in claim.value), claim.label


def test_the_held_out_claim_is_the_number_the_held_out_run_produces() -> None:
    summary = holdout.summarize(holdout.evaluate())
    claim = _claim(why.evidence_section(fleet_size=48), "Held-out days")
    assert f"{summary.days_won} of {summary.days} days" in claim.value
    assert f"${summary.mean_uplift_usd:,.2f}" in claim.detail
    assert f"${summary.median_uplift_usd:,.2f}" in claim.detail


def test_the_recovery_claim_is_the_number_the_replay_produces() -> None:
    run = replay.run(fleet_size=48)
    claim = _claim(why.evidence_section(fleet_size=48), "Recovery at fleet scale")
    assert f"{run.recovered_share:.0%}" in claim.value
    # The spare headroom travels with the recovery, so "all of it came back" can never
    # be read as a guarantee.
    assert f"{run.spare_kw_at_fault:,.0f} kW of spare headroom" in claim.detail


def test_the_ancillary_claim_leads_with_the_median_and_names_the_day_carrying_it() -> None:
    split = ancillary.holdout_summary(why.ANCILLARY_UNIT)
    top_day, share = split.top_day_share
    claim = _claim(why.evidence_section(fleet_size=48), "Ancillary value")
    assert f"median ${split.median_uplift_usd:,.2f}" in claim.value
    assert f"${split.mean_uplift_usd:,.2f}" in claim.detail
    assert top_day in claim.detail and f"{share:.0%}" in claim.detail


def test_the_deliverability_claim_is_the_counterfactual_the_report_measures() -> None:
    report = deliverability_report.run()
    claim = _claim(why.approach_section(fleet_size=48), "proves an award")
    assert f"{report.undeliverable_awards} of {report.awards:,} awards" in claim.value
    assert f"{report.undeliverable_kw:,.2f} kW" in claim.detail


def test_the_model_is_presented_as_a_second_opinion_that_loses_to_the_rules() -> None:
    report = judgment_report.build()
    rules = report.blind[judgment_report.RULES]
    jev = report.blind[judgment_report.JEV]
    claim = _claim(why.evidence_section(fleet_size=48), "second opinion")
    assert f"rules (Jev offline) {rules.correct} of {rules.total}" in claim.value
    assert f"Jev {jev.correct} of {jev.total}" in claim.value
    assert rules.correct >= jev.correct, "if that ever flips, rewrite the claim"


def test_the_limits_say_the_tuning_did_not_move_the_held_out_half() -> None:
    cal = judgment_report.build().calibration
    moved = round((cal.after - cal.before) * cal.holdout)
    limits = "\n".join(why.limits(fleet_size=48))
    assert "did not meaningfully improve held-out agreement" in limits
    assert f"{moved:+d} episode of {cal.holdout}" in limits
    assert "overfitting" in limits


def test_the_limits_keep_the_simulation_and_price_taker_caveats() -> None:
    limits = "\n".join(why.limits(fleet_size=48)).lower()
    assert "simulated" in limits
    assert "price taker" in limits
    assert "assumption" in limits
    assert "base" in limits


def test_the_command_prints_the_same_page_the_screen_shows(capsys) -> None:  # type: ignore[no-untyped-def]
    assert why.main(["--devices", "48"]) == 0
    printed = capsys.readouterr().out
    for section in why.build(fleet_size=48).sections:
        for claim in section.claims:
            assert claim.value in printed


def test_the_page_is_built_once_per_process_so_the_timing_cannot_drift() -> None:
    """Rebuilding would re-time the mesh: slow on camera, and two different answers."""
    assert why.build(fleet_size=48) is why.build(fleet_size=48)


def _claim(section: why.Section, needle: str) -> why.Claim:
    return next(c for c in section.claims if needle.lower() in c.label.lower())


def test_the_page_says_what_the_recorded_jev_answers_actually_change() -> None:
    deltas = evaluate.fixture_deltas()
    claim = _claim(why.evidence_section(fleet_size=48), "switched off")

    # With no approve path the model cannot move a kW; it moves the explanation.
    assert deltas and not any(d.changed for d in deltas)
    assert f"{len(deltas)} of {len(deltas)}" in claim.value
    differing = sum(d.rules_root_cause != d.jev_root_cause for d in deltas)
    assert f"root cause differs in {differing}" in claim.detail
