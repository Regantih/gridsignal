"""Why this exists, what it does, what the evidence is and where it stops.

Every number on this page is recomputed from the code that produces it — there is no
typed-in figure to go stale. ``python -m gridsignal.why`` prints exactly what the app's
**Why** screen shows, so a reader can check the screen against the command:

    python -m gridsignal.why

Everything is a simulated fleet of simulated members, settled against cached historical
ERCOT prices. Nothing here is a statement about Base's real fleet or its real numbers.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import TypeVar

from gridsignal import (
    ancillary,
    degradation,
    deliverability_report,
    holdout,
    insight,
    judgment_report,
    perf,
    replay,
    transport,
)
from gridsignal.control_room import ControlRoomEngine
from gridsignal.control_room.workflow import group_alarms
from gridsignal.fleet import FLEET_SIZE, FOCUS_DEVICE_ID
from gridsignal.jev import evaluate
from gridsignal.prices import load_scenario

#: The scale the demo and the docs talk about.
DEMO_FLEET = 10_000
#: The Why page times the mesh live so the speed claim is not a memory either. It runs
#: at a fraction of the benchmark's scale because a reader is waiting for the screen;
#: the 10,000 and 100,000-agent runs live in docs/PERFORMANCE.md.
PERF_AGENTS = 2_000
PERF_REPEATS = 3
#: Agents in the live transport round the page runs over loopback sockets between real
#: processes. Small for the same reason: the 1,000 and 10,000-agent runs, and the lossy
#: ones, are in docs/PERFORMANCE.md.
TRANSPORT_AGENTS = 150
#: The larger of the two modelled hardware generations, the one the README quotes the
#: ancillary headline on. Base Core-style means modelled from a public interview, not
#: an official specification.
ANCILLARY_UNIT = ancillary.BASE_CORE


T = TypeVar("T")


def _once_per_fleet_size(build_it: Callable[[int], T]) -> Callable[..., T]:
    """Compute a part of the page once per fleet size per process.

    Each part replays a scarcity day, rescores held-out days or times the mesh:
    seconds of work that must not run again while a judge clicks around, and a
    live timing that must not differ between the screen and the command that
    reproduces it.
    """
    cache: dict[int, T] = {}

    @wraps(build_it)
    def wrapper(fleet_size: int = DEMO_FLEET) -> T:
        if fleet_size not in cache:
            cache[fleet_size] = build_it(fleet_size)
        return cache[fleet_size]

    return wrapper


def _signed_usd(amount: float) -> str:
    """Money with the sign in front of the dollar sign, the way the app prints it."""

    return f"{'-' if amount < 0 else '+'}${abs(amount):,.2f}"


@dataclass(frozen=True)
class Claim:
    """One line of the page: a plain-language statement, its number, and its source."""

    label: str
    value: str
    detail: str
    command: str
    #: True when the number is measured on the machine showing the page, so it moves
    #: between runs. Everything else is deterministic and must match to the digit.
    live: bool = False


@dataclass(frozen=True)
class Section:
    title: str
    lead: str
    claims: tuple[Claim, ...]


@dataclass(frozen=True)
class WhyPage:
    problem: Section
    approach: Section
    evidence: Section
    limits: tuple[str, ...]

    @property
    def sections(self) -> tuple[Section, ...]:
        return (self.problem, self.approach, self.evidence)


def _stale_grouping(fleet_size: int) -> tuple[int, int]:
    """Raw alarms and grouped incidents for a dark ring plus a stale-telemetry wave."""
    eng = ControlRoomEngine(price_trace=load_scenario("scarcity"), fleet_size=fleet_size)
    eng.trigger_device_failure(FOCUS_DEVICE_ID)
    eng.inject_stale_telemetry()
    report = group_alarms(eng.alarms)
    return report.alarms, report.incidents


@_once_per_fleet_size
def problem_section(fleet_size: int = DEMO_FLEET) -> Section:
    """What goes wrong in a distributed fleet that a single battery never shows."""
    run = replay.run(fleet_size=fleet_size)
    alarms, incidents = _stale_grouping(fleet_size)
    view = insight.summarize(insight.analyze())
    return Section(
        "The problem",
        "A home battery fleet earns its money in the few hours a year when the grid is "
        "short — which is exactly when gateways drop, telemetry goes stale and an "
        "operator is reading thousands of alarms.",
        (
            Claim(
                "Three faults at the peak",
                f"${run.dollars_at_risk:,.0f} at risk",
                f"{run.kw_lost:,.0f} kW of committed capacity lost across "
                f"{run.fault_minutes:,.0f} simulated minutes at "
                f"${run.price_mwh:,.0f}/MWh on {run.date} in {run.location}.",
                "python -m gridsignal.replay",
            ),
            Claim(
                "One alarm per battery per symptom is not a workload",
                f"{alarms:,} alarms",
                f"The same faults grouped by cause are {incidents:,} incidents an "
                f"operator can actually work.",
                f"python -m gridsignal.workflow --devices {fleet_size} --stale-wave",
            ),
            Claim(
                "The day-ahead curve does not show where the money is",
                f"{view.scarcity_visible_share:.0%} visible in advance",
                f"On the bundled scarcity days ${view.scarcity_blind_usd:,.2f} per "
                f"battery only appears in real time — about "
                f"{view.ordinary_days_equivalent} ordinary trading days.",
                "python -m gridsignal.insight",
            ),
        ),
    )


@_once_per_fleet_size
def approach_section(fleet_size: int = DEMO_FLEET) -> Section:
    """How the product answers it, with the number that shows the rule is real."""
    eng = ControlRoomEngine(price_trace=load_scenario("scarcity"), fleet_size=fleet_size)
    incident = eng.trigger_device_failure(FOCUS_DEVICE_ID)
    eng.approve_recovery()
    snap = eng.snapshot()
    delivery = deliverability_report.run()
    split = ancillary.holdout_summary(ANCILLARY_UNIT)
    return Section(
        "The approach",
        "One engine holds the fleet state; the mesh, the Control Room and the Member "
        "App all read it. Safety is deterministic: reserves and market rules are hard "
        "rules, and the model is a second opinion that can only escalate.",
        (
            Claim(
                "The member's home comes before the grid",
                f"{split.reserve_violations} reserve breaches",
                f"Across {split.days} held-out days of co-optimized energy and "
                f"ancillary awards, no plan ever spent a member's backup reserve.",
                "python -m gridsignal.ancillary",
            ),
            Claim(
                "A human approves anything consequential",
                f"${incident.dollars_recovered:,.0f} recovered",
                f"{incident.lost_kw:,.0f} kW came back only after one operator click; "
                f"nothing in a scenario file can stand in for it.",
                "python -m gridsignal.workflow",
            ),
            Claim(
                "The fleet proves an award before it promises it",
                f"{delivery.undeliverable_awards} of {delivery.awards:,} awards",
                f"would have committed {delivery.undeliverable_kw:,.2f} kW no battery "
                f"could hold for the whole window; they are trimmed or refused, with "
                f"the reason logged.",
                "python -m gridsignal.deliverability_report",
            ),
            Claim(
                "Assets someone else controls are out of reach",
                f"{len(eng.mine):,} of {snap.total_devices:,} dispatchable",
                "The rest belong to a partner utility's tenant: visible to the "
                "operator, never biddable by this fleet.",
                "python -m gridsignal.pipeline --scenario scarcity --devices 10000",
            ),
        ),
    )


@_once_per_fleet_size
def evidence_section(fleet_size: int = DEMO_FLEET) -> Section:
    """What was measured, including the results that did not flatter the product."""
    home_first = holdout.summarize(holdout.evaluate())
    run = replay.run(fleet_size=fleet_size)
    deltas = evaluate.fixture_deltas()
    unchanged_kw = sum(not d.changed for d in deltas)
    different_cause = sum(d.rules_root_cause != d.jev_root_cause for d in deltas)
    split = ancillary.holdout_summary(ANCILLARY_UNIT)
    unrestricted = ancillary.holdout_summary(ANCILLARY_UNIT, rules=ancillary.ALL_PRODUCTS)
    top_day, top_share = split.top_day_share
    legacy = next(
        r
        for r in degradation.evaluate_all()
        if r.split == "held-out" and r.model.unit_type is degradation.UnitType.LEGACY
    )
    judgment = judgment_report.build()
    rules = judgment.blind[judgment_report.RULES]
    jev = judgment.blind[judgment_report.JEV]
    wire = transport.measure(TRANSPORT_AGENTS, workers=2)
    after = perf.measure(PERF_AGENTS, repeats=PERF_REPEATS)
    before = perf.measure_before(PERF_AGENTS, repeats=PERF_REPEATS)
    speedup = before.total_p50_ms / after.total_p50_ms if after.total_p50_ms else 0.0
    return Section(
        "The evidence",
        "Days the parameters were never fitted on, a pack of safety questions committed "
        "before it was scored, and a replay of a real scarcity day at Base scale.",
        (
            Claim(
                "Held-out days, rescored at every revision",
                f"{home_first.days_won} of {home_first.days} days beat the baseline",
                f"Mean ${home_first.mean_uplift_usd:,.2f} and median "
                f"${home_first.median_uplift_usd:,.2f} per battery per day against a "
                f"fixed charge/discharge clock — the median is the honest one.",
                "python -m gridsignal.holdout",
            ),
            Claim(
                "Recovery at fleet scale",
                f"{run.recovered_share:.0%} of the dollars at risk",
                f"${run.dollars_recovered:,.0f} of ${run.dollars_at_risk:,.0f} came "
                f"back — priced only over the {run.recovery_hours:,.2f} h left once the "
                f"fix landed, never the minutes the fleet spent degraded — and only "
                f"possible because "
                f"{run.spare_kw_at_fault:,.0f} kW of spare headroom existed "
                f"({run.headroom_cover:,.1f}x the kW lost). Runtime "
                f"{run.runtime_s:,.1f} s, machine-dependent.",
                "python -m gridsignal.replay",
            ),
            Claim(
                "Ancillary value is real, concentrated, and capped by the pilot rules",
                f"median ${split.median_uplift_usd:,.2f} per battery per day",
                f"On a {split.battery} bidding only what ERCOT's ADER pilot allows an "
                f"aggregation of home batteries to sell (ECRS and Non-Spin, 90 MW each "
                f"per QSE) the mean is ${split.mean_uplift_usd:,.2f} and {top_day} alone "
                f"carries {top_share:.0%} of the total across {split.days} held-out days. "
                f"Unrestricted the same days pay a median of "
                f"${unrestricted.median_uplift_usd:,.2f} \u2014 a comparison, not an offer: "
                f"Reg Down is where that money is and an ADER may not sell it.",
                "python -m gridsignal.ancillary",
            ),
            Claim(
                "Wear-aware dispatch pays on one battery type, not both",
                f"{_signed_usd(legacy.gated_net_usd - legacy.net_usd)} per day, "
                f"{legacy.model.label}",
                f"{legacy.cycles_saved:,.2f} of {legacy.cycles:,.2f} equivalent full "
                f"cycles skipped on held-out days; on a newer, cheaper-to-cycle unit "
                f"the same gate never binds.",
                "python -m gridsignal.degradation",
            ),
            Claim(
                "The model is a second opinion, not the decider",
                f"{judgment_report.RULES} {rules.correct} of {rules.total}, "
                f"{judgment_report.jev_label(judgment)} {jev.correct} of {jev.total}",
                f"Blind score on a safety pack and answer key committed before the "
                f"first run; the deterministic layer won every one of the "
                f"{len(judgment.disagreements)} disagreements, so the rules and the "
                f"vetoes decide and Jev escalates.",
                "python -m gridsignal.judgment_report",
            ),
            Claim(
                "What the model changes when it is switched off",
                f"{unchanged_kw} of {len(deltas)} scenarios: not one kW",
                f"Every bundled scenario replayed twice on the same seeds, once with the "
                f"recorded Jev answers and once with them withheld: the covered kW is "
                f"identical in {unchanged_kw} of {len(deltas)}, while the reported root "
                f"cause differs in {different_cause}. Since the model has no approve "
                f"path, what it changes is the explanation an operator reads and when "
                f"they are asked — never the dispatch.",
                "python -m gridsignal.jev.evaluate",
            ),
            Claim(
                "It runs at fleet scale",
                f"{speedup:,.2f}x faster than the first implementation",
                f"{PERF_AGENTS:,} agents end to end, timed live on this machine "
                f"({before.total_p50_ms:,.0f} ms to {after.total_p50_ms:,.0f} ms). "
                f"Absolute times are machine-dependent; the 10,000 and 100,000-agent "
                f"runs are in docs/PERFORMANCE.md.",
                "python -m gridsignal.perf --before",
                live=True,
            ),
            Claim(
                "It survives a real transport, not just a function call",
                f"p50 {wire.p50_ms:,.0f} ms, p95 {wire.p95_ms:,.0f} ms detect to award",
                f"{wire.agents:,} agents in {wire.worker_processes} separate processes, "
                f"multiplexed over {wire.connections} loopback TCP sockets "
                f"({wire.agents_per_connection:,.0f} agents each, so the fleet fits a "
                f"laptop's file-descriptor limit), cards signed in the agent process and "
                f"verified in the coordinator's: {wire.frames:,} frames at "
                f"{wire.frames_per_s:,.0f}/s, {wire.coverage_pct:.0f}% of the call "
                f"covered. Local loopback, not a WAN — no gateway, cellular or inverter "
                f"time. At 10,000 agents with --drop 0.05 the call still clears and the "
                f"tail moves; see docs/PERFORMANCE.md.",
                "python -m gridsignal.transport",
                live=True,
            ),
        ),
    )


@_once_per_fleet_size
def limits(fleet_size: int = DEMO_FLEET) -> tuple[str, ...]:
    """What this build does not show. Computed too, so the caveats cannot drift."""
    home_first = holdout.summarize(holdout.evaluate())
    judgment = judgment_report.build()
    cal = judgment.calibration
    flag = ancillary.procurement_flag()
    wear = degradation.WEAR_MODELS[0]
    out = [
        f"The fleet, the members, the homes and the operator overrides are simulated. "
        f"Prices are cached historical ERCOT settlement data; {fleet_size:,} simulated "
        f"batteries are not {fleet_size:,} real ones.",
        f"The held-out result rests on {home_first.days} days. That is enough to keep "
        f"the policy honest and not enough to call it a forecast of annual revenue.",
        "Tuning the judgment model happens on a simulated override log. "
        + judgment_report.calibration_reading(cal),
        f"Wear cost is this repository's assumption — "
        f"${wear.wear_usd_per_mwh:,.0f}/MWh for a {wear.label} — not vendor data.",
        "The transport benchmark is local loopback between processes on one machine. "
        "It measures this software's own overhead under a real socket, not a field "
        "network: no gateway, no cellular link, no inverter.",
        "Nothing here contacts a real device, a real utility or a real ERCOT system, "
        "and no result should be read as a statement about Base's own fleet.",
    ]
    if flag is not None:
        out.insert(
            3,
            f"Offers are priced as a price taker. In the unrestricted comparison a "
            f"{fleet_size:,}-battery Reg Down offer is {flag.fleet_mw:,.0f} MW against "
            f"{flag.procured_mw:,.0f} MW ERCOT published for {flag.date}: "
            f"{flag.share:.0%} of the product. The pilot-restricted offer is held to "
            f"90 MW per product by the governing document, and either way the "
            f"ancillary dollars are an upper bound.",
        )
    return tuple(out)


@_once_per_fleet_size
def build(fleet_size: int = DEMO_FLEET) -> WhyPage:
    """The whole page: every figure recomputed from the module that produces it."""
    return WhyPage(
        problem=problem_section(fleet_size),
        approach=approach_section(fleet_size),
        evidence=evidence_section(fleet_size),
        limits=limits(fleet_size),
    )


def lines(page: WhyPage) -> list[str]:
    out: list[str] = ["GridSignal — why this exists (every number recomputed just now)", ""]
    for section in page.sections:
        out.append(section.title.upper())
        out.append(f"  {section.lead}")
        out.append("")
        for claim in section.claims:
            out.append(f"  {claim.label}")
            out.append(f"    {claim.value}")
            out.append(f"    {claim.detail}")
            out.append(f"    reproduce: {claim.command}")
            out.append("")
    out.append("LIMITS")
    for limit in page.limits:
        out.append(f"  - {limit}")
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Print the app's Why page from live code.")
    parser.add_argument("--devices", type=int, default=DEMO_FLEET, help="simulated fleet size")
    args = parser.parse_args(argv)
    print("\n".join(lines(build(max(args.devices, FLEET_SIZE)))))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
