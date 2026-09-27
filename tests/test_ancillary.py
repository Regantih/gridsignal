"""Energy/ancillary co-optimization: real ERCOT capacity prices, simulated battery."""

from __future__ import annotations

import pandas as pd
import pytest

from gridsignal import ancillary, holdout, ingest
from gridsignal.prices import load_scenario


@pytest.fixture(scope="module")
def scarcity():
    return load_scenario("scarcity")


def test_every_bundled_day_has_real_ancillary_prices_with_provenance():
    """The prices are ERCOT's; the sidecar has to say so for each bundled day."""
    for path in (
        *holdout.holdout_paths(),
        *holdout.tuning_paths(),
    ):
        meta = ancillary.as_provenance(path)
        assert meta["market"] == ingest.AS_MARKET
        assert meta["source"].startswith("https://www.ercot.com/")
        assert meta["units"] == "$/MW per hour of capacity held"
        frame = ancillary.load_as_prices(path)
        assert len(frame) == 24
        assert set(ingest.AS_PRODUCTS) <= set(frame.columns)
        assert (frame[list(ingest.AS_PRODUCTS)] >= 0).all().all()


def test_co_optimization_never_sells_the_member_backup(scarcity):
    """Every discharge award has to be sustainable above reserve for its full duration."""
    for battery in (ancillary.LEGACY, ancillary.BASE_CORE):
        day = ancillary.co_optimize(scarcity, battery)
        ledger = ancillary._hour_rows(ancillary._energy_ledger(scarcity, battery))
        floor = battery.reserve_kwh
        for award in day.awards:
            product = next(p for p in ancillary.PRODUCTS if p.key == award.product)
            if product.direction != "discharge":
                continue
            hour = ledger[ledger["hour"] == award.hour]
            worst_soc = float(hour["signal_soc_kwh"].astype(float).min())
            # Delivering the award for its full duration must not eat into the reserve.
            assert worst_soc - award.kw * product.sustain_h >= floor - 1e-6
        assert day.reserve_violations == 0


def test_only_the_products_the_ader_pilot_allows_are_offered(scarcity):
    """Phase 3.3 lets an aggregation of home batteries sell ECRS and Non-Spin, nothing else."""
    assert ancillary.ADER_PILOT.products == ("ecrs", "nonspin")
    for battery in (ancillary.LEGACY, ancillary.BASE_CORE):
        day = ancillary.co_optimize(scarcity, battery)
        assert day.rules == ancillary.ADER_PILOT.name
        assert {a.product for a in day.awards} <= set(ancillary.ADER_PILOT.products)
    unrestricted = ancillary.co_optimize(
        scarcity, ancillary.BASE_CORE, rules=ancillary.ALL_PRODUCTS
    )
    assert {a.product for a in unrestricted.awards} - set(ancillary.ADER_PILOT.products)


def test_no_offer_exceeds_the_per_qse_mw_cap_shared_across_the_fleet(scarcity):
    """100 MW system-wide per product, no QSE above 90% of it, split over the fleet."""
    caps = ancillary.pilot_cap_kw()
    assert caps == {"ecrs": 9.0, "nonspin": 9.0}
    for devices in (1_000, 10_000):
        day = ancillary.co_optimize(scarcity, ancillary.BASE_CORE, devices=devices)
        cap_kw = ancillary.ADER_PILOT.per_battery_kw("ecrs", devices)
        assert all(a.kw <= cap_kw + 1e-9 for a in day.awards)
        fleet_mw = max((a.kw for a in day.awards), default=0.0) * devices / 1000.0
        assert fleet_mw <= ancillary.ADER_PILOT.qse_cap_mw("ecrs") + 1e-6


def test_the_pilot_caps_cost_most_of_the_unrestricted_value():
    """The honest finding: the product that pays is the one an ADER may not offer."""
    restricted = ancillary.holdout_summary(ancillary.BASE_CORE)
    unrestricted = ancillary.holdout_summary(ancillary.BASE_CORE, rules=ancillary.ALL_PRODUCTS)
    assert restricted.median_uplift_usd < unrestricted.median_uplift_usd
    assert restricted.by_product["regdn"] == 0.0
    assert unrestricted.by_product["regdn"] > 0.5 * sum(unrestricted.by_product.values())


def test_the_day_ercot_reserves_cleared_high_is_rescored_inside_the_rules():
    """2024-05-08 carried the old headline; under the pilot rules it is ECRS and Non-Spin."""
    summary = ancillary.holdout_summary(ancillary.BASE_CORE)
    day = next(d for d in summary.per_day if d.date == "2024-05-08")
    assert {a.product for a in day.awards} <= {"ecrs", "nonspin"}
    assert day.by_product["ecrs"] > 0
    assert day.uplift_usd > summary.median_uplift_usd
    assert summary.top_day_share[0] == "2024-05-08"


def test_awards_respect_the_inverter(scarcity):
    for battery in (ancillary.LEGACY, ancillary.BASE_CORE):
        day = ancillary.co_optimize(scarcity, battery)
        assert day.awards
        assert all(a.kw <= battery.power_kw + 1e-9 for a in day.awards)
        assert all(a.price_mw_h > 0 for a in day.awards)


def test_only_one_product_is_sold_per_hour(scarcity):
    """The same kW cannot be promised to two products in the same hour."""
    day = ancillary.co_optimize(scarcity, ancillary.BASE_CORE)
    hours = [a.hour for a in day.awards]
    assert len(hours) == len(set(hours))


def test_ancillary_only_adds_value_on_top_of_the_frozen_energy_policy(scarcity):
    day = ancillary.co_optimize(scarcity, ancillary.BASE_CORE)
    assert day.ancillary_usd > 0
    assert day.total_usd == round(day.energy_usd + day.ancillary_usd, 2)
    assert day.uplift_usd == round(day.total_usd - day.energy_only_usd, 2)
    assert day.uplift_usd > 0


