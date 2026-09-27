"""Congestion analysis: basis arithmetic, zone-aware timing and the placement sketch.

Every test here runs offline against either a synthetic frame or the bundled Parquet
traces. No key, no network.
"""

from __future__ import annotations

import pandas as pd
import pytest

from gridsignal import congestion


def _synthetic() -> pd.DataFrame:
    """Two zones, the hub and West, over four 15-minute intervals of one day."""
    starts = pd.date_range("2024-07-01 16:00", periods=4, freq="15min")
    rows = []
    prices = {
        "LZ_HOUSTON": [100.0, 200.0, 50.0, 400.0],
        "LZ_WEST": [10.0, 20.0, 40.0, 30.0],
        "HB_HUBAVG": [60.0, 110.0, 45.0, 215.0],
    }
    for location, series in prices.items():
        for start, spp in zip(starts, series, strict=True):
            rows.append(
                {
                    "interval_start": start,
                    "interval_end": start + pd.Timedelta(minutes=15),
                    "location": location,
                    "spp": spp,
                    "date": "2024-07-01",
                }
            )
    return pd.DataFrame(rows)


def test_basis_is_exact_subtraction() -> None:
    basis = congestion.basis_frame(_synthetic())
    houston = basis[basis["location"] == "LZ_HOUSTON"].reset_index(drop=True)
    assert list(houston["hub_basis"]) == [40.0, 90.0, 5.0, 185.0]
    # West-to-load-center spread: the metro price minus West Texas.
    assert list(houston["west_basis"]) == [90.0, 180.0, 10.0, 370.0]


def test_west_row_has_zero_west_basis() -> None:
    basis = congestion.basis_frame(_synthetic())
    west = basis[basis["location"] == "LZ_WEST"]
    assert set(west["west_basis"]) == {0.0}


def test_heatmap_averages_by_zone_and_hour() -> None:
    basis = congestion.basis_frame(_synthetic())
    grid = congestion.heatmap(basis)
    # All four intervals fall in hour 16, so the cell is the mean of the four.
    assert grid.loc["LZ_HOUSTON", 16] == pytest.approx(80.0)
    assert grid.loc["LZ_WEST", 16] == pytest.approx((-50.0 - 90.0 - 5.0 - 185.0) / 4)


def test_west_spread_only_covers_load_centers() -> None:
    spread = congestion.west_spread(congestion.basis_frame(_synthetic()))
    assert list(spread.columns) == ["LZ_HOUSTON"]
    assert spread.loc[16, "LZ_HOUSTON"] == pytest.approx(162.5)


def test_widest_hour_picks_the_largest_mean_basis() -> None:
    basis = congestion.basis_frame(_synthetic())
    widest = congestion.widest_hours(basis, top=1)[0]
    assert widest.location == "LZ_HOUSTON"
    assert widest.hour == 16
    assert widest.metro == "Houston"
    assert widest.days == 1


def test_zone_timed_beats_zone_blind_when_the_hub_misranks_the_peak() -> None:
    """The hub's dearest interval is not the zone's, so local timing wins."""
    starts = pd.date_range("2024-07-01 00:00", periods=32, freq="15min")
    zone = [20.0] * 32
    hub = [20.0] * 32
    # Zone spikes late; the hub thinks the earlier interval is the expensive one.
    zone[20:32] = [500.0] * 12
    hub[8:20] = [500.0] * 12
    rows = []
    for location, series in (("LZ_HOUSTON", zone), ("HB_HUBAVG", hub), ("LZ_WEST", [20.0] * 32)):
        for start, spp in zip(starts, series, strict=True):
            rows.append(
                {
                    "interval_start": start,
                    "interval_end": start + pd.Timedelta(minutes=15),
                    "location": location,
                    "spp": spp,
                    "date": "2024-07-01",
                }
            )
    basis = congestion.basis_frame(pd.DataFrame(rows))
    day = congestion.zone_day(basis, "2024-07-01", "LZ_HOUSTON")
    assert day.zone_timed_usd > day.zone_blind_usd
    assert day.uplift_usd == round(day.zone_timed_usd - day.zone_blind_usd, 2)


def test_zone_day_rejects_a_zone_with_no_prices() -> None:
    basis = congestion.basis_frame(_synthetic())
    with pytest.raises(KeyError):
        congestion.zone_day(basis, "2024-07-01", "LZ_CPS")


def _ranks() -> list[congestion.PlacementRank]:
    return [
        congestion.PlacementRank("LZ_HOUSTON", "Houston", 8.0, 1_000, 5.0),
        congestion.PlacementRank("LZ_NORTH", "Dallas-Fort Worth", 6.0, 2_000, 3.0),
        congestion.PlacementRank("LZ_SOUTH", "South Texas", 0.0, 0, -2.0),
    ]


def test_marginal_value_falls_as_a_zone_saturates() -> None:
    rank = _ranks()[0]
    assert rank.marginal_usd(0) == pytest.approx(8.0)
    assert rank.marginal_usd(500) == pytest.approx(4.0)
    assert rank.marginal_usd(1_000) == pytest.approx(0.0)
    assert rank.marginal_usd(5_000) == pytest.approx(0.0)


