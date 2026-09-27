"""Base's two business models, scored on the same simulated fleet and the same days.

Base has described two ways the same battery can earn: a **retail-choice** model, where
Base is the member's retail electric provider, operates the battery and sells its energy
and grid services itself, and a **utility-partner** model, where the utility controls
dispatch and pays Base for access to the fleet (public interview with Base's COO,
https://www.sourcery.vc/p/breaking-base-power-hits-13b-on-1b, 3 Aug 2026). They are not
the same business: they differ in who chooses the dispatch hour, who carries price risk,
who holds the member, and who keeps the upside of a scarcity day.

This module puts them side by side on the seven bundled held-out LZ_HOUSTON days with
one simulated Base Core-style battery, so the comparison is arithmetic rather than
opinion. Everything here is simulated except the prices, which are real ERCOT prints.

Two things it deliberately does not do. It does not invent Base's access fee: the fee is
an unknown, so the output is the **break-even fee** — what the utility would have to pay
per battery for the partner model to match what the retail model earns on the same days.
And it does not model the utility's transmission-charge saving (ERCOT 4CP), which is a
large part of why a utility wants the battery at all and would move the answer.

Reproduce with ``python -m gridsignal.business``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from gridsignal import ancillary, backtest, dam, detect, forecast, holdout
from gridsignal.prices import PriceTrace
from gridsignal.signals import Signal

#: The interview both models are read from. An interview, not a Base document.
COO_INTERVIEW_URL = "https://www.sourcery.vc/p/breaking-base-power-hits-13b-on-1b"
#: Why a utility wants to call a battery in the summer evening: transmission cost is
#: allocated to load at the four summer monthly system peaks.
ERCOT_4CP_URL = "https://www.ercot.com/mktinfo/data_agg/4cp"
#: Average Texas residential retail price, the public series the modelled flat rate is
#: sized against. It is not a Base tariff and Base publishes none.
EIA_RESIDENTIAL_URL = "https://www.eia.gov/electricity/monthly/epm_table_grapher.php?t=epmt_5_6_a"
#: The member's backup floor is this repo's rule, so it cites this repo's code.
RESERVE_SOURCE_URL = "https://github.com/Regantih/gridsignal/blob/main/src/gridsignal/home.py"

#: Flat residential rate the member is billed at in the retail model. Modelled: a
#: plausible Texas fixed-rate plan, not a Base tariff.
RETAIL_RATE_USD_KWH = 0.14
#: Hours the utility calls the battery in the partner model, and the overnight hours it
#: refills it. Modelled stand-in for a 4CP-driven call window.
UTILITY_EXPORT_HOURS = range(16, 20)
UTILITY_CHARGE_HOURS = range(1, 5)
#: A month, for turning a daily number into an access fee.
DAYS_PER_MONTH = 30.4

RETAIL = "retail-choice"
PARTNER = "utility-partner"


@dataclass(frozen=True)
class Assumption:
    """One input the comparison rests on, with where it came from."""

    name: str
    value: str
    source: str
    url: str
    published: bool = True

    @property
    def provenance(self) -> str:
        return self.source if self.published else f"modelled by this repo — {self.source}"


ASSUMPTIONS: tuple[Assumption, ...] = (
    Assumption(
        "the two models",
        "retail-choice (Base sells the member energy and sells the battery's services) "
        "vs utility-partner (the utility dispatches and pays Base for access)",
        "public interview with Base's COO, 3 Aug 2026",
        COO_INTERVIEW_URL,
    ),
    Assumption(
        "battery",
        f"Base Core-style {backtest.DEFAULT_KWH:,.0f} kWh / "
        f"{backtest.DEFAULT_POWER_KW:,.0f} kW, simulated",
        "sized from the same interview, not an official specification",
        COO_INTERVIEW_URL,
        published=False,
    ),
    Assumption(
        "prices",
        "real ERCOT LZ_HOUSTON day-ahead and real-time prints, 7 bundled held-out days",
        "ERCOT public market data, cached in data/holdout",
        "https://www.ercot.com/mktinfo/prices",
    ),
    Assumption(
        "grid services",
        "ECRS and Non-Spin only, inside the ADER pilot caps the guardrails enforce",
        "ADER Pilot Governing Document Phase 3.3",
        ancillary.ADER_PILOT_URL,
    ),
    Assumption(
        "retail rate",
        f"{RETAIL_RATE_USD_KWH * 100:,.0f}¢/kWh flat, what the member is billed",
        "modelled assumption: no Base tariff is public, so the rate is set near the EIA "
        "average Texas residential price rather than taken from any Base document",
        EIA_RESIDENTIAL_URL,
        published=False,
    ),
    Assumption(
        "the utility's call",
        f"discharge {UTILITY_EXPORT_HOURS.start:02d}:00–{UTILITY_EXPORT_HOURS.stop:02d}:00, "
        f"refill {UTILITY_CHARGE_HOURS.start:02d}:00–{UTILITY_CHARGE_HOURS.stop:02d}:00, "
        "every day, regardless of price",
        "a stand-in for a 4CP-driven call window; ERCOT allocates transmission cost to "
        "load at the four summer monthly peaks, which is the utility's own interest",
        ERCOT_4CP_URL,
        published=False,
    ),
    Assumption(
        "backup",
        f"{ancillary.RESERVE_SHARE:.0%} of the pack is the member's, in both models",
        "modelled assumption: the floor is this repo's promise to the homeowner, "
        "enforced in settlement; the ADER pilot sets no such reserve",
        RESERVE_SOURCE_URL,
        published=False,
    ),
    Assumption(
        "not modelled",
        "the utility's avoided 4CP transmission charge, capacity deployment energy, "
        "customer acquisition, hardware cost and financing",
        "named so the comparison is not read as a full P&L",
        ERCOT_4CP_URL,
        published=False,
    ),
)


@dataclass(frozen=True)
class ControlLine:
    """One question about control, answered by each model."""

    question: str
    retail: str
    partner: str


CONTROL: tuple[ControlLine, ...] = (
    ControlLine(
        "who picks the dispatch hour", "Base, from the price", "the utility, from its own peak"
    ),
    ControlLine("who carries price risk", "Base", "the utility"),
    ControlLine("who bills the member", "Base, as the retail provider", "the utility"),
    ControlLine("who keeps a scarcity day", "Base and the member", "the utility"),
    ControlLine("who may sell grid services", "Base, inside the ADER pilot", "the utility's QSE"),
    ControlLine("who enforces the backup promise", "Base", "Base, over the utility's call"),
    ControlLine("what Base earns", "market revenue, variable", "an access fee, certain"),
)


@dataclass(frozen=True)
class DayModel:
    """One simulated battery, one day, under one model."""

    date: str
    model: str
    #: Wholesale value of the energy the battery moved, net of what it cost to charge.
    energy_usd: float
    #: Ancillary capacity sold inside the pilot rules. Zero when Base does not dispatch.
    capacity_usd: float
    #: Base's margin on the grid energy the member still buys, at the retail rate.
    supply_margin_usd: float
    #: Retail margin Base gives up because the battery served the house instead.
    self_supply_cost_usd: float
    #: What the member's bill drops by, against the same house with no battery.
    member_saved_usd: float
    #: kWh delivered out of the member's promised backup. Must always be zero.
    reserve_spent_kwh: float
    reserve_kwh: float

    @property
    def base_usd(self) -> float:
        """What Base earns from the battery that day, before any access fee."""
        if self.model == PARTNER:
            return 0.0
        return round(
            self.energy_usd
            + self.capacity_usd
            + self.supply_margin_usd
            - self.self_supply_cost_usd,
            4,
        )

    @property
    def market_usd(self) -> float:
        """Wholesale value the dispatch created, whoever ends up with it."""
        return round(self.energy_usd + self.capacity_usd, 4)

    @property
    def reserve_held(self) -> bool:
        return self.reserve_spent_kwh <= 1e-6


@dataclass(frozen=True)
class ModelSummary:
    """A model across the held-out days, per battery per day."""

    model: str
    days: int
    base_median_usd: float
    base_mean_usd: float
    market_median_usd: float
    market_mean_usd: float
    member_median_usd: float
    member_mean_usd: float
    capacity_median_usd: float
    reserve_breaches: int

    def fleet_usd(self, devices: int) -> float:
        return round(self.base_mean_usd * devices, 2)


@dataclass(frozen=True)
class Comparison:
    """Both models on the same days, and what it would take to be indifferent."""

    retail: ModelSummary
    partner: ModelSummary
    per_day: tuple[tuple[DayModel, DayModel], ...]
    #: Access fee per battery per month that matches everything Base earns in the
    #: retail model on the median day, the margin on the member's own supply included.
    break_even_month_usd: float
    #: The same, counting only what the battery itself earns in the market.
    break_even_battery_month_usd: float
    #: The two fees again on the mean day, which one scarcity day carries. Reported
    #: second: a mean over seven days with one $24 day in it is not a typical month.
    break_even_month_mean_usd: float
    break_even_battery_month_mean_usd: float
    #: The battery-only fee against the kW the ADER pilot lets this battery register.
    break_even_kw_month_usd: float
    registered_kw: float
    #: Wholesale value the clock-called dispatch leaves behind against price-following.
    certainty_cost_usd: float
    #: Days where an unclamped utility call would have spent the member's backup.
    unclamped_breaches: int


def price_plan(trace: PriceTrace) -> pd.DataFrame:
    detections = detect.detect_spikes(trace.frame)
    prob = forecast.forecast_spike_probability(forecast.build_features(detections))
    return dam.signals_for(detections, prob, trace.dam)


def utility_call(plan: pd.DataFrame) -> pd.DataFrame:
    """The same day, dispatched on the utility's clock instead of the price.

    The plan's day-ahead columns are kept so the settlement is otherwise identical; only
    the action changes, and it no longer depends on what the market did.
    """
    called = plan.copy()
    hours = pd.to_datetime(called["interval_start"]).dt.hour
    action = pd.Series(Signal.HOLD.value, index=called.index)
    action[hours.isin(UTILITY_CHARGE_HOURS)] = Signal.CHARGE.value
    action[hours.isin(UTILITY_EXPORT_HOURS)] = Signal.EXPORT.value
    called["signal"] = action.to_numpy()
    called["planned"] = action.to_numpy()
    return called


def _settle(
    plan: pd.DataFrame,
    trace: PriceTrace,
    battery: ancillary.Battery,
    backup_kwh: float,
) -> pd.DataFrame:
    return backtest.value_captured(
        plan,
        trace.frame,
        kwh=battery.kwh,
        power_kw=battery.power_kw,
        serve_home=True,
        backup_kwh=backup_kwh,
    )


def reserve_spent_kwh(ledger: pd.DataFrame, reserve_kwh: float) -> float:
    """Energy delivered while the pack was at or under the member's floor.

    The pack starts the day empty, so the low point of the day says nothing on its own;
    what matters is whether anything was ever *taken* out of the promised energy.
    """
    soc = ledger["signal_soc_kwh"].astype(float)
    opening = soc.shift(1, fill_value=0.0)
    delivered = (opening - soc).clip(lower=0.0)
    below_floor = (reserve_kwh - soc).clip(lower=0.0)
    return float(pd.concat([delivered, below_floor], axis=1).min(axis=1).sum())


def _margin_usd(ledger: pd.DataFrame, kwh_column: str) -> float:
    """Retail margin on energy Base sells the member: the rate less what it cost."""
    spp = ledger["spp"].astype(float) / 1000.0
    return float((ledger[kwh_column] * (RETAIL_RATE_USD_KWH - spp)).sum())


def score_day(
    trace: PriceTrace,
    battery: ancillary.Battery = ancillary.BASE_CORE,
) -> tuple[DayModel, DayModel]:
    """One day under both models, from the same prices and the same battery."""
    reserve = battery.reserve_kwh
    plan = price_plan(trace)

    ledger = _settle(plan, trace, battery, reserve)
    summary = backtest.summarize(ledger)
    capacity = ancillary.co_optimize(trace, battery).ancillary_usd
    retail = DayModel(
        date=trace.date,
        model=RETAIL,
        energy_usd=round(summary.signal_usd, 4),
        capacity_usd=round(capacity, 4),
        supply_margin_usd=round(_margin_usd(ledger, "signal_grid_kwh"), 4),
        self_supply_cost_usd=round(_margin_usd(ledger, "signal_home_kwh"), 4),
        member_saved_usd=round(RETAIL_RATE_USD_KWH * summary.home_served_kwh, 4),
        reserve_spent_kwh=round(reserve_spent_kwh(ledger, reserve), 4),
        reserve_kwh=reserve,
    )

    called = utility_call(plan)
    called_ledger = _settle(called, trace, battery, reserve)
    called_summary = backtest.summarize(called_ledger)
    partner = DayModel(
        date=trace.date,
        model=PARTNER,
        energy_usd=round(called_summary.signal_usd, 4),
        capacity_usd=0.0,
        supply_margin_usd=0.0,
        self_supply_cost_usd=0.0,
        member_saved_usd=round(RETAIL_RATE_USD_KWH * called_summary.home_served_kwh, 4),
        reserve_spent_kwh=round(reserve_spent_kwh(called_ledger, reserve), 4),
        reserve_kwh=reserve,
    )
    return retail, partner


def unclamped_breach(trace: PriceTrace, battery: ancillary.Battery = ancillary.BASE_CORE) -> bool:
    """Would the utility's call have spent the member's backup if nothing stopped it?

    This is the stress the guard has to survive: the same call, settled with the reserve
    floor removed. If the answer were always no, the floor would be decoration.
    """
    ledger = _settle(utility_call(price_plan(trace)), trace, battery, 0.0)
    return reserve_spent_kwh(ledger, battery.reserve_kwh) > 1e-6


def _summarize(days: tuple[DayModel, ...]) -> ModelSummary:
    base = pd.Series([d.base_usd for d in days], dtype=float)
    market = pd.Series([d.market_usd for d in days], dtype=float)
    member = pd.Series([d.member_saved_usd for d in days], dtype=float)
    capacity = pd.Series([d.capacity_usd for d in days], dtype=float)
    return ModelSummary(
        model=days[0].model,
        days=len(days),
        base_median_usd=round(float(base.median()), 2),
        base_mean_usd=round(float(base.mean()), 2),
        market_median_usd=round(float(market.median()), 2),
        market_mean_usd=round(float(market.mean()), 2),
        member_median_usd=round(float(member.median()), 2),
        member_mean_usd=round(float(member.mean()), 2),
        capacity_median_usd=round(float(capacity.median()), 2),
        reserve_breaches=sum(0 if d.reserve_held else 1 for d in days),
    )


def compare(
    battery: ancillary.Battery = ancillary.BASE_CORE,
    devices: int = ancillary.FLEET_DEVICES,
) -> Comparison:
    """Both models across the bundled held-out days, plus the break-even access fee."""
    scored = tuple(score_day(trace, battery) for trace in holdout.load_holdout())
    retail = _summarize(tuple(day for day, _ in scored))
    partner = _summarize(tuple(day for _, day in scored))
    registered_kw = ancillary.ADER_PILOT.per_battery_kw("ecrs", devices)
    fee_month = retail.base_median_usd * DAYS_PER_MONTH
    battery_fee_month = retail.market_median_usd * DAYS_PER_MONTH
    return Comparison(
        retail=retail,
        partner=partner,
        per_day=scored,
        break_even_month_usd=round(fee_month, 2),
        break_even_battery_month_usd=round(battery_fee_month, 2),
        break_even_month_mean_usd=round(retail.base_mean_usd * DAYS_PER_MONTH, 2),
        break_even_battery_month_mean_usd=round(retail.market_mean_usd * DAYS_PER_MONTH, 2),
        break_even_kw_month_usd=(
            round(battery_fee_month / registered_kw, 2) if registered_kw else 0.0
        ),
        registered_kw=round(registered_kw, 2),
        certainty_cost_usd=round(retail.market_median_usd - partner.market_median_usd, 2),
        unclamped_breaches=sum(
            1 for trace in holdout.load_holdout() if unclamped_breach(trace, battery)
        ),
    )


def main() -> None:
    result = compare()
    print(
        "Base's two models on the same simulated battery and the same seven held-out "
        "days.\nEvery battery, household load and award is simulated; the prices are "
        "real ERCOT prints.\n"
    )

    print("Assumptions")
    for item in ASSUMPTIONS:
        print(f"  {item.name:<20}{item.value}")
        print(f"  {'':<20}source: {item.provenance}")
        print(f"  {'':<20}{item.url}")
    print()

    print(f"{'who decides what':<34}{'retail-choice':<34}utility-partner")
    for line in CONTROL:
        print(f"  {line.question:<32}{line.retail:<34}{line.partner}")
    print()

    print(f"{'per battery per day, median':<40}{'retail-choice':>16}{'utility-partner':>18}")
    rows = (
        ("Base earns", result.retail.base_median_usd, result.partner.base_median_usd),
        (
            "wholesale value the dispatch created",
            result.retail.market_median_usd,
            result.partner.market_median_usd,
        ),
        (
            "of which grid services",
            result.retail.capacity_median_usd,
            result.partner.capacity_median_usd,
        ),
        (
            "member's bill, lower by",
            result.retail.member_median_usd,
            result.partner.member_median_usd,
        ),
    )
    for label, left, right in rows:
        print(f"  {label:<38}{left:>16,.2f}{right:>18,.2f}")
    print(
        f"  {'backup energy sold out from under the member':<44}"
        f"{result.retail.reserve_breaches:>4} days{result.partner.reserve_breaches:>10} days"
    )
    print(
        f"  the member's bill barely moves either way (mean "
        f"${result.retail.member_mean_usd:,.2f} against "
        f"${result.partner.member_mean_usd:,.2f} a day): storage is held for the evening,"
        f"\n  so the house is carried by the grid and the member's return is the backup, "
        f"not self-supply."
    )
    print()

    print(
        f"Break-even access fee, median day first. For the battery alone — what its "
        f"energy and\ngrid services earn — the utility would have to pay "
        f"${result.break_even_battery_month_usd:,.2f} per battery per month "
        f"(${result.break_even_kw_month_usd:,.2f} per kW-month on the "
        f"{result.registered_kw:,.1f} kW the pilot lets it register). On the mean day "
        f"it is\n${result.break_even_battery_month_mean_usd:,.2f}, and that mean is one "
        f"scarcity day carrying seven. To replace the whole retail\nrelationship, margin "
        f"on the member's own supply included, ${result.break_even_month_usd:,.2f} per "
        f"battery per month on the median day "
        f"(${result.break_even_month_mean_usd:,.2f} on the mean). The gap is the point: "
        f"in the retail model\nmost of what Base earns is the member's electricity bill, "
        f"not the battery's market revenue."
    )
    print(
        f"The price of certainty: calling the battery on the clock instead of the price "
        f"leaves ${result.certainty_cost_usd:,.2f} per battery per day of wholesale value "
        f"behind (median). That is what the utility is buying, and what Base stops "
        f"carrying: in the partner model the access fee arrives whatever the market does."
    )
    print(
        f"What Base keeps either way: the backup floor. With the floor removed, the "
        f"utility's call\nspends the member's promised energy on "
        f"{result.unclamped_breaches} of {result.retail.days} days; with it, "
        f"{result.partner.reserve_breaches}. The utility picks the hour, Base still "
        f"answers to the member."
    )
    print()

    print(f"{'date':<12}{'retail Base $':>15}{'retail market $':>17}{'partner market $':>18}")
    for day, called in result.per_day:
        print(
            f"{day.date:<12}{day.base_usd:>15,.2f}{day.market_usd:>17,.2f}"
            f"{called.market_usd:>18,.2f}"
        )


if __name__ == "__main__":
    main()