def test_capacity_is_not_sold_when_the_battery_is_empty(scarcity):
    """An empty battery has nothing to hold in reserve, so it cannot sell reserve."""
    empty = pd.DataFrame(
        {
            "interval_start": pd.date_range("2023-09-06", periods=24, freq="h"),
            **{product: [500.0] * 24 for product in ingest.AS_PRODUCTS},
        }
    )
    flat = ancillary.Battery("Empty unit", kwh=0.0, power_kw=5.0)
    day = ancillary.co_optimize(scarcity, flat, as_prices=empty)
    assert day.awards == ()
    assert day.ancillary_usd == 0.0


def test_a_product_is_only_bid_when_its_duration_can_be_met():
    """Non-Spin needs four hours of sustained output; 1 kWh of headroom cannot back it."""
    battery = ancillary.Battery("Tiny unit", kwh=5.0, power_kw=5.0)
    row = pd.Series({"regup": 0.0, "rrs": 0.0, "ecrs": 0.0, "nonspin": 100.0, "regdn": 0.0})
    best = ancillary._best_product(
        row, battery, sellable_kwh=4.0, headroom_kwh=0.0, available_kw=5.0, devices=1
    )
    assert best is not None
    product, kw, _ = best
    assert product.key == "nonspin"
    assert kw == pytest.approx(1.0)


def test_held_out_days_are_scored_with_the_same_frozen_policy():
    summary = ancillary.holdout_summary(ancillary.BASE_CORE)
    assert summary.days == len(holdout.holdout_paths())
    assert summary.reserve_violations == 0
    assert summary.ancillary_usd > 0
    assert summary.mean_uplift_usd == round(summary.uplift_usd / summary.days, 2)
    assert summary.fleet_usd(10_000) == round(summary.mean_uplift_usd * 10_000, 2)


def test_co_optimization_is_deterministic(scarcity):
    first = ancillary.co_optimize(scarcity, ancillary.BASE_CORE)
    second = ancillary.co_optimize(scarcity, ancillary.BASE_CORE)
    assert first == second


def test_cli_reports_the_source_and_the_split(capsys):
    assert ancillary.main() == 0
    out = capsys.readouterr().out
    assert "ercot.com/mktrules/pilots/ader" in out
    assert "backup reserve violations: 0" in out
    assert "Offers restricted to the ERCOT ADER pilot" in out
    assert "Comparison only, NOT available to an ADER" in out


def test_the_mean_is_reported_next_to_the_median_it_hides():
    """One rare day carries the headline, so the median has to travel with it."""
    summary = ancillary.holdout_summary(ancillary.BASE_CORE)
    per_day = sorted(d.uplift_usd for d in summary.per_day)
    assert len(summary.per_day) == summary.days
    assert summary.median_uplift_usd == pytest.approx(per_day[len(per_day) // 2], abs=0.01)
    assert summary.median_uplift_usd < summary.mean_uplift_usd


def test_the_headline_leads_with_the_median_and_labels_the_unrestricted_number():
    summary = ancillary.holdout_summary(ancillary.BASE_CORE)
    unrestricted = ancillary.holdout_summary(ancillary.BASE_CORE, rules=ancillary.ALL_PRODUCTS)
    date, share = summary.top_day_share
    assert share > 0.5
    text = ancillary.headline(summary, unrestricted)
    assert date in text
    median = f"${summary.median_uplift_usd:,.2f}"
    mean = f"${summary.mean_uplift_usd:,.2f}"
    assert text.index(median) < text.index(mean)
    assert "rare-day money" in text
    assert text.index("ADER pilot rules") < text.index("all five products")
    assert f"${unrestricted.median_uplift_usd:,.2f}" in text


def test_a_median_of_an_even_number_of_days_averages_the_middle_pair():
    days = ancillary.evaluate(holdout.load_holdout()[:4], ancillary.BASE_CORE)
    middle = sorted(d.uplift_usd for d in days.per_day)[1:3]
    assert days.median_uplift_usd == pytest.approx(sum(middle) / 2, abs=0.01)


def test_a_missing_procurement_plan_is_reported_not_assumed(monkeypatch, tmp_path):
    """ERCOT drops the AS plan after about a month; silence must not read as 'small'."""
    monkeypatch.setattr(ancillary, "AS_PLAN_DIR", tmp_path)
    assert ancillary.load_as_plan("2024-05-08") is None
    assert ancillary.procurement_flag() is None
    out = "\n".join(ancillary.lines())
    assert "Price-taker assumption unchecked" in out


def test_the_fleet_offer_is_flagged_against_what_ercot_procures():
    flag = ancillary.procurement_flag()
    assert flag is not None, "no bundled day has a cached AS plan; run scripts/fetch_as_plan.py"
    assert flag.fleet_mw == pytest.approx(
        ancillary.FLEET_DEVICES * ancillary.BASE_CORE.power_kw / 1000.0, abs=1.0
    )
    assert flag.procured_mw > 0
    assert flag.share > ancillary.PRICE_TAKER_SHARE
    assert not flag.price_taker_credible


def test_a_small_fleet_can_still_call_itself_a_price_taker():
    flag = ancillary.procurement_flag(devices=100)
    assert flag is not None
    assert flag.price_taker_credible


def test_cli_prints_the_per_day_split_and_the_price_taker_check(capsys):
    assert ancillary.main() == 0
    out = capsys.readouterr().out
    assert "uplift per held-out day" in out
    assert "median day" in out
    assert "Pilot registration check" in out
    assert "Price-taker check on the unrestricted comparison" in out
    assert "upper bound" in out
