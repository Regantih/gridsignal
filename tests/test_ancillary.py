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
        row, battery, sellable_kwh=4.0, headroom_kwh=0.0, available_kw=5.0
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
    assert "ercot.com" in out
    assert "backup reserve violations: 0" in out
    assert "Reg Down" in out
