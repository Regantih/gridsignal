"""Base's two business models: the comparison is arithmetic, and the guard is not free."""

from __future__ import annotations

import pandas as pd
import pytest

from gridsignal import ancillary, business, holdout
from gridsignal.signals import Signal


@pytest.fixture(scope="module")
def result() -> business.Comparison:
    return business.compare()


def test_the_comparison_is_deterministic() -> None:
    assert business.compare() == business.compare()


def test_both_models_score_the_same_days_and_the_same_battery(
    result: business.Comparison,
) -> None:
    dates = [trace.date for trace in holdout.load_holdout()]
    assert [day.date for day, _ in result.per_day] == dates
    assert [called.date for _, called in result.per_day] == dates
    assert result.retail.days == result.partner.days == len(dates)
    for day, called in result.per_day:
        assert day.reserve_kwh == called.reserve_kwh == ancillary.BASE_CORE.reserve_kwh


def test_the_utility_call_ignores_the_price_and_runs_on_the_clock() -> None:
    schedules = []
    for trace in holdout.load_holdout():
        called = business.utility_call(business.price_plan(trace))
        hours = pd.to_datetime(called["interval_start"]).dt.hour
        exports = called["signal"] == Signal.EXPORT.value
        charges = called["signal"] == Signal.CHARGE.value
        assert set(hours[exports]) == set(business.UTILITY_EXPORT_HOURS)
        assert set(hours[charges]) == set(business.UTILITY_CHARGE_HOURS)
        schedules.append(tuple(zip(hours, called["signal"], strict=True)))
    assert len(set(schedules)) == 1, "the clock is the same every day, whatever prices do"


def test_the_partner_model_pays_base_nothing_until_a_fee_is_agreed(
    result: business.Comparison,
) -> None:
    """Base sells access, not energy, so every market dollar belongs to the utility."""
    for _, called in result.per_day:
        assert called.base_usd == 0.0
        assert called.capacity_usd == 0.0
        assert called.supply_margin_usd == 0.0
    assert result.partner.base_median_usd == 0.0


def test_the_retail_model_earns_the_market_plus_the_margin_it_does_not_cannibalise(
    result: business.Comparison,
) -> None:
    for day, _ in result.per_day:
        expected = (
            day.energy_usd + day.capacity_usd + day.supply_margin_usd - day.self_supply_cost_usd
        )
        assert day.base_usd == pytest.approx(expected, abs=1e-4)
        assert day.market_usd == pytest.approx(day.energy_usd + day.capacity_usd, abs=1e-4)
        assert day.self_supply_cost_usd >= 0.0, "serving the house costs Base retail margin"


def test_only_the_retail_model_sells_grid_services(result: business.Comparison) -> None:
    """The utility's QSE bids the pilot products in the partner model, not Base."""
    assert result.retail.capacity_median_usd > 0.0
    assert result.partner.capacity_median_usd == 0.0


def test_calling_the_battery_on_the_clock_leaves_market_value_behind(
    result: business.Comparison,
) -> None:
    assert result.certainty_cost_usd > 0.0
    assert result.retail.market_median_usd > result.partner.market_median_usd
    losing = [called for _, called in result.per_day if called.market_usd < 0]
    assert losing, "a clock that ignores price sometimes charges into an expensive hour"


def test_the_break_even_fee_is_the_retail_earnings_it_has_to_replace(
    result: business.Comparison,
) -> None:
    assert result.break_even_month_usd == pytest.approx(
        result.retail.base_median_usd * business.DAYS_PER_MONTH, abs=0.01
    )
    assert result.break_even_battery_month_usd == pytest.approx(
        result.retail.market_median_usd * business.DAYS_PER_MONTH, abs=0.01
    )
    assert result.break_even_battery_month_mean_usd == pytest.approx(
        result.retail.market_mean_usd * business.DAYS_PER_MONTH, abs=0.01
    )
    assert result.break_even_battery_month_mean_usd > result.break_even_battery_month_usd, (
        "one scarcity day carries the mean, which is why the median is published first"
    )
    assert result.break_even_kw_month_usd == pytest.approx(
        result.break_even_battery_month_usd / result.registered_kw, abs=0.01
    )
    assert result.registered_kw == pytest.approx(
        ancillary.ADER_PILOT.per_battery_kw("ecrs", ancillary.FLEET_DEVICES), abs=1e-6
    )
    assert result.break_even_month_usd > result.break_even_battery_month_usd, (
        "the retail relationship is worth more than the battery alone"
    )


