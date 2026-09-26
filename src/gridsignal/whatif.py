"""What-if console: price a hypothetical against the live control-room state.

The operator types a scenario in plain words ("20% of LZ_HOUSTON offline at 17:00",
"gateway ring 3 dark", "price spike to $3,000") and gets back the dollars at stake,
the kW the rest of the fleet could cover and the plan a human would be asked to
approve. Nothing is mutated and nothing is dispatched: every figure is read off the
same :class:`~gridsignal.control_room.engine.ControlRoomEngine` the Control Room
shows, so a what-if and the real thing can never disagree.

The fleet, the failures and the plan are simulated. Prices are cached historical
ERCOT settlement prices.

    python -m gridsignal.whatif
    python -m gridsignal.whatif --scenario "gateway ring 3 dark" --fleet 10000
"""

from __future__ import annotations

import argparse
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from gridsignal.control_room.engine import ControlRoomEngine
from gridsignal.control_room.models import Device
from gridsignal.fleet import FLEET_SIZE, GATEWAY_RING_SIZE
from gridsignal.guardrails import HCAP_USD
from gridsignal.prices import energy_value_usd

#: Scenarios the console offers when nothing is typed, and what the tests exercise.
EXAMPLES: tuple[str, ...] = (
    "20% of LZ_HOUSTON offline at 17:00",
    "gateway ring 3 dark",
    "price spike to $3,000",
)


class ScenarioKind(Enum):
    ZONE_OUTAGE = "zone_outage"
    RING_DARK = "ring_dark"
    DEVICE_OUTAGE = "device_outage"
    PRICE_SPIKE = "price_spike"


class ParseError(ValueError):
    """The console could not read the scenario as one of the forms it knows."""


@dataclass(frozen=True)
class Scenario:
    """A parsed hypothetical. Times are wall-clock inside the grid-event window."""

    kind: ScenarioKind
    text: str
    share: float = 1.0
    zone: str | None = None
    ring: int | None = None
    device_id: str | None = None
    price_mwh: float | None = None
    at: tuple[int, int] | None = None

    @property
    def label(self) -> str:
        when = "" if self.at is None else f" at {self.at[0]:02d}:{self.at[1]:02d}"
        if self.kind is ScenarioKind.ZONE_OUTAGE:
            return f"{self.share * 100:.0f}% of {self.zone} offline{when}"
        if self.kind is ScenarioKind.RING_DARK:
            return f"Gateway ring {self.ring} dark{when}"
        if self.kind is ScenarioKind.DEVICE_OUTAGE:
            return f"{self.device_id} offline{when}"
        return f"Price spike to ${self.price_mwh:,.0f}/MWh{when}"


@dataclass
class WhatIf:
    """What the console answers: dollars, kW and the plan, all unexecuted."""

    scenario: Scenario
    fleet_size: int
    devices_affected: int
    lost_kw: float
    hours: float
    price_mwh: float
    dollars_at_risk: float
    recoverable_kw: float
    dollars_recoverable: float
    spare_kw: float
    reserve_kwh_touched: float
    headline: str
    plan: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0

    @property
    def uncovered_kw(self) -> float:
        return round(max(self.lost_kw - self.recoverable_kw, 0.0), 2)

    @property
    def dollars_exposed(self) -> float:
        return round(max(self.dollars_at_risk - self.dollars_recoverable, 0.0), 2)


_TIME_RE = re.compile(r"\bat\s+(\d{1,2}):(\d{2})\b")
_SHARE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_ZONE_RE = re.compile(r"\b(lz[_ ][a-z]+)\b")
_RING_RE = re.compile(r"\bring\s+(\d+)\b")
_DEVICE_RE = re.compile(r"\b(bat-\d+)\b")
_PRICE_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)|\b(?:to|at)\s+([\d,]+(?:\.\d+)?)\s*(?:/mwh)?\b")
_OUT_RE = re.compile(r"\b(offline|dark|out|down|lost|fails?|failure)\b")


