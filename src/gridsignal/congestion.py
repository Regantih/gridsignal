"""Where the power was expensive, not just when: ERCOT zonal basis and what it is worth.

Texas generates a lot of its cheap energy in the west and consumes it in Houston,
Dallas, Austin and San Antonio. When the lines between them bind, the load zones stop
settling at the same price, and the gap between a zone and the ERCOT hub average — the
*basis* — is the part of the money that a single-zone price series cannot see.

This module reads the bundled all-zone real-time traces in ``data/zones/`` (public
ERCOT settlement point prices, one Parquet plus a provenance sidecar per trade day) and
answers three questions:

* :func:`basis_frame` — zone-minus-hub and zone-minus-west basis per 15-minute interval.
* :func:`zone_uplift` — what a battery in each zone earns by timing its discharge to
  that zone's own congested hours instead of the zone-blind hub ranking.
* :func:`placement_sketch` — a ranking of where the next batteries are worth most, with
  the marginal value falling as a zone fills up.

Everything here is a read-only analysis of a handful of historical days. The placement
ranking in particular is a **sketch, not a forecast**: it is arithmetic on the bundled
prices with an explicit saturation assumption, not a siting study.

**Every dollar figure in this module is hindsight-timed.** The discharge and charge hours
are picked by ranking a day's settled prices after that day is over, so these are ceilings
on what perfect timing in a zone was worth, not what a live policy earned. The causal
policy that decides before prices settle lives in :mod:`gridsignal.dam`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from gridsignal import backtest, paths
from gridsignal.insight import SCARCITY_PEAK_MWH
from gridsignal.signals import Signal

#: Stamped on every dollar figure here, because all of them rank prices after the fact.
HINDSIGHT = "hindsight-timed"
HINDSIGHT_NOTE = (
    "hindsight-timed: discharge hours are ranked on prices that had already settled, "
    "so every dollar figure is a ceiling on perfect timing, not a live result"
)

DATA_DIR = paths.DATA_DIR
ZONE_DIR = DATA_DIR / "zones"

#: ERCOT load zones, as published in the settlement point price reports.
ZONES: tuple[str, ...] = (
    "LZ_WEST",
    "LZ_NORTH",
    "LZ_HOUSTON",
    "LZ_SOUTH",
    "LZ_AEN",
    "LZ_CPS",
    "LZ_LCRA",
    "LZ_RAYBN",
)
#: The ERCOT hub average, used as the system-wide reference price.
HUB_AVERAGE = "HB_HUBAVG"
#: The generation-heavy western zone the other zones are compared against.
WEST = "LZ_WEST"
#: The four metros, in ERCOT's zone names. Dallas-Fort Worth settles in LZ_NORTH;
#: Austin splits across the city utility (LZ_AEN) and LZ_LCRA; San Antonio is LZ_CPS.
LOAD_CENTERS: tuple[str, ...] = ("LZ_HOUSTON", "LZ_NORTH", "LZ_AEN", "LZ_CPS", "LZ_LCRA")
METRO: dict[str, str] = {
    "LZ_HOUSTON": "Houston",
    "LZ_NORTH": "Dallas-Fort Worth",
    "LZ_AEN": "Austin (city)",
    "LZ_LCRA": "Austin (LCRA)",
    "LZ_CPS": "San Antonio",
    "LZ_SOUTH": "South Texas",
    "LZ_RAYBN": "Rayburn",
    "LZ_WEST": "West Texas",
}

#: How many hours a day the battery discharges into, and charges over, in the uplift
#: comparison. One cycle: 13.5 kWh out of a 5 kW inverter is under three hours.
DISCHARGE_HOURS = 3
CHARGE_HOURS = 4
INTERVALS_PER_HOUR = 4
#: Placement sketch: how much congested-hour discharge power a zone can absorb before
#: the basis it is being paid for is assumed gone, per dollar of mean positive basis.
#: A deliberately blunt assumption, stated so it can be argued with.
RELIEF_MW_PER_DOLLAR = 8.0
BATTERY_KW = backtest.DEFAULT_POWER_KW


def zone_path_for(date: str) -> Path:
    return ZONE_DIR / f"zones_rtm_spp_{date.replace('-', '')}.parquet"


def bundled_days() -> list[str]:
    """Trade dates with a bundled all-zone trace, oldest first."""
    return sorted(p.stem.split("_")[-1] for p in ZONE_DIR.glob("zones_rtm_spp_*.parquet"))


def _as_date(stamp: str) -> str:
    return f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}" if "-" not in stamp else stamp


def provenance(date: str) -> dict:
    """The sidecar describing where one day's all-zone trace came from."""
    path = zone_path_for(date).with_suffix(".json")
    return json.loads(path.read_text()) if path.exists() else {}