def test_the_backup_promise_holds_in_both_models(result: business.Comparison) -> None:
    for day, called in result.per_day:
        assert day.reserve_spent_kwh == 0.0
        assert called.reserve_spent_kwh == 0.0
        assert day.reserve_held and called.reserve_held
    assert result.retail.reserve_breaches == 0
    assert result.partner.reserve_breaches == 0


def test_the_backup_promise_is_not_true_by_construction(result: business.Comparison) -> None:
    """Remove the floor and the utility's call eats the member's backup. That is the proof."""
    assert result.unclamped_breaches == result.retail.days
    breached = [
        business.unclamped_breach(trace, ancillary.BASE_CORE) for trace in holdout.load_holdout()
    ]
    assert all(breached)


def test_reserve_spent_counts_only_energy_taken_from_under_the_floor() -> None:
    """Starting empty is not a breach; discharging below the line is."""
    empty_then_charged = pd.DataFrame({"signal_soc_kwh": [0.0, 4.0, 9.0, 12.0]})
    assert business.reserve_spent_kwh(empty_then_charged, 8.0) == pytest.approx(0.0)
    drained = pd.DataFrame({"signal_soc_kwh": [12.0, 10.0, 6.0, 3.0]})
    assert business.reserve_spent_kwh(drained, 8.0) == pytest.approx(5.0)


def test_every_assumption_names_a_source_and_says_whether_it_is_published() -> None:
    modelled = [item for item in business.ASSUMPTIONS if not item.published]
    published = [item for item in business.ASSUMPTIONS if item.published]
    assert modelled and published
    for item in business.ASSUMPTIONS:
        assert item.url.startswith("https://")
        assert item.value and item.source
    for item in modelled:
        assert item.provenance.startswith("modelled by this repo")
    for item in published:
        assert not item.provenance.startswith("modelled")
    assert any(item.url == business.COO_INTERVIEW_URL for item in published), (
        "the two models come from the public interview, and it is cited"
    )
    assert any(item.url == ancillary.ADER_PILOT_URL for item in published)
    assert any("4CP" in item.value or "4CP" in item.source for item in business.ASSUMPTIONS)


def test_the_modelled_numbers_do_not_cite_documents_that_do_not_contain_them() -> None:
    """A link beside a number is read as its source, so it has to be one."""
    rate = next(item for item in business.ASSUMPTIONS if item.name == "retail rate")
    assert rate.url != business.COO_INTERVIEW_URL, "the interview quotes no retail rate"
    assert "modelled assumption" in rate.source

    floor = next(item for item in business.ASSUMPTIONS if item.name == "backup")
    assert floor.url != ancillary.ADER_PILOT_URL, "the ADER pilot sets no member reserve"
    assert "modelled assumption" in floor.source


def test_the_battery_size_is_never_claimed_as_an_official_specification() -> None:
    sized = next(item for item in business.ASSUMPTIONS if item.name == "battery")
    assert not sized.published
    assert "not an official specification" in sized.source


def test_control_is_answered_differently_by_the_two_models() -> None:
    for line in business.CONTROL:
        assert line.retail != line.partner
    answers = {line.question: line for line in business.CONTROL}
    assert "utility" in answers["who picks the dispatch hour"].partner
    assert "Base" in answers["who enforces the backup promise"].partner, (
        "the backup floor is Base's either way"
    )


def test_the_cli_prints_the_computed_numbers_and_labels_the_simulation(
    capsys: pytest.CaptureFixture[str], result: business.Comparison
) -> None:
    business.main()
    out = capsys.readouterr().out
    assert "simulated" in out
    assert f"${result.break_even_month_usd:,.2f} per battery per month" in out
    assert f"${result.break_even_battery_month_usd:,.2f} per battery per month" in out
    assert out.index(f"${result.break_even_battery_month_usd:,.2f}") < out.index(
        f"${result.break_even_battery_month_mean_usd:,.2f}"
    ), "the median fee is the headline and the mean is the second reading"
    assert f"${result.certainty_cost_usd:,.2f} per battery per day" in out
    assert f"{result.unclamped_breaches} of {result.retail.days} days" in out
    for item in business.ASSUMPTIONS:
        assert item.url in out
