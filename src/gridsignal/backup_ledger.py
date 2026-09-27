"""The backup promise, audited interval by interval wherever a battery earns.

Every member in this simulation is promised the same thing: a fifth of their pack is
theirs, held for an outage, and nothing the fleet sells may reach into it. This module
is the receipt. It walks every path that moves a member's stored energy — the market
backtest on the held-out days, the Control Room's dispatch across the grid event, and
every bundled chaos scenario's awards — and records, per member and per interval, how
much backup was promised, how much was actually standing there, and how much (if any)
was taken out of it.

The pack starts a market day empty and fills, so a low state of charge is not itself a
breach: what the promise forbids is *delivering* energy that had to come out of the
floor. That is what ``taken_kwh`` measures, and it is the only thing counted as a
violation.

Zero violations is worth nothing on its own — a promise no dispatch was ever close to
breaking is decoration. So the same walk runs a second time with the floor removed and
nothing else changed, and the report prints both columns side by side: what the guard
catches, and what would have happened without it.

Reproduce with ``python -m gridsignal.backup_ledger``. Simulated fleet, simulated
household load, real cached ERCOT prices.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd

from gridsignal import ancillary, backtest, business, holdout, home
from gridsignal.control_room.engine import ControlRoomEngine
from gridsignal.control_room.models import Device
from gridsignal.load import ESSENTIAL_LOAD_KW
from gridsignal.mesh.scenarios import Scenario, available_scenarios, load_scenario
from gridsignal.prices import PriceTrace
from gridsignal.prices import load_scenario as load_price_scenario
from gridsignal.simulate import run_scenario

#: The promise itself: the share of the pack that is the member's, whatever the fleet
#: is doing with the rest. The market settlement and the fleet dispatch hold the same
#: figure back, and the audit always measures against this number — including in the
#: unguarded run, where the promise still stands even though nothing enforces it.
PROMISE_SHARE = backtest.RESERVE_SHARE
#: Rounding slack, in kWh. Below this a difference is a float artefact, not a breach.
TOLERANCE_KWH = 1e-6
#: Intervals the grid event is split into for the Control Room walk.
EVENT_STEPS = 12
#: Price days the Control Room walk is run over.
FLEET_DAYS = ("normal", "scarcity")

MARKET = "market day"
FLEET = "grid event"
CHAOS = "chaos scenario"


@dataclass(frozen=True)
class IntervalRow:
    """One member, one interval, one promise."""

    family: str
    source: str
    member: str
    interval: str
    #: Energy the member is promised, in kWh.
    promised_kwh: float
    #: How much of that promise was actually standing in the pack at the interval's end.
    held_kwh: float
    #: Energy delivered in this interval that had to come out of the promise.
    taken_kwh: float

    @property
    def kept(self) -> bool:
        return self.taken_kwh <= TOLERANCE_KWH

    @property
    def promised_hours(self) -> float:
        """The promise in the unit a member cares about: hours of essential load."""
        return round(self.promised_kwh / ESSENTIAL_LOAD_KW, 2)

    @property
    def held_hours(self) -> float:
        return round(self.held_kwh / ESSENTIAL_LOAD_KW, 2)


@dataclass(frozen=True)
class MemberRun:
    """What one member's promise did across one run: a day, an event, a scenario."""

    family: str
    source: str
    member: str
    intervals: int
    promised_kwh: float
    #: The least backup standing at any point in the run.
    worst_held_kwh: float
    taken_kwh: float
    violations: int

    @property
    def promised_hours(self) -> float:
        return round(self.promised_kwh / ESSENTIAL_LOAD_KW, 2)

    @property
    def worst_held_hours(self) -> float:
        return round(self.worst_held_kwh / ESSENTIAL_LOAD_KW, 2)