def parse(text: str) -> Scenario:
    """Read one scenario, or say which forms are understood.

    Deterministic and offline: a small grammar of four shapes, not a model. Anything
    it cannot read is refused with examples rather than guessed at, because a console
    that quietly answers a different question than the one typed is worse than one
    that says no.
    """
    raw = text.strip()
    low = raw.lower()
    if not low:
        raise ParseError(f"Type a scenario, for example: {'; '.join(EXAMPLES)}")

    at: tuple[int, int] | None = None
    match = _TIME_RE.search(low)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 23 or minute > 59:
            raise ParseError(f"{hour:02d}:{minute:02d} is not a time of day")
        at = (hour, minute)
        low = low[: match.start()] + low[match.end() :]

    if "price" in low or "$" in low:
        price = _PRICE_RE.search(low)
        if price is None:
            raise ParseError("Give the price, for example: price spike to $3,000")
        text_price = price.group(1) or price.group(2)
        value = float(text_price.replace(",", ""))
        if value <= 0:
            raise ParseError("A price spike has to be above $0/MWh")
        return Scenario(kind=ScenarioKind.PRICE_SPIKE, text=raw, price_mwh=value, at=at)

    if _OUT_RE.search(low) is None:
        raise ParseError(f"Unrecognised scenario. Try one of: {'; '.join(EXAMPLES)}")

    ring = _RING_RE.search(low)
    if ring is not None:
        return Scenario(kind=ScenarioKind.RING_DARK, text=raw, ring=int(ring.group(1)), at=at)

    device = _DEVICE_RE.search(low)
    if device is not None:
        return Scenario(
            kind=ScenarioKind.DEVICE_OUTAGE, text=raw, device_id=device.group(1).upper(), at=at
        )

    zone = _ZONE_RE.search(low)
    if zone is not None:
        share = _SHARE_RE.search(low)
        fraction = float(share.group(1)) / 100.0 if share else 1.0
        if not 0 < fraction <= 1:
            raise ParseError("A share of a zone has to be above 0% and at most 100%")
        return Scenario(
            kind=ScenarioKind.ZONE_OUTAGE,
            text=raw,
            share=fraction,
            zone=zone.group(1).upper().replace(" ", "_"),
            at=at,
        )

    raise ParseError(f"Unrecognised scenario. Try one of: {'; '.join(EXAMPLES)}")


def _window(engine: ControlRoomEngine, scenario: Scenario) -> tuple[datetime, float]:
    """When the hypothetical starts, and the hours of the event left after it."""
    start = engine.grid_event.started_at
    if scenario.at is not None:
        asked = start.replace(hour=scenario.at[0], minute=scenario.at[1], second=0, microsecond=0)
        start = min(max(asked, engine.grid_event.started_at), engine.grid_event.ends_at)
    start = max(start, engine.snapshot().now)
    hours = max((engine.grid_event.ends_at - start).total_seconds() / 3600.0, 0.0)
    return start, round(hours, 3)


def _affected(engine: ControlRoomEngine, scenario: Scenario) -> list[Device]:
    """Which operator-controlled devices the hypothetical takes out.

    The other tenant's units are never included: their kW was not ours to lose and
    reassigning it is not ours to plan.
    """
    if scenario.kind is ScenarioKind.PRICE_SPIKE:
        return []
    pool = [d for d in engine.mine if d.is_dispatchable]
    if scenario.kind is ScenarioKind.DEVICE_OUTAGE:
        return [d for d in pool if d.device_id == scenario.device_id]
    if scenario.kind is ScenarioKind.RING_DARK:
        ring = scenario.ring or 0
        return [d for d in pool if int(d.device_id.split("-")[1]) % GATEWAY_RING_SIZE == ring]
    in_zone = [d for d in pool if d.zone == scenario.zone]
    # Deterministic sample: the lowest device ids in the zone, so the same question
    # asked twice gives the same answer.
    return in_zone[: math.ceil(scenario.share * len(in_zone))]