def test_placement_prefers_the_best_zone_then_moves_on() -> None:
    sketch = congestion.placement_sketch(1_000, ranks=_ranks(), block=100)
    placed = dict(zip(sketch["zone"], sketch["batteries placed"], strict=True))
    assert placed["LZ_HOUSTON"] > 0
    assert placed["LZ_NORTH"] > 0
    # A zone with no congestion premium never receives a battery.
    assert "LZ_SOUTH" not in placed
    assert sum(placed.values()) == 1_000
    houston = sketch[sketch["zone"] == "LZ_HOUSTON"].iloc[0]
    assert (
        houston["last $/battery/day (hindsight-timed)"]
        < houston["first $/battery/day (hindsight-timed)"]
    )
    assert sketch.attrs["unplaced"] == 0


def test_placement_stops_when_every_zone_is_saturated() -> None:
    sketch = congestion.placement_sketch(100_000, ranks=_ranks(), block=1_000)
    assert sketch["batteries placed"].sum() == 3_000
    assert sketch.attrs["unplaced"] == 97_000


def test_placement_ranks_sort_by_value_and_drop_unsaturable_zones_last() -> None:
    ranks = congestion.placement_ranks(
        [
            congestion.ZoneUplift("LZ_SOUTH", "South Texas", 2, 9.0, 8.0, -1.0, 4.0, 1),
            congestion.ZoneUplift("LZ_HOUSTON", "Houston", 2, 5.0, 4.0, 2.0, 40.0, 2),
        ]
    )
    assert [r.location for r in ranks] == ["LZ_HOUSTON", "LZ_SOUTH"]
    assert ranks[0].saturation_batteries > 0
    # A zone that never priced above the hub is assumed already saturated.
    assert ranks[1].saturation_batteries == 0


# --------------------------------------------------------------- bundled data


def test_bundled_days_are_present_with_provenance() -> None:
    days = congestion.bundled_days()
    assert len(days) >= 10
    for day in days:
        meta = congestion.provenance(day)
        assert set(meta) >= {"market", "location", "date", "source", "fetched_at"}
        assert meta["market"] == "REAL_TIME_15_MIN"
        assert congestion.HUB_AVERAGE in meta["location"]


def test_bundled_day_covers_every_zone_and_the_hub_offline() -> None:
    frame = congestion.load_day(congestion.bundled_days()[-1])
    assert set(frame["location"]) == {*congestion.ZONES, congestion.HUB_AVERAGE}
    assert frame["interval_start"].nunique() == 96


def test_dispatch_order_covers_every_bundled_zone() -> None:
    basis = congestion.basis_frame(congestion.load_days(congestion.bundled_days()[:2]))
    order = congestion.dispatch_order(basis)
    assert set(order) == set(congestion.ZONES)
    uplifts = {u.location: u.uplift_usd for u in congestion.zone_uplift(basis)}
    assert order == sorted(order, key=lambda z: uplifts[z], reverse=True)


def test_summary_headline_states_only_bundled_facts() -> None:
    basis = congestion.basis_frame(congestion.load_days(congestion.bundled_days()[:2]))
    summary = congestion.summarize(basis)
    assert summary.days == 2
    assert summary.zones == len(congestion.ZONES)
    assert f"{summary.days} bundled ERCOT days" in summary.headline
    assert summary.widest.location in summary.headline
    assert 0.0 <= summary.divergent_share <= 1.0


# ------------------------------------------------- hindsight labels and regimes


def test_the_headline_pairs_a_zones_gap_with_that_same_zones_uplift() -> None:
    """The widest gap and the uplift quoted next to it must be the same zone."""
    basis = congestion.basis_frame(congestion.load_days(congestion.bundled_days()[:3]))
    summary = congestion.summarize(basis)
    assert summary.widest_zone.location == summary.widest.location
    quoted = f"${summary.widest_zone.uplift_usd:,.2f}/battery/day"
    assert quoted in summary.headline
    others = [u for u in congestion.zone_uplift(basis) if u.location != summary.widest.location]
    assert summary.best.location not in summary.headline or summary.best.location == (
        summary.widest.location
    )
    assert all(o.location not in summary.headline for o in others)


def test_every_dollar_figure_is_labelled_hindsight_timed(capsys) -> None:
    """CLI headline, uplift table and placement sketch all carry the label."""
    congestion.main()
    out = capsys.readouterr().out
    assert congestion.HINDSIGHT_NOTE in out
    for line in out.splitlines():
        if "$/battery/day" in line or "$/day" in line:
            assert congestion.HINDSIGHT in line, line


def test_ordinary_and_scarcity_days_are_reported_apart() -> None:
    basis = congestion.basis_frame()
    uplift = congestion.zone_uplift(basis)[0]
    assert uplift.ordinary_days and uplift.scarcity_days
    assert uplift.ordinary_days + uplift.scarcity_days == uplift.days
    # The blended mean sits between the two regimes it is made of.
    low, high = sorted((uplift.ordinary_uplift_usd, uplift.scarcity_uplift_usd))
    assert low - 0.01 <= uplift.uplift_usd <= high + 0.01
    assert "ordinary days" in uplift.split_summary and "scarcity days" in uplift.split_summary


def test_the_placement_sketch_does_not_send_most_of_the_next_1000_to_one_zone() -> None:
    """The DEMO claim is checked against the sketch rather than asserted in prose."""
    sketch = congestion.placement_sketch(1000)
    top = sketch.iloc[0]
    assert top["batteries placed"] < 500, "a 'mostly one zone' claim would need >500 here"
    assert len(sketch) >= 5