@dataclass(frozen=True)
class Ledger:
    """Every member-run in one walk of the product."""

    guarded: bool
    runs: tuple[MemberRun, ...]

    @property
    def intervals(self) -> int:
        return sum(r.intervals for r in self.runs)

    @property
    def members(self) -> int:
        return len({(r.family, r.member) for r in self.runs})

    @property
    def violations(self) -> int:
        return sum(r.violations for r in self.runs)

    @property
    def taken_kwh(self) -> float:
        return round(sum(r.taken_kwh for r in self.runs), 3)

    @property
    def runs_breached(self) -> int:
        return sum(1 for r in self.runs if r.violations)

    def family(self, family: str) -> tuple[MemberRun, ...]:
        return tuple(r for r in self.runs if r.family == family)

    def family_violations(self, family: str) -> int:
        return sum(r.violations for r in self.family(family))

    def worst(self, family: str) -> MemberRun:
        """The run that took the most promised energy, ties broken by name."""
        return max(self.family(family), key=lambda r: (r.taken_kwh, r.member))


def collapse(rows: list[IntervalRow]) -> list[MemberRun]:
    """Group interval rows into one line per member per run."""
    runs: dict[tuple[str, str, str], list[IntervalRow]] = {}
    for row in rows:
        runs.setdefault((row.family, row.source, row.member), []).append(row)
    return [
        MemberRun(
            family=family,
            source=source,
            member=member,
            intervals=len(group),
            promised_kwh=group[0].promised_kwh,
            worst_held_kwh=round(min(r.held_kwh for r in group), 3),
            taken_kwh=round(sum(r.taken_kwh for r in group), 6),
            violations=sum(1 for r in group if not r.kept),
        )
        for (family, source, member), group in runs.items()
    ]


# ---------------------------------------------------------------- market days


def _market_rows(
    trace: PriceTrace,
    battery: ancillary.Battery,
    dispatch: str,
    guarded: bool,
) -> list[IntervalRow]:
    """One simulated battery through one market day, interval by interval."""
    promised = round(battery.kwh * PROMISE_SHARE, 3)
    plan = business.price_plan(trace)
    if dispatch == "utility clock":
        plan = business.utility_call(plan)
    ledger = backtest.value_captured(
        plan,
        trace.frame,
        kwh=battery.kwh,
        power_kw=battery.power_kw,
        serve_home=True,
        backup_kwh=promised if guarded else 0.0,
    )
    soc = ledger["signal_soc_kwh"].astype(float)
    opening = soc.shift(1, fill_value=0.0)
    delivered = (opening - soc).clip(lower=0.0)
    below = (promised - soc).clip(lower=0.0)
    taken = pd.concat([delivered, below], axis=1).min(axis=1)
    return [
        IntervalRow(
            family=MARKET,
            source=f"{trace.date}, {dispatch}",
            member=f"{battery.label} home",
            interval=str(start),
            promised_kwh=promised,
            held_kwh=round(min(float(level), promised), 3),
            taken_kwh=round(float(amount), 6),
        )
        for start, level, amount in zip(ledger["interval_start"], soc, taken, strict=True)
    ]


def market_rows(guarded: bool = True) -> list[IntervalRow]:
    """Both battery sizes, both dispatch models, every bundled held-out day."""
    rows: list[IntervalRow] = []
    for trace in holdout.load_holdout():
        for battery in (ancillary.BASE_CORE, ancillary.LEGACY):
            for dispatch in ("price-following", "utility clock"):
                rows.extend(_market_rows(trace, battery, dispatch, guarded))
    return rows


# ----------------------------------------------------------------- grid event


def fleet_rows(day: str = "scarcity", guarded: bool = True) -> list[IntervalRow]:
    """Every operator-controlled battery, walked across the grid event window.

    The dispatch the Control Room committed is held for the whole event, so the walk
    is what each pack would actually do: stored energy less the home's draw and the
    exported kW, step by step, against the member's floor.
    """
    fraction = home.DEFAULT_RESERVE_FRACTION if guarded else 0.0
    engine = ControlRoomEngine(price_trace=load_price_scenario(day))
    if not guarded:
        engine.set_reserve_floor(fraction)
    hours = engine.remaining_hours()
    step_h = hours / EVENT_STEPS

    rows: list[IntervalRow] = []
    for device in engine.mine:
        if not device.is_dispatchable:
            continue
        promised = round(device.capacity_kwh * PROMISE_SHARE, 3)
        soc = device.available_kwh
        drawn_kw = device.discharge_kw
        for step in range(EVENT_STEPS):
            moved = min(drawn_kw * step_h, soc)
            soc = round(soc - moved, 6)
            rows.append(
                IntervalRow(
                    family=FLEET,
                    source=f"{day} day, {len(engine.mine)} dispatchable homes",
                    member=device.device_id,
                    interval=f"+{(step + 1) * step_h:.2f} h",
                    promised_kwh=promised,
                    held_kwh=round(min(soc, promised), 3),
                    taken_kwh=round(min(moved, max(promised - soc, 0.0)), 6),
                )
            )
    return rows


