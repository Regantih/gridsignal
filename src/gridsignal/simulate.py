"""Deterministic chaos runner for the agent mesh.

    python -m gridsignal.simulate scenarios/zone_outage.yaml

Builds the simulated fleet, registers every battery, gateway and zone as an agent with
a signed capability card, injects the scenario's failures, runs a contract-net auction
to close the resulting gap behind a human approval, and writes the whole conversation
to a JSONL trace the dashboard can replay. No network, no keys, no real devices.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from pathlib import Path

from gridsignal.control_room.engine import ControlRoomEngine
from gridsignal.control_room.models import Device, DeviceStatus
from gridsignal.fleet import gateway_ring
from gridsignal.mesh.build import card_for, heartbeat_all, register_fleet
from gridsignal.mesh.cards import CardStatus, derived_signing_key
from gridsignal.mesh.llm import LLMCoordinator
from gridsignal.mesh.messages import MessageBus, MessageKind, read_jsonl
from gridsignal.mesh.negotiation import AwardSet, Coordinator
from gridsignal.mesh.registry import AgentRegistry
from gridsignal.mesh.scenarios import (
    TRACE_DIR,
    Failure,
    Injection,
    Scenario,
    available_scenarios,
    load_scenario,
)
from gridsignal.prices import energy_value_usd
from gridsignal.prices import load_scenario as load_price_scenario

# Bid logging is per-agent for demo-sized fleets and summarised above this, so a
# 10,000-agent run does not write a 10,000-line trace.
BID_LOG_LIMIT = 200


@dataclass(frozen=True)
class RunMetrics:
    """What a judge should be able to read off a chaos run."""

    scenario: str
    agents: int
    rejected_cards: int
    stale_agents: int
    lost_kw: float
    covered_kw: float
    covered_pct: float
    time_to_cover_s: int
    messages: int
    dollars_at_risk: float
    dollars_recovered: float
    escalated: bool
    rounds: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class RunResult:
    scenario: Scenario
    metrics: RunMetrics
    bus: MessageBus
    registry: AgentRegistry
    coordinator: Coordinator
    awards: list[AwardSet]

    def summary(self) -> str:
        m = self.metrics
        return (
            f"{m.scenario}: {m.covered_kw:.1f} of {m.lost_kw:.1f} kW recovered "
            f"({m.covered_pct:.0f}%) in {m.time_to_cover_s}s over {m.messages} messages; "
            f"${m.dollars_recovered:,.2f} of ${m.dollars_at_risk:,.2f} at risk"
            + (" — escalated to a human" if m.escalated else "")
        )


def _targets(failure: Failure, devices: list[Device]) -> list[Device]:
    """Which simulated devices an injection hits."""
    by_id = {d.device_id: d for d in devices}
    if failure.kind is Injection.ZONE_OUTAGE:
        zone = failure.zone
        return [d for d in devices if zone is not None and d.zone == zone]
    if failure.kind is Injection.GATEWAY_OUTAGE:
        ring: list[Device] = []
        for device_id in failure.devices:
            ring.extend(by_id[i] for i in gateway_ring(device_id, len(devices)) if i in by_id)
        return ring
    return [by_id[d] for d in failure.devices if d in by_id]


def run_scenario(scenario: Scenario) -> RunResult:
    """Replay one YAML scenario end to end."""
    trace = load_price_scenario(scenario.price_scenario)
    engine = ControlRoomEngine(seed=scenario.seed, price_trace=trace, fleet_size=scenario.batteries)
    hours = engine.remaining_hours()
    price_mwh = engine.remaining_price_mwh()
    devices = engine.devices

    registry = AgentRegistry(
        key=derived_signing_key(scenario.seed), stale_after_s=scenario.stale_after_s
    )
    bus = MessageBus()
    register_fleet(registry, devices, hours, bus)
    coordinator: Coordinator = LLMCoordinator(registry, bus, enabled=scenario.llm_coordinator)

    silent: set[str] = set()
    silent_after_award: set[str] = set()
    lost_kw = 0.0
    first_failure_s = 0

    for failure in sorted(scenario.failures, key=lambda f: f.at_s):
        registry.advance(max(failure.at_s - registry.now_s, 0))
        heartbeat_all(registry, silent)
        if not first_failure_s:
            first_failure_s = registry.now_s

        if failure.kind is Injection.SILENT_AFTER_AWARD:
            silent_after_award.update(failure.devices)
            continue
        if failure.kind is Injection.LYING_AGENT:
            for device_id in failure.devices:
                registry.tamper(device_id, kw_available=failure.claim_kw)
                bus.send(
                    registry.now_s,
                    MessageKind.REJECT,
                    "registry",
                    device_id,
                    (
                        f"{device_id} card claims {failure.claim_kw:.0f} kW but its signature "
                        "does not verify — rejected, excluded from discovery"
                    ),
                    claim_kw=failure.claim_kw,
                )
            continue
        if failure.kind is Injection.STALE_TELEMETRY:
            silent.update(failure.devices)
            bus.send(
                registry.now_s,
                MessageKind.HEARTBEAT_LOST,
                "registry",
                "coordinator-llm",
                f"{len(failure.devices)} agents stopped sending heartbeats",
                devices=list(failure.devices),
            )
            continue

        hit = [d for d in _targets(failure, devices) if d.is_dispatchable]
        for device in hit:
            lost_kw += device.assigned_kw
            device.assigned_kw = 0.0
            device.status = DeviceStatus.OFFLINE
            silent.add(device.device_id)
            registry.publish(registry.sign(card_for(device, hours)))
        bus.send(
            registry.now_s,
            MessageKind.HEARTBEAT_LOST,
            "telemetry-monitor",
            coordinator.agent_id,
            f"{len(hit)} agents went dark ({failure.kind.value})",
            devices=[d.device_id for d in hit][:20],
            injection=failure.kind.value,
        )

    lost_kw = round(lost_kw, 2)
    dollars_at_risk = energy_value_usd(lost_kw, hours, price_mwh)

    rounds = 0
    awards: list[AwardSet] = []
    covered_kw = 0.0
    gap_kw = lost_kw
    excluded: set[str] = set(silent)
    escalated = False
    time_to_cover_s = 0

    while gap_kw > 0.01 and rounds < 2:
        rounds += 1
        registry.advance(15)
        heartbeat_all(registry, silent)
        call = coordinator.call_for_capacity(gap_kw, hours, exclude=tuple(sorted(excluded)))
        bids = coordinator.collect_bids(call, log_each=scenario.batteries <= BID_LOG_LIMIT)
        award_set = coordinator.propose(call, bids)
        registry.advance(scenario.approval_delay_s)
        heartbeat_all(registry, silent)
        coordinator.approve(call.call_id, scenario.approver)
        awards.append(award_set)
        covered_kw += award_set.covered_kw
        escalated = escalated or award_set.escalated
        time_to_cover_s = registry.now_s - first_failure_s

        # A bidder that accepted and then went quiet does not deliver: its kW goes back
        # into the gap and the coordinator runs another round without it.
        if not silent_after_award:
            break
        winners = {a.agent_id for a in award_set.awards}
        going_silent = silent_after_award & winners
        if not going_silent:
            # The named devices did not win anything, so silence the biggest winners
            # instead — the point of the scenario is a defaulting awardee, not an id.
            ranked = sorted(award_set.awards, key=lambda a: (-a.kw, a.agent_id))
            going_silent = {a.agent_id for a in ranked[: len(silent_after_award)]}
        silent.update(going_silent)
        excluded.update(going_silent)
        registry.advance(scenario.stale_after_s + 1)
        heartbeat_all(registry, silent)
        defaulted = coordinator.defaulters(award_set)
        silent_after_award = set()
        lost_again = round(sum(a.kw for a in award_set.awards if a.agent_id in set(defaulted)), 2)
        covered_kw -= lost_again
        gap_kw = round(lost_kw - covered_kw, 2)
        if gap_kw <= 0.01:
            break

    covered_kw = round(max(covered_kw, 0.0), 2)
    uncovered = round(max(lost_kw - covered_kw, 0.0), 2)
    if uncovered > 0.01:
        escalated = True
        bus.send(
            registry.now_s,
            MessageKind.ESCALATION,
            coordinator.agent_id,
            "fleet-operator",
            f"{uncovered:.1f} kW still uncovered after {rounds} rounds — human action needed",
            uncovered_kw=uncovered,
        )

    # Let the scenario run out its clock so agents that stopped answering are visibly
    # stale in the final registry, the way an operator would see them.
    registry.advance(max(scenario.duration_s - registry.now_s, 0))
    heartbeat_all(registry, silent)
    statuses = registry.statuses()
    metrics = RunMetrics(
        scenario=scenario.name,
        agents=len(registry),
        rejected_cards=sum(1 for s in statuses.values() if s is CardStatus.REJECTED),
        stale_agents=sum(1 for s in statuses.values() if s is CardStatus.STALE),
        lost_kw=lost_kw,
        covered_kw=covered_kw,
        covered_pct=round(100.0 * covered_kw / lost_kw, 1) if lost_kw > 0 else 100.0,
        time_to_cover_s=time_to_cover_s,
        messages=len(bus) + 1,
        dollars_at_risk=dollars_at_risk,
        dollars_recovered=energy_value_usd(covered_kw, hours, price_mwh),
        escalated=escalated,
        rounds=rounds,
    )
    bus.send(
        registry.now_s,
        MessageKind.METRICS,
        coordinator.agent_id,
        "fleet-operator",
        (
            f"{metrics.covered_kw:.1f}/{metrics.lost_kw:.1f} kW covered "
            f"({metrics.covered_pct:.0f}%) in {metrics.time_to_cover_s}s"
        ),
        **metrics.as_dict(),
    )
    return RunResult(
        scenario=scenario,
        metrics=metrics,
        bus=bus,
        registry=registry,
        coordinator=coordinator,
        awards=awards,
    )


def trace_path(scenario: Scenario, directory: Path = TRACE_DIR) -> Path:
    return directory / f"{scenario.slug}.jsonl"


def run_file(path: str | Path, out: Path | None = None) -> RunResult:
    scenario = load_scenario(path)
    result = run_scenario(scenario)
    result.bus.write_jsonl(out or trace_path(scenario))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", nargs="?", help="path to a scenario YAML file")
    parser.add_argument("--out", type=Path, default=None, help="where to write the JSONL trace")
    parser.add_argument("--all", action="store_true", help="run every bundled scenario")
    args = parser.parse_args(argv)

    files = available_scenarios() if args.all else [Path(args.scenario or "")]
    if not args.all and not args.scenario:
        parser.error("pass a scenario file or --all")

    for file in files:
        result = run_file(file, args.out if not args.all else None)
        destination = args.out if (args.out and not args.all) else trace_path(result.scenario)
        print(result.summary())
        print(f"  trace: {destination}")
    return 0


def load_trace(path: Path) -> list[dict[str, object]]:
    """Read a previously written JSONL trace back for replay in the dashboard."""
    return read_jsonl(path)


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