def evaluate(engine: ControlRoomEngine, scenario: Scenario) -> WhatIf:
    """Price ``scenario`` against the fleet as it stands, without touching it."""
    started = time.perf_counter()
    start, hours = _window(engine, scenario)
    hurt = _affected(engine, scenario)
    hurt_ids = {d.device_id for d in hurt}
    healthy = [d for d in engine.mine if d.is_dispatchable and d.device_id not in hurt_ids]

    if scenario.kind is ScenarioKind.PRICE_SPIKE:
        price = scenario.price_mwh or 0.0
        spare = round(sum(engine.spare_kw(d) for d in healthy), 2)
        at_risk = energy_value_usd(spare, hours, price)
        result = WhatIf(
            scenario=scenario,
            fleet_size=engine.fleet_size,
            devices_affected=0,
            lost_kw=0.0,
            hours=hours,
            price_mwh=price,
            dollars_at_risk=at_risk,
            recoverable_kw=spare,
            dollars_recoverable=at_risk,
            spare_kw=spare,
            reserve_kwh_touched=0.0,
            headline=(
                f"${at_risk:,.2f} of upside, not exposure: {spare:,.1f} kW is uncommitted "
                f"and worth that over the remaining {hours:.2f} h at ${price:,.2f}/MWh."
            ),
        )
        cap = "" if price <= HCAP_USD else f" Offers are still capped at ${HCAP_USD:,.0f}/MWh."
        result.plan = [
            f"Offer the {spare:,.1f} kW of uncommitted headroom into the spike."
            f" The member's backup reserve is not part of it and stays held.{cap}",
            "Every offer passes the ERCOT guardrail validator before it leaves"
            " (python -m gridsignal.guardrails).",
            "A human approves the offer: this console never dispatches.",
        ]
        result.elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return result

    lost_kw = round(sum(d.assigned_kw for d in hurt), 2)
    price = engine.prices.window_price_mwh(start, engine.grid_event.ends_at)
    at_risk = energy_value_usd(lost_kw, hours, price)
    spare = round(sum(engine.spare_kw(d) for d in healthy), 2)
    recoverable = round(min(lost_kw, spare), 2)
    recovered_usd = min(energy_value_usd(recoverable, hours, price), at_risk)
    zones = sorted({d.zone for d in hurt})

    result = WhatIf(
        scenario=scenario,
        fleet_size=engine.fleet_size,
        devices_affected=len(hurt),
        lost_kw=lost_kw,
        hours=hours,
        price_mwh=price,
        dollars_at_risk=at_risk,
        recoverable_kw=recoverable,
        dollars_recoverable=recovered_usd,
        spare_kw=spare,
        reserve_kwh_touched=0.0,
        headline=(
            f"{lost_kw:,.1f} kW drops out across {len(hurt):,} devices, "
            f"${at_risk:,.2f} at risk over the remaining {hours:.2f} h at "
            f"${price:,.2f}/MWh; {recoverable:,.1f} kW is coverable, leaving "
            f"${round(max(at_risk - recovered_usd, 0.0), 2):,.2f} exposed."
        ),
    )
    uncovered = result.uncovered_kw
    result.plan = [
        f"Quarantine {len(hurt):,} devices in {', '.join(zones) or 'the fleet'} "
        "and stop counting their capacity.",
        f"Reassign {recoverable:,.1f} kW across {len(healthy):,} healthy devices holding "
        f"{spare:,.1f} kW of spare export headroom.",
        (
            "Member backup is not part of the headroom: 0 kWh of reserve is spent "
            "(python -m gridsignal.backup_ledger audits every interval)."
        ),
        (
            "Nothing left uncovered."
            if uncovered <= 0
            else f"{uncovered:,.1f} kW cannot be covered: tell ERCOT before the interval, "
            "do not silently under-deliver."
        ),
        "A human approves the plan: this console only prices it.",
    ]
    result.elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return result


def ask(text: str, fleet_size: int = FLEET_SIZE, engine: ControlRoomEngine | None = None) -> WhatIf:
    """Parse and price one scenario against a fresh (or given) control room."""
    return evaluate(engine or ControlRoomEngine(fleet_size=fleet_size), parse(text))


def report(fleet_size: int = FLEET_SIZE, scenarios: tuple[str, ...] = EXAMPLES) -> list[WhatIf]:
    """Answer every scenario against one control room, as the console does."""
    engine = ControlRoomEngine(fleet_size=fleet_size)
    return [evaluate(engine, parse(text)) for text in scenarios]


def _print(answer: WhatIf) -> None:
    print(f"\n{answer.scenario.label}")
    print(f"  {answer.headline}")
    print(
        f"  devices affected {answer.devices_affected:,} | at risk ${answer.dollars_at_risk:,.2f}"
        f" | recoverable {answer.recoverable_kw:,.1f} kW (${answer.dollars_recoverable:,.2f})"
        f" | exposed ${answer.dollars_exposed:,.2f} | answered in {answer.elapsed_ms:.0f} ms"
    )
    for step in answer.plan:
        print(f"    - {step}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Price a hypothetical against the fleet.")
    parser.add_argument("--scenario", action="append", help="scenario text, repeatable")
    parser.add_argument("--fleet", type=int, default=FLEET_SIZE, help="simulated devices")
    args = parser.parse_args(argv)

    scenarios = tuple(args.scenario) if args.scenario else EXAMPLES
    print(
        f"What-if console — simulated fleet of {args.fleet:,} devices, cached ERCOT prices. "
        "Nothing is dispatched."
    )
    built = time.perf_counter()
    engine = ControlRoomEngine(fleet_size=args.fleet)
    print(f"Control room built in {(time.perf_counter() - built) * 1000:,.0f} ms")
    slowest = 0.0
    for text in scenarios:
        try:
            answer = evaluate(engine, parse(text))
        except ParseError as exc:
            print(f"\n{text}\n  refused: {exc}")
            continue
        _print(answer)
        slowest = max(slowest, answer.elapsed_ms)
    print(f"\nSlowest answer {slowest:.0f} ms at {args.fleet:,} devices.")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
