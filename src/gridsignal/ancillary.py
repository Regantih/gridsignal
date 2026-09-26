"""Co-optimize a home battery between energy and ERCOT ancillary capacity.

The ancillary clearing prices are real: ERCOT publishes day-ahead market clearing
prices for capacity (Reg Up, Reg Down, RRS, ECRS, Non-Spin) for every trade date, and
``scripts/fetch_as_prices.py`` caches them next to each bundled price trace with a
provenance sidecar. Everything the battery does with them is simulated.

The energy leg is the frozen signal policy from :mod:`gridsignal.dam` — unchanged, so
the held-out scoring stays comparable. Ancillary bids then take the inverter capacity
that plan leaves idle, and outbid the export when the capacity price beats the energy
price for that hour. Two constraints are hard: the member's backup reserve is never
offered, and a product is only bid when the battery could actually sustain it for the
product's duration.

Simulation assumptions, documented because they set the numbers:

* Capacity payments only. A battery holding reserve is paid the clearing price for
  being available; deployment energy is not modelled, which understates the value.
* Duration requirements per product (hours of sustained response above reserve) are
  the ``sustain_h`` values below, a simplification of ERCOT's ESR qualification rules.
* One product per hour. Offering the same kW into two products would be selling it
  twice, so the hour goes to the best-paying product it can actually deliver.
* Price taker. The fleet is assumed to clear at the published price without moving it.
  That is only credible while the fleet is small next to what ERCOT procures, so the
  report flags the offer against the published AS plan (MW procured) where the MIS
  still publishes it — 10,000 × 20 kW is 200 MW, which is *not* small against Reg Down.

    python -m gridsignal.ancillary
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from gridsignal import backtest, dam, detect, forecast, holdout, ingest
from gridsignal.paths import DATA_DIR
from gridsignal.prices import AS_SUFFIX, PriceTrace, load_price_trace, load_scenario
from gridsignal.signals import Signal

#: Share of usable capacity held back for the member's backup, never sold.
RESERVE_SHARE = 0.20
FLEET_DEVICES = 10_000
#: ERCOT's published ancillary procurement volumes, cached by scripts/fetch_as_plan.py.
AS_PLAN_DIR = DATA_DIR / "as_plan"
#: Above this share of a product's procured MW, calling the fleet a price taker is not honest.
PRICE_TAKER_SHARE = 0.05


@dataclass(frozen=True)
class Product:
    """One ERCOT ancillary product as this simulation bids into it."""

    key: str
    label: str
    #: ``discharge`` products need stored energy above reserve; ``charge`` needs room.
    direction: str
    #: Hours the battery must be able to sustain the award for, above reserve.
    sustain_h: float


PRODUCTS: tuple[Product, ...] = (
    Product("regup", "Reg Up", "discharge", 1.0),
    Product("rrs", "RRS", "discharge", 1.0),
    Product("ecrs", "ECRS", "discharge", 2.0),
    Product("nonspin", "Non-Spin", "discharge", 4.0),
    Product("regdn", "Reg Down", "charge", 1.0),
)


@dataclass(frozen=True)
class Battery:
    """A simulated home battery. Labelled per public interview, not official specs."""

    label: str
    kwh: float
    power_kw: float

    @property
    def reserve_kwh(self) -> float:
        return round(self.kwh * RESERVE_SHARE, 3)


LEGACY = Battery("Legacy unit", backtest.DEFAULT_KWH, backtest.DEFAULT_POWER_KW)
BASE_CORE = Battery("Base Core-style unit", 40.0, 20.0)
FLEET_MIX: tuple[tuple[Battery, float], ...] = ((LEGACY, 0.6), (BASE_CORE, 0.4))


@dataclass(frozen=True)
class HourAward:
    """What one hour of one battery's capacity was sold into."""

    hour: int
    product: str
    kw: float
    price_mw_h: float
    usd: float
    #: True when the export was withheld this hour so the energy could be rented instead.
    held_for_capacity: bool