def load_day(date: str) -> pd.DataFrame:
    """One trade day, tidy as ``interval_start``/``interval_end``/``location``/``spp``."""
    path = zone_path_for(date)
    if not path.exists():
        raise FileNotFoundError(f"no bundled all-zone trace for {date}; run scripts/fetch_zones.py")
    frame = pd.read_parquet(path)
    for column in ("interval_start", "interval_end"):
        frame[column] = pd.to_datetime(frame[column]).dt.tz_localize(None)
    frame["date"] = _as_date(date)
    return frame.sort_values(["interval_start", "location"]).reset_index(drop=True)


def load_days(dates: list[str] | None = None) -> pd.DataFrame:
    dates = dates if dates is not None else bundled_days()
    if not dates:
        raise FileNotFoundError("no bundled all-zone traces; run scripts/fetch_zones.py")
    return pd.concat([load_day(d) for d in dates], ignore_index=True)


def basis_frame(frame: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per-interval basis: each zone against the hub average and against West Texas.

    ``hub_basis`` is what the zone paid over the system reference; ``west_basis`` is
    what it paid over the generation-heavy west, which is the West-to-load-center
    spread congestion shows up in.
    """
    frame = frame if frame is not None else load_days()
    reference = frame[frame["location"].isin({HUB_AVERAGE, WEST})].pivot_table(
        index=["date", "interval_start"], columns="location", values="spp"
    )
    zones = frame[frame["location"].isin(ZONES)].merge(
        reference.reset_index(), on=["date", "interval_start"], how="left"
    )
    zones = zones.rename(columns={HUB_AVERAGE: "hub_spp", WEST: "west_spp"})
    zones["hub_basis"] = (zones["spp"] - zones["hub_spp"]).round(2)
    zones["west_basis"] = (zones["spp"] - zones["west_spp"]).round(2)
    zones["hour"] = zones["interval_start"].dt.hour
    zones["metro"] = zones["location"].map(METRO)
    return zones.sort_values(["date", "interval_start", "location"]).reset_index(drop=True)


def heatmap(basis: pd.DataFrame | None = None, column: str = "hub_basis") -> pd.DataFrame:
    """Mean basis by zone (rows) and hour of day (columns), averaged over bundled days."""
    basis = basis if basis is not None else basis_frame()
    return (
        basis.pivot_table(index="location", columns="hour", values=column, aggfunc="mean")
        .round(2)
        .reindex([z for z in ZONES if z in set(basis["location"])])
    )


@dataclass(frozen=True)
class WidestHour:
    """One hour of one zone, where the zone priced furthest above the reference."""

    location: str
    metro: str
    hour: int
    hub_basis: float
    west_basis: float
    days: int


def widest_hours(basis: pd.DataFrame | None = None, top: int = 10) -> list[WidestHour]:
    """The zone-hours with the widest mean basis across the bundled days."""
    basis = basis if basis is not None else basis_frame()
    grouped = (
        basis.groupby(["location", "hour"])
        .agg(
            hub_basis=("hub_basis", "mean"),
            west_basis=("west_basis", "mean"),
            days=("date", "nunique"),
        )
        .reset_index()
        .sort_values("hub_basis", ascending=False)
        .head(top)
    )
    return [
        WidestHour(
            location=row.location,
            metro=METRO.get(row.location, row.location),
            hour=int(row.hour),
            hub_basis=round(float(row.hub_basis), 2),
            west_basis=round(float(row.west_basis), 2),
            days=int(row.days),
        )
        for row in grouped.itertuples()
    ]


def west_spread(basis: pd.DataFrame | None = None) -> pd.DataFrame:
    """Mean West-to-load-center spread by hour: load-zone price minus ``LZ_WEST``.

    One row per hour of the day, one column per load-center zone. Positive means the
    metro paid more than West Texas in that hour, which is the direction a battery
    sitting in the metro is paid for.
    """
    basis = basis if basis is not None else basis_frame()
    centers = basis[basis["location"].isin(LOAD_CENTERS)]
    return (
        centers.pivot_table(index="hour", columns="location", values="west_basis", aggfunc="mean")
        .round(2)
        .reindex(columns=[z for z in LOAD_CENTERS if z in set(centers["location"])])
    )


def _plan(prices: pd.DataFrame, rank_by: pd.Series) -> pd.DataFrame:
    """Charge in the cheapest intervals of ``rank_by``, discharge in the dearest."""
    frame = prices.reset_index(drop=True)
    ranking = rank_by.reset_index(drop=True)
    plan = frame[["interval_start", "interval_end", "spp"]].copy()
    plan["signal"] = Signal.HOLD.value
    discharge = ranking.nlargest(DISCHARGE_HOURS * INTERVALS_PER_HOUR).index
    # Only charge before the first discharge interval, so the cycle is physical.
    first = min(discharge)
    charge = ranking.iloc[:first].nsmallest(CHARGE_HOURS * INTERVALS_PER_HOUR).index
    plan.loc[charge, "signal"] = Signal.CHARGE.value
    plan.loc[discharge, "signal"] = Signal.EXPORT.value
    return plan


@dataclass(frozen=True)
class ZoneDay:
    """What one battery in one zone earned on one day, timed two ways."""

    date: str
    location: str
    metro: str
    zone_timed_usd: float
    zone_blind_usd: float
    mean_hub_basis: float
    peak_hub_basis: float
    peak_mwh: float = 0.0

    @property
    def uplift_usd(self) -> float:
        return round(self.zone_timed_usd - self.zone_blind_usd, 2)

    @property
    def scarcity(self) -> bool:
        """A day whose zone price touched four figures behaves nothing like the rest."""
        return self.peak_mwh >= SCARCITY_PEAK_MWH


def zone_day(basis: pd.DataFrame, date: str, location: str) -> ZoneDay:
    """Settle a battery in one zone twice: timed locally, timed on the hub.

    Both plans are settled at the **zone's own** prices, which is what a battery there
    is actually paid. The only difference is which signal picked the hours: the zone's
    own settlement price, which carries its congestion, or the system-wide hub average
    a zone-blind operator watches.
    """
    day = basis[(basis["date"] == date) & (basis["location"] == location)].reset_index(drop=True)
    if day.empty:
        raise KeyError(f"no bundled prices for {location} on {date}")
    prices = day[["interval_start", "interval_end", "spp"]]

    timed = backtest.summarize(
        backtest.value_captured(_plan(prices, day["spp"].astype(float)), prices)
    )
    blind = backtest.summarize(
        backtest.value_captured(_plan(prices, day["hub_spp"].astype(float)), prices)
    )
    return ZoneDay(
        date=date,
        location=location,
        metro=METRO.get(location, location),
        zone_timed_usd=timed.signal_usd,
        zone_blind_usd=blind.signal_usd,
        mean_hub_basis=round(float(day["hub_basis"].mean()), 2),
        peak_hub_basis=round(float(day["hub_basis"].max()), 2),
        peak_mwh=round(float(day["spp"].max()), 2),
    )


@dataclass(frozen=True)
class ZoneUplift:
    """A zone across every bundled day, per battery per day."""

    location: str
    metro: str
    days: int
    zone_timed_usd: float
    zone_blind_usd: float
    mean_hub_basis: float
    peak_hub_basis: float
    win_days: int
    ordinary_days: int = 0
    scarcity_days: int = 0
    ordinary_uplift_usd: float = 0.0
    scarcity_uplift_usd: float = 0.0

    @property
    def uplift_usd(self) -> float:
        return round(self.zone_timed_usd - self.zone_blind_usd, 2)

    @property
    def win_rate(self) -> float:
        return round(self.win_days / self.days, 2) if self.days else 0.0

    @property
    def split_summary(self) -> str:
        """The two regimes stated apart, because their averages are nothing alike."""
        return (
            f"${self.ordinary_uplift_usd:,.2f} on {self.ordinary_days} ordinary days, "
            f"${self.scarcity_uplift_usd:,.2f} on {self.scarcity_days} scarcity days"
        )


def _mean_uplift(days: list[ZoneDay]) -> float:
    return round(sum(d.uplift_usd for d in days) / len(days), 2) if days else 0.0


def zone_uplift(basis: pd.DataFrame | None = None) -> list[ZoneUplift]:
    """Per-battery-per-day value of congestion-aware timing, zone by zone."""
    basis = basis if basis is not None else basis_frame()
    results = []
    for location in [z for z in ZONES if z in set(basis["location"])]:
        days = [zone_day(basis, d, location) for d in sorted(basis["date"].unique())]
        results.append(
            ZoneUplift(
                location=location,
                metro=METRO.get(location, location),
                days=len(days),
                zone_timed_usd=round(sum(d.zone_timed_usd for d in days) / len(days), 2),
                zone_blind_usd=round(sum(d.zone_blind_usd for d in days) / len(days), 2),
                mean_hub_basis=round(sum(d.mean_hub_basis for d in days) / len(days), 2),
                peak_hub_basis=round(max(d.peak_hub_basis for d in days), 2),
                win_days=sum(1 for d in days if d.uplift_usd > 0),
                ordinary_days=len([d for d in days if not d.scarcity]),
                scarcity_days=len([d for d in days if d.scarcity]),
                ordinary_uplift_usd=_mean_uplift([d for d in days if not d.scarcity]),
                scarcity_uplift_usd=_mean_uplift([d for d in days if d.scarcity]),
            )
        )
    return sorted(results, key=lambda r: r.uplift_usd, reverse=True)


def uplift_frame(results: list[ZoneUplift] | None = None) -> pd.DataFrame:
    results = results if results is not None else zone_uplift()
    return pd.DataFrame(
        [
            {
                "zone": r.location,
                "metro": r.metro,
                "zone-timed $/battery/day (hindsight-timed)": r.zone_timed_usd,
                "zone-blind $/battery/day (hindsight-timed)": r.zone_blind_usd,
                "uplift $ (hindsight-timed)": r.uplift_usd,
                "ordinary-day uplift $": r.ordinary_uplift_usd,
                "scarcity-day uplift $": r.scarcity_uplift_usd,
                "mean basis $/MWh": r.mean_hub_basis,
                "peak basis $/MWh": r.peak_hub_basis,
                "days won": f"{r.win_days}/{r.days}",
            }
            for r in results
        ]
    )


@dataclass(frozen=True)
class PlacementRank:
    """Where the next batteries are worth most, and how fast that value decays.

    A data-driven sketch on a handful of bundled days, not a siting study or a forecast.
    """

    location: str
    metro: str
    first_battery_usd: float
    saturation_batteries: int
    mean_hub_basis: float

    def marginal_usd(self, installed: int) -> float:
        """Value per day of the next battery once ``installed`` are already there."""
        if self.saturation_batteries <= 0:
            return 0.0
        remaining = max(0.0, 1.0 - installed / self.saturation_batteries)
        return round(self.first_battery_usd * remaining, 4)

    def total_usd(self, batteries: int) -> float:
        """Value per day of placing ``batteries`` here, integrating the decay."""
        if batteries <= 0 or self.saturation_batteries <= 0:
            return 0.0
        capped = min(batteries, self.saturation_batteries)
        mean_share = 1.0 - capped / (2 * self.saturation_batteries)
        return round(self.first_battery_usd * capped * mean_share, 2)


def placement_ranks(results: list[ZoneUplift] | None = None) -> list[PlacementRank]:
    """Rank zones by what a battery there is worth, with a saturation assumption.

    ``saturation_batteries`` is the point where the zone's congestion premium is assumed
    fully relieved: ``RELIEF_MW_PER_DOLLAR`` megawatts of discharge per dollar of mean
    positive basis, divided by one battery's inverter. Zones that never priced above the
    hub saturate immediately, which is the honest answer for them.
    """
    results = results if results is not None else zone_uplift()
    ranks = []
    for r in results:
        relief_mw = RELIEF_MW_PER_DOLLAR * max(r.mean_hub_basis, 0.0)
        ranks.append(
            PlacementRank(
                location=r.location,
                metro=r.metro,
                first_battery_usd=max(r.zone_timed_usd, 0.0),
                saturation_batteries=int(relief_mw * 1000 / BATTERY_KW),
                mean_hub_basis=r.mean_hub_basis,
            )
        )
    return sorted(
        ranks, key=lambda r: (r.saturation_batteries > 0, r.first_battery_usd), reverse=True
    )


def placement_sketch(
    batteries: int,
    ranks: list[PlacementRank] | None = None,
    block: int = 100,
) -> pd.DataFrame:
    """Place ``batteries`` greedily, always into whichever zone pays most right now.

    Returns one row per zone that received any, with how many it took, the value of the
    first and last battery placed there, and the total per day. Greedy placement in
    blocks of ``block`` is what makes the saturation visible: the best zone fills up and
    the next one starts winning.
    """
    ranks = ranks if ranks is not None else placement_ranks()
    placed = dict.fromkeys((r.location for r in ranks), 0)
    first_usd: dict[str, float] = {}
    last_usd: dict[str, float] = {}
    zone_total: dict[str, float] = dict.fromkeys((r.location for r in ranks), 0.0)
    total = 0.0

    remaining = batteries
    while remaining > 0:
        size = min(block, remaining)
        best = max(ranks, key=lambda r: r.marginal_usd(placed[r.location]))
        value = best.marginal_usd(placed[best.location])
        if value <= 0:
            break
        first_usd.setdefault(best.location, value)
        last_usd[best.location] = value
        total += value * size
        zone_total[best.location] += value * size
        placed[best.location] += size
        remaining -= size

    rows = [
        {
            "zone": r.location,
            "metro": r.metro,
            "batteries placed": placed[r.location],
            "first $/battery/day (hindsight-timed)": first_usd.get(r.location, 0.0),
            "last $/battery/day (hindsight-timed)": last_usd.get(r.location, 0.0),
            "total $/day (hindsight-timed)": round(zone_total[r.location], 2),
            "saturates at": r.saturation_batteries,
        }
        for r in ranks
        if placed[r.location] > 0
    ]
    frame = pd.DataFrame(rows)
    frame.attrs["total_usd_per_day"] = round(total, 2)
    frame.attrs["unplaced"] = remaining
    return frame


@dataclass(frozen=True)
class CongestionSummary:
    """The one-line version, stating only what the bundled days show."""

    days: int
    zones: int
    widest: WidestHour
    widest_zone: ZoneUplift
    best: ZoneUplift
    worst: ZoneUplift
    mean_uplift_usd: float
    intervals: int
    divergent_intervals: int

    @property
    def divergent_share(self) -> float:
        return round(self.divergent_intervals / self.intervals, 4) if self.intervals else 0.0

    @property
    def headline(self) -> str:
        """One zone's gap, paired with that same zone's uplift and nobody else's."""
        return (
            f"Across {self.days} bundled ERCOT days the hub average hid a "
            f"${self.widest.hub_basis:,.2f}/MWh gap: {self.widest.metro} "
            f"({self.widest.location}) priced that far above the hub in hour "
            f"{self.widest.hour:02d}, yet a battery in {self.widest_zone.location} timed "
            f"to its own zone's price earned only "
            f"${self.widest_zone.uplift_usd:,.2f}/battery/day more than the same battery "
            f"timed to the hub ({HINDSIGHT}; "
            f"{self.widest_zone.split_summary})."
        )

    @property
    def subhead(self) -> str:
        return (
            f"{self.divergent_intervals:,} of {self.intervals:,} zone-intervals "
            f"({self.divergent_share:.1%}) settled more than $5/MWh away from the hub "
            f"average, so a single-zone price series misses them by construction. "
            f"Best zone {self.best.location} "
            f"(+${self.best.uplift_usd:,.2f}/battery/day {HINDSIGHT}: "
            f"{self.best.split_summary}), worst {self.worst.location} "
            f"(${self.worst.uplift_usd:,.2f})."
        )


DIVERGENCE_USD = 5.0


def summarize(basis: pd.DataFrame | None = None) -> CongestionSummary:
    basis = basis if basis is not None else basis_frame()
    uplifts = zone_uplift(basis)
    widest = widest_hours(basis, top=1)[0]
    return CongestionSummary(
        days=int(basis["date"].nunique()),
        zones=int(basis["location"].nunique()),
        widest=widest,
        widest_zone=next(u for u in uplifts if u.location == widest.location),
        best=uplifts[0],
        worst=uplifts[-1],
        mean_uplift_usd=round(sum(u.uplift_usd for u in uplifts) / len(uplifts), 2),
        intervals=int(len(basis)),
        divergent_intervals=int((basis["hub_basis"].abs() > DIVERGENCE_USD).sum()),
    )


def dispatch_order(basis: pd.DataFrame | None = None) -> list[str]:
    """Zones in the order a congestion-aware operator should discharge them.

    Highest mean basis first: a kW discharged where the grid is short is worth more than
    the same kW discharged where it is not.
    """
    return [u.location for u in zone_uplift(basis)]


def main() -> None:
    basis = basis_frame()
    summary = summarize(basis)
    print(summary.headline)
    print(summary.subhead)
    print(f"({HINDSIGHT_NOTE}.)")
    print()
    print(uplift_frame(zone_uplift(basis)).to_string(index=False))
    print()
    print("widest zone-hours (mean $/MWh over the hub average):")
    for hour in widest_hours(basis, top=5):
        print(
            f"  {hour.location:<12} hour {hour.hour:02d}  "
            f"hub +${hour.hub_basis:>8,.2f}  west +${hour.west_basis:>8,.2f}"
        )
    print()
    print(f"where to install the next 1,000 batteries (sketch, not a forecast; {HINDSIGHT}):")
    sketch = placement_sketch(1000)
    print(sketch.to_string(index=False))
    print(f"  total ${sketch.attrs['total_usd_per_day']:,.2f}/day ({HINDSIGHT})")


if __name__ == "__main__":
    main()