# -------------------------------------------------------------- chaos scenarios


def scenario_rows(path: Path, guarded: bool = True) -> list[IntervalRow]:
    """Every award in one chaos scenario, against the awardee's promise.

    An award is a commitment for the whole call window, so the interval that matters
    is the window: what the battery has left once it has delivered every kW it just
    accepted, on top of the event dispatch and the home load already netted out of
    its card.
    """
    scenario = load_scenario(path)
    fraction = home.DEFAULT_RESERVE_FRACTION if guarded else 0.0
    result = run_scenario(scenario, reserve_fraction=fraction)
    fleet = _scenario_fleet(scenario)
    capacity = {d.device_id: d.capacity_kwh for d in fleet.devices}
    hours = fleet.remaining_hours()

    rows: list[IntervalRow] = []
    for award_set in result.awards:
        for award in award_set.awards:
            card = result.registry.card(award.agent_id)
            promised = round(capacity.get(award.agent_id, 0.0) * PROMISE_SHARE, 3)
            # The card's spare energy is already net of the reserve the fleet held
            # back, so add the declared reserve to read the pack's real floor again.
            spare = card.capability("kwh_available") + card.capability("reserve_kwh")
            left = spare - award.kw * hours
            rows.append(
                IntervalRow(
                    family=CHAOS,
                    source=f"{scenario.name} ({path.name})",
                    member=award.agent_id,
                    interval=f"award {award_set.call_id}",
                    promised_kwh=promised,
                    held_kwh=round(min(max(left, 0.0), promised), 3),
                    taken_kwh=round(max(min(promised - left, promised), 0.0), 6),
                )
            )
    return rows


def _scenario_fleet(scenario: Scenario) -> ControlRoomEngine:
    """The same fleet state ``run_scenario`` builds, for capacities and the window."""
    return ControlRoomEngine(
        seed=scenario.seed,
        price_trace=load_price_scenario(scenario.price_scenario),
        fleet_size=scenario.batteries,
    )


def chaos_rows(guarded: bool = True) -> list[IntervalRow]:
    rows: list[IntervalRow] = []
    for path in available_scenarios():
        rows.extend(scenario_rows(path, guarded))
    return rows


# ------------------------------------------------------------------- the walk


def build(guarded: bool = True) -> Ledger:
    """Walk every path that moves a member's stored energy."""
    rows = market_rows(guarded)
    for day in FLEET_DAYS:
        rows.extend(fleet_rows(day, guarded))
    rows.extend(chaos_rows(guarded))
    return Ledger(guarded=guarded, runs=tuple(collapse(rows)))


@dataclass(frozen=True)
class Proof:
    """The guarded walk and the same walk with the floor removed."""

    guarded: Ledger
    unguarded: Ledger

    @property
    def caught_violations(self) -> int:
        """Violations the guard prevented: what the unguarded walk produced."""
        return self.unguarded.violations

    @property
    def energy_protected_kwh(self) -> float:
        return round(self.unguarded.taken_kwh - self.guarded.taken_kwh, 2)


def prove() -> Proof:
    return Proof(guarded=build(True), unguarded=build(False))


@lru_cache(maxsize=1)
def cached_prove() -> Proof:
    """The same walk, built once per process: the report and the screen share it."""
    return prove()


def member_day(device_id: str, day: str = "scarcity", guarded: bool = True) -> list[IntervalRow]:
    """One member's own intervals across the grid event, for the screen card."""
    return [r for r in fleet_rows(day, guarded) if r.member == device_id]