@dataclass(frozen=True)
class DayValue:
    """Energy versus ancillary for one battery on one day."""

    date: str
    battery: str
    energy_only_usd: float
    energy_usd: float
    ancillary_usd: float
    awards: tuple[HourAward, ...]
    held_hours: int
    reserve_violations: int

    @property
    def total_usd(self) -> float:
        return round(self.energy_usd + self.ancillary_usd, 2)

    @property
    def energy_given_up_usd(self) -> float:
        """Export revenue the battery walked away from to rent capacity instead."""
        return round(max(self.energy_only_usd - self.energy_usd, 0.0), 2)

    @property
    def uplift_usd(self) -> float:
        """Extra dollars over running the same battery on energy alone."""
        return round(self.total_usd - self.energy_only_usd, 2)

    @property
    def by_product(self) -> dict[str, float]:
        split: dict[str, float] = {p.key: 0.0 for p in PRODUCTS}
        for award in self.awards:
            split[award.product] = round(split[award.product] + award.usd, 2)
        return split


def as_path_for(path: Path) -> Path:
    """Where the ancillary clearing prices for a real-time trace are cached."""
    return path.with_name(f"{path.stem}{AS_SUFFIX}{path.suffix}")


def as_plan_path(date: str) -> Path:
    """Where ERCOT's published procurement volume for one trade date is cached."""
    return AS_PLAN_DIR / f"as_plan_{date.replace('-', '')}.parquet"


def load_as_plan(date: str) -> pd.DataFrame | None:
    """Procured MW per product per hour, or ``None`` when ERCOT no longer publishes it."""
    path = as_plan_path(date)
    return pd.read_parquet(path) if path.exists() else None


def load_as_prices(path: Path) -> pd.DataFrame:
    """Cached hourly ancillary clearing prices for the day of ``path``'s trace."""
    as_path = as_path_for(path)
    if not as_path.exists():
        raise FileNotFoundError(
            f"no cached ancillary prices at {as_path}; run python scripts/fetch_as_prices.py"
        )
    frame = pd.read_parquet(as_path)
    frame["interval_start"] = pd.to_datetime(frame["interval_start"]).dt.tz_localize(None)
    return frame.sort_values("interval_start").reset_index(drop=True)


def as_provenance(path: Path) -> dict[str, str]:
    """The provenance sidecar written next to the cached ancillary prices."""
    meta = as_path_for(path).with_suffix(".json")
    return json.loads(meta.read_text()) if meta.exists() else {}


def _plan(trace: PriceTrace) -> pd.DataFrame:
    """The frozen signal policy's plan for this day, thresholds untouched."""
    detections = detect.detect_spikes(trace.frame)
    prob = forecast.forecast_spike_probability(forecast.build_features(detections))
    return dam.signals_for(detections, prob, trace.dam)


def _settle(trace: PriceTrace, plan: pd.DataFrame, battery: Battery) -> pd.DataFrame:
    return backtest.value_captured(
        plan, trace.frame, kwh=battery.kwh, power_kw=battery.power_kw, serve_home=True
    )


def _energy_ledger(trace: PriceTrace, battery: Battery) -> pd.DataFrame:
    """The frozen signal policy's energy plan for this day, home-first."""
    return _settle(trace, _plan(trace), battery)


def _day_ahead_price(trace: PriceTrace) -> dict[int, float]:
    """The day-ahead hourly curve, keyed by hour. Published before the trade day."""
    if trace.dam is None or trace.dam.empty:
        return {}
    return {
        int(row.interval_start.hour): float(row.spp) for row in trace.dam.itertuples(index=False)
    }


def _hold_hours(
    trace: PriceTrace, plan: pd.DataFrame, battery: Battery, as_prices: pd.DataFrame
) -> set[int]:
    """Hours where renting the stored energy as capacity beats selling it as energy.

    Decided day-ahead: ancillary clears in the day-ahead market and the energy side
    uses the day-ahead curve, so both prices are published before the trade day. An
    hour that is held keeps its energy, which is then available to rent again in the
    next hour or to export later.
    """
    day_ahead = _day_ahead_price(trace)
    if not day_ahead:
        return set()

    hours = plan.assign(hour=trace.frame["interval_start"].dt.hour)
    exports = {
        int(hour)
        for hour, rows in hours.groupby("hour")
        if (rows["signal"] == Signal.EXPORT.value).any()
    }
    sellable_kwh = max(battery.kwh - battery.reserve_kwh, 0.0)
    prices = as_prices.set_index(as_prices["interval_start"].dt.hour)

    held: set[int] = set()
    for hour in sorted(exports):
        if hour not in prices.index:
            continue
        best = _best_product(prices.loc[hour], battery, sellable_kwh, 0.0, battery.power_kw)
        if not best:
            continue
        _, kw, price = best
        capacity_usd = kw * price / 1000.0
        export_usd = min(battery.power_kw, sellable_kwh) * day_ahead.get(hour, 0.0) / 1000.0
        if capacity_usd > export_usd:
            held.add(hour)
    return held


def _withhold(plan: pd.DataFrame, trace: PriceTrace, hours: set[int]) -> pd.DataFrame:
    """Turn the export off in the held hours; the energy stays in the battery."""
    if not hours:
        return plan
    updated = plan.copy()
    hour_of = trace.frame["interval_start"].dt.hour.to_numpy()
    mask = pd.Series(hour_of, index=updated.index).isin(hours) & (
        updated["signal"] == Signal.EXPORT.value
    )
    updated.loc[mask, "signal"] = Signal.HOLD.value
    return updated


def _hour_rows(ledger: pd.DataFrame) -> pd.DataFrame:
    """Tag the 15-minute energy ledger with the hour ancillary clears on.

    ``moved_kwh`` is energy leaving storage (positive) or entering it (negative), read
    off the state-of-charge path so the inverter use is whatever the energy plan did,
    not whatever its label says.
    """
    frame = ledger.copy()
    frame["hour"] = frame["interval_start"].dt.hour
    soc = frame["signal_soc_kwh"].astype(float)
    frame["moved_kwh"] = -soc.diff().fillna(soc.iloc[0])
    frame["exporting"] = frame["signal_action"] == Signal.EXPORT.value
    return frame


def co_optimize(
    trace: PriceTrace,
    battery: Battery = LEGACY,
    as_prices: pd.DataFrame | None = None,
) -> DayValue:
    """Sell the capacity the energy plan leaves idle, and outbid it when that pays more.

    Returns the day's split between energy and ancillary for one battery, plus the
    hourly awards and a count of reserve violations (which must always be zero).
    """
    if as_prices is None:
        as_prices = load_as_prices(_trace_path(trace))

    plan = _plan(trace)
    energy_only = _settle(trace, plan, battery)
    held = _hold_hours(trace, plan, battery, as_prices)
    ledger = _hour_rows(_settle(trace, _withhold(plan, trace, held), battery))
    prices = as_prices.set_index(as_prices["interval_start"].dt.hour)
    energy_only_usd = round(float(energy_only["signal_usd"].sum()), 2)

    awards: list[HourAward] = []
    energy_usd = 0.0
    violations = 0

    for hour, rows in ledger.groupby("hour", sort=True):
        hour_energy_usd = float(rows["signal_usd"].sum())
        # An award is held for the whole hour, so it has to survive the worst moment of
        # it: the least stored energy for a discharge product, the least room for Reg Down.
        soc = rows["signal_soc_kwh"].astype(float)
        headroom_kwh = max(battery.kwh - float(soc.max()), 0.0)
        sellable_kwh = max(float(soc.min()) - battery.reserve_kwh, 0.0)

        moved = rows["moved_kwh"]
        exported_kw = max(float(moved.clip(lower=0).sum()), 0.0)
        charged_kw = max(float((-moved).clip(lower=0).sum()), 0.0)
        idle_kw = max(battery.power_kw - max(exported_kw, charged_kw), 0.0)

        if hour not in prices.index:
            energy_usd += hour_energy_usd
            continue

        best = _best_product(prices.loc[hour], battery, sellable_kwh, headroom_kwh, idle_kw)
        energy_usd += hour_energy_usd
        if not best:
            continue

        product, kw, price = best
        if product.direction == "discharge" and kw * product.sustain_h > sellable_kwh + 1e-6:
            violations += 1
            continue
        awards.append(
            HourAward(
                hour=int(hour),
                product=product.key,
                kw=round(kw, 3),
                price_mw_h=round(price, 2),
                usd=round(kw * price / 1000.0, 4),
                held_for_capacity=int(hour) in held,
            )
        )

    return DayValue(
        date=trace.date,
        battery=battery.label,
        energy_only_usd=energy_only_usd,
        energy_usd=round(energy_usd, 2),
        ancillary_usd=round(sum(a.usd for a in awards), 2),
        awards=tuple(awards),
        held_hours=len(held),
        reserve_violations=violations,
    )