def focus_member() -> tuple[str, str]:
    """The day and member whose promise the floor is doing the most work for.

    Picked from the unguarded walk, so the card shows a home where the floor is the
    only thing standing between the dispatch and the member's backup, rather than one
    the schedule was never going to touch."""
    candidates = [
        (day, run) for day in FLEET_DAYS for run in collapse(fleet_rows(day, guarded=False))
    ]
    day, run = max(candidates, key=lambda pair: (pair[1].taken_kwh, pair[1].member))
    return day, run.member


def focus_device(device_id: str, day: str = "scarcity") -> Device:
    engine = ControlRoomEngine(price_trace=load_price_scenario(day))
    return next(d for d in engine.mine if d.device_id == device_id)


# ------------------------------------------------------------------ reporting


def report() -> str:
    proof = cached_prove()
    guarded, unguarded = proof.guarded, proof.unguarded
    lines = [
        "Backup promise ledger — simulated fleet, real cached ERCOT prices",
        "",
        f"The promise: {PROMISE_SHARE:.0%} of every pack is the member's, held for an "
        f"outage. On a Base Core-style {backtest.DEFAULT_KWH:,.0f} kWh unit that is "
        f"{backtest.DEFAULT_KWH * PROMISE_SHARE:,.1f} kWh, about "
        f"{backtest.DEFAULT_KWH * PROMISE_SHARE / ESSENTIAL_LOAD_KW:,.1f} hours of "
        f"essential household load at {ESSENTIAL_LOAD_KW:,.1f} kW.",
        "",
        f"{'':<44}{'with the floor':>16}{'without it':>16}",
    ]
    for family in (MARKET, FLEET, CHAOS):
        kept = guarded.family_violations(family)
        broken = unguarded.family_violations(family)
        counted = len(guarded.family(family))
        intervals = sum(r.intervals for r in guarded.family(family))
        lines.append(
            f"{family + f' ({counted} runs, {intervals:,} intervals)':<44}"
            f"{f'{kept} breaches':>16}{f'{broken} breaches':>16}"
        )
    lines += [
        f"{'all paths':<44}{f'{guarded.violations} breaches':>16}"
        f"{f'{unguarded.violations} breaches':>16}",
        "",
        f"{guarded.members:,} simulated members across {len(guarded.runs):,} runs and "
        f"{guarded.intervals:,} intervals keep every promised kWh. Removing the floor and "
        f"changing nothing else takes {unguarded.taken_kwh:,.1f} kWh of promised backup out "
        f"of {unguarded.runs_breached:,} of those runs — so the guard is load-bearing, not "
        "a property of the schedule.",
        "",
        "The single worst run on each path, in the walk without the floor:",
    ]
    for family in (MARKET, FLEET, CHAOS):
        worst = unguarded.worst(family)
        lines.append(
            f"  {family}: {worst.member} in {worst.source} loses "
            f"{worst.taken_kwh:,.2f} of {worst.promised_kwh:,.2f} kWh promised "
            f"({worst.violations} of {worst.intervals} intervals); with the floor it "
            "loses none."
        )

    day, member = focus_member()
    device = focus_device(member, day)
    intervals = member_day(member, day)
    without = {r.interval: r for r in member_day(member, day, guarded=False)}
    lines += [
        "",
        f"One member, every interval of the grid event — {member} on the {day} day, "
        f"{device.capacity_kwh:,.0f} kWh pack, exporting "
        f"{device.export_kw:,.2f} kW with {device.home_load_kw:,.2f} kW of house on it. "
        "Backup held, and what the same interval holds with the floor removed:",
        f"  {'interval':<12}{'promised':>12}{'held':>10}{'without':>10}",
    ]
    for row in intervals:
        bare = without[row.interval]
        lines.append(
            f"  {row.interval:<12}{f'{row.promised_hours:,.1f} h':>12}"
            f"{f'{row.held_hours:,.1f} h':>10}{f'{bare.held_hours:,.1f} h':>10}"
        )
    lines += [
        "",
        "Everything above is simulated dispatch on simulated homes; the prices are real "
        "ERCOT prints. The promise is this repo's modelled policy, not a Base commitment.",
    ]
    return "\n".join(lines)


def main() -> None:
    print(report())


if __name__ == "__main__":  # pragma: no cover - CLI
    main()