def _best_product(
    row: pd.Series,
    battery: Battery,
    sellable_kwh: float,
    headroom_kwh: float,
    available_kw: float,
) -> tuple[Product, float, float] | None:
    """Highest-paying product the battery can actually deliver this hour."""
    if available_kw <= 0:
        return None
    best: tuple[Product, float, float] | None = None
    for product in PRODUCTS:
        price = float(row.get(product.key, 0.0) or 0.0)
        if price <= 0:
            continue
        if product.direction == "discharge":
            deliverable = sellable_kwh / product.sustain_h
        else:
            deliverable = headroom_kwh / product.sustain_h
        kw = min(available_kw, deliverable, battery.power_kw)
        if kw <= 0:
            continue
        value = kw * price
        if best is None or value > best[1] * best[2]:
            best = (product, kw, price)
    return best


def _trace_path(trace: PriceTrace) -> Path:
    """Find the cached parquet a trace came from, so its ancillary sibling can load."""
    slug = holdout.slug_for(trace.date)
    for directory in (holdout.HOLDOUT_DIR, holdout.TUNING_DIR):
        candidate = directory / f"{slug}.parquet"
        if candidate.exists():
            return candidate
    from gridsignal import prices as price_module

    for candidate in (price_module.SCARCITY_PRICES, price_module.SAMPLE_PRICES):
        if candidate.exists() and load_price_trace(candidate).date == trace.date:
            return candidate
    raise FileNotFoundError(f"no bundled price trace for {trace.date}")


@dataclass(frozen=True)
class SplitSummary:
    """Co-optimized value across a set of days, per battery per day."""

    days: int
    battery: str
    energy_only_usd: float
    energy_usd: float
    ancillary_usd: float
    by_product: dict[str, float]
    held_hours: int
    reserve_violations: int
    #: Every scored day, so the concentration of the value is visible, not averaged away.
    per_day: tuple[DayValue, ...] = ()

    @property
    def total_usd(self) -> float:
        return round(self.energy_usd + self.ancillary_usd, 2)

    @property
    def energy_given_up_usd(self) -> float:
        return round(max(self.energy_only_usd - self.energy_usd, 0.0), 2)

    @property
    def uplift_usd(self) -> float:
        return round(self.total_usd - self.energy_only_usd, 2)

    @property
    def mean_uplift_usd(self) -> float:
        return round(self.uplift_usd / self.days, 2) if self.days else 0.0

    @property
    def median_uplift_usd(self) -> float:
        """The typical day, which the mean hides when one day carries the total."""
        values = sorted(d.uplift_usd for d in self.per_day)
        if not values:
            return 0.0
        mid = len(values) // 2
        if len(values) % 2:
            return round(values[mid], 2)
        return round((values[mid - 1] + values[mid]) / 2, 2)

    @property
    def top_day_share(self) -> tuple[str, float]:
        """The single best day and the share of the total uplift it carries."""
        if not self.per_day or self.uplift_usd <= 0:
            return ("", 0.0)
        best = max(self.per_day, key=lambda d: d.uplift_usd)
        return (best.date, round(best.uplift_usd / self.uplift_usd, 4))

    def fleet_usd(self, devices: int = FLEET_DEVICES) -> float:
        """Mean daily ancillary uplift scaled to a fleet of ``devices`` batteries."""
        return round(self.mean_uplift_usd * devices, 2)


def evaluate(traces: list[PriceTrace], battery: Battery = LEGACY) -> SplitSummary:
    """Co-optimize each day and total the split. Held-out days are scored as before."""
    days = [co_optimize(trace, battery) for trace in traces]
    split: dict[str, float] = {p.key: 0.0 for p in PRODUCTS}
    for day in days:
        for key, usd in day.by_product.items():
            split[key] = round(split[key] + usd, 2)
    return SplitSummary(
        days=len(days),
        battery=battery.label,
        energy_only_usd=round(sum(d.energy_only_usd for d in days), 2),
        energy_usd=round(sum(d.energy_usd for d in days), 2),
        ancillary_usd=round(sum(d.ancillary_usd for d in days), 2),
        by_product=split,
        held_hours=sum(d.held_hours for d in days),
        reserve_violations=sum(d.reserve_violations for d in days),
        per_day=tuple(days),
    )


def holdout_summary(battery: Battery = LEGACY) -> SplitSummary:
    return evaluate(holdout.load_holdout(), battery)


def scarcity_day(battery: Battery = LEGACY) -> DayValue:
    return co_optimize(load_scenario("scarcity"), battery)


@dataclass(frozen=True)
class ProcurementFlag:
    """How big the simulated fleet's Reg Down offer is next to what ERCOT buys."""

    date: str
    #: Largest MW this fleet would offer into Reg Down in any hour of the day.
    fleet_mw: float
    #: MW of Reg Down ERCOT procured in that hour, from the published AS plan.
    procured_mw: float

    @property
    def share(self) -> float:
        return round(self.fleet_mw / self.procured_mw, 4) if self.procured_mw else 0.0

    @property
    def price_taker_credible(self) -> bool:
        """A fleet worth a few percent of the procurement can plausibly take the price."""
        return self.share <= PRICE_TAKER_SHARE


def procurement_flag(
    battery: Battery = BASE_CORE, devices: int = FLEET_DEVICES
) -> ProcurementFlag | None:
    """Flag the fleet's Reg Down offer against ERCOT's published procurement volume.

    ``None`` when no bundled day still has a published AS plan (the MIS keeps about a
    month), which is itself worth saying rather than assuming the offer is small.
    """
    for trace in (*holdout.load_holdout(), load_scenario("normal")):
        plan = load_as_plan(trace.date)
        if plan is None:
            continue
        day = co_optimize(trace, battery)
        by_hour: dict[int, float] = {}
        for award in day.awards:
            if award.product == "regdn":
                by_hour[award.hour] = by_hour.get(award.hour, 0.0) + award.kw
        if not by_hour:
            continue
        hours = plan.set_index(plan["interval_start"].dt.hour)["regdn"]
        hour, kw = max(by_hour.items(), key=lambda item: item[1])
        return ProcurementFlag(
            date=trace.date,
            fleet_mw=round(kw * devices / 1000.0, 1),
            procured_mw=round(float(hours.get(hour, 0.0)), 1),
        )
    return None


def headline(summary: SplitSummary) -> str:
    """What the ancillary split says that the energy-only view does not."""
    total = sum(summary.by_product.values())
    share = summary.by_product["regdn"] / total if total else 0.0
    top_date, top_share = summary.top_day_share
    return (
        f"Selling the capacity the energy plan leaves idle adds "
        f"${summary.mean_uplift_usd:,.2f} per battery per day on the held-out days, and "
        f"{share:.0%} of it is Reg Down \u2014 paid for room to charge, not energy to sell. "
        f"The value is concentrated in rare days: the median day is only "
        f"${summary.median_uplift_usd:,.2f} and {top_date} alone carries {top_share:.0%} "
        f"of the total, so this is not income to count on every day."
    )


def lines() -> list[str]:
    """The CLI report, one command that reproduces every published number."""
    out: list[str] = []
    scarcity = load_scenario("scarcity")
    meta = as_provenance(_trace_path(scarcity))
    out.append(
        f"ERCOT ancillary clearing prices, {meta.get('date', scarcity.date)} "
        f"({meta.get('units', '$/MW per hour')}), source {meta.get('source', ingest.AS_SOURCE_URL)}"
    )
    out.append("Capacity payments only; deployment energy not modelled. Fleet is simulated.")
    out.append("")

    out.append(
        f"{'battery':<24}{'energy $':>10}{'ancillary $':>13}{'total $':>10}"
        f"{'uplift $':>10}{'export held':>13}"
    )
    for battery, _ in FLEET_MIX:
        day = scarcity_day(battery)
        out.append(
            f"{battery.label:<24}{day.energy_usd:>10.2f}{day.ancillary_usd:>13.2f}"
            f"{day.total_usd:>10.2f}{day.uplift_usd:>10.2f}{day.held_hours:>13d}"
        )
    out.append("")

    for battery, _ in FLEET_MIX:
        summary = holdout_summary(battery)
        split = ", ".join(
            f"{p.label} ${summary.by_product[p.key]:,.2f}"
            for p in PRODUCTS
            if summary.by_product[p.key]
        )
        out.append(
            f"{battery.label}: {summary.days} held-out days, energy ${summary.energy_usd:,.2f}, "
            f"ancillary ${summary.ancillary_usd:,.2f}, "
            f"mean uplift ${summary.mean_uplift_usd:,.2f}/battery/day "
            f"({summary.fleet_usd():,.0f}/day across {FLEET_DEVICES:,} batteries)"
        )
        out.append(f"  split: {split or 'nothing cleared above zero'}")
        out.append(
            f"  export withheld in {summary.held_hours} hours, "
            f"${summary.energy_given_up_usd:,.2f} of energy revenue given up"
        )
        out.append(f"  backup reserve violations: {summary.reserve_violations}")
        out.append(
            f"  median day ${summary.median_uplift_usd:,.2f} vs mean "
            f"${summary.mean_uplift_usd:,.2f} \u2014 the value is concentrated in rare days"
        )
    out.append("")

    core = holdout_summary(BASE_CORE)
    out.append(f"{BASE_CORE.label}, uplift per held-out day (Reg Down is the bulk of it):")
    out.append(f"{'date':<14}{'uplift $':>10}{'Reg Down $':>13}{'mean Reg Down $/MW-h':>24}")
    for day in sorted(core.per_day, key=lambda d: d.date):
        regdn_awards = [a for a in day.awards if a.product == "regdn"]
        mean_price = (
            sum(a.price_mw_h for a in regdn_awards) / len(regdn_awards) if regdn_awards else 0.0
        )
        out.append(
            f"{day.date:<14}{day.uplift_usd:>10.2f}"
            f"{day.by_product['regdn']:>13.2f}{mean_price:>24.2f}"
        )
    out.append("")

    flag = procurement_flag()
    if flag is None:
        out.append(
            "Price-taker assumption unchecked: ERCOT's MIS no longer publishes an AS plan "
            "for any bundled trade date, so the fleet's offer is not sized against "
            "procurement. Treat the fleet numbers as an upper bound."
        )
    else:
        verdict = "plausible" if flag.price_taker_credible else "NOT credible"
        out.append(
            f"Price-taker check ({flag.date}, ERCOT published AS plan): "
            f"{FLEET_DEVICES:,} simulated batteries would offer {flag.fleet_mw:,.0f} MW into "
            f"Reg Down in the peak hour against {flag.procured_mw:,.0f} MW procured "
            f"({flag.share:.0%}) \u2014 price-taker assumption {verdict}. A fleet this size "
            "would move the clearing price it is being paid, so the fleet-scale dollars are "
            "an upper bound, not a forecast."
        )
    out.append("")
    out.append(headline(core))
    return out


def main() -> int:
    print("\n".join(lines()))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
