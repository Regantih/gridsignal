"""Deterministic chaos runner for the agent mesh.

    python -m gridsignal.simulate scenarios/zone_outage.yaml

Builds the simulated fleet, registers every battery, gateway and zone as an agent with
a signed capability card, injects the scenario's failures, runs a contract-net auction
to close the resulting gap behind a human approval, and writes the whole conversation
to a JSONL trace the dashboard can replay. No network, no keys, no real devices.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from gridsignal.control_room.engine import ControlRoomEngine
from gridsignal.control_room.models import Device, DeviceStatus
from gridsignal.fleet import GATEWAY_RING_SIZE, gateway_ring
from gridsignal.jev import rules
from gridsignal.jev.client import JevClient, JevResponse, Source
from gridsignal.jev.policy import ApprovalDecision, ApprovalPolicy, decide
from gridsignal.jev.questions import (
    BACKUP_RISK,
    ROOT_CAUSE,
    TRUST_PREFIX,
    IncidentSnapshot,
    Suspect,
    incident_questions,
)
from gridsignal.mesh.build import card_for, gateway_id, heartbeat_all, register_fleet
from gridsignal.mesh.cards import AgentCard, CardStatus, derived_signing_key
from gridsignal.mesh.llm import LLMCoordinator
from gridsignal.mesh.messages import MessageBus, MessageKind, read_jsonl
from gridsignal.mesh.negotiation import BACKUP_RESERVE_KWH, AwardSet, Bid, Coordinator
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
# How many agents get a second opinion from Jev per round. The HMAC check already
# catches forged cards; these questions are about validly signed agents behaving oddly.
SUSPECTS_PER_ROUND = 3
# Nominal grid frequency of the simulated interconnection, and the ERCOT Fast Frequency
# Response trigger it models: auto-deployment at 59.85 Hz within 15 cycles. Every Hz in
# this repo is simulated; nothing here reads a real frequency feed.
NOMINAL_HZ = 60.0
FFR_TRIGGER_HZ = 59.85
FFR_DEADLINE_CYCLES = 15
CYCLES_PER_SECOND = 60.0
#: Simulated latency of a battery acting on the rule already signed into its own card:
#: measure frequency, deploy, no round trip to the coordinator. Inside the 15-cycle
#: window it is being measured against, and it is an assumption, not a measurement.
LOCAL_DEPLOY_CYCLES = 12


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
    jev_source: str
    jev_model: str
    root_cause: str
    root_cause_truth: str
    root_cause_correct: bool
    root_cause_confidence: float
    backup_risk: float
    human_approvals: int
    auto_approvals: int
    decision_latency_ms: float
    distrusted_agents: int
    # Grid-stress drill measurements. All simulated.
    min_frequency_hz: float = NOMINAL_HZ
    extra_demand_kw: float = 0.0
    islanded_agents: int = 0
    resynced_agents: int = 0
    conflicting_cards: int = 0
    backup_violations: int = 0
    #: kW deployed by batteries acting on their own cards, with no coordinator involved.
    self_deployed_kw: float = 0.0
    #: Cycles from the simulated under-frequency dip to the first committed kW, or None
    #: when the drill has no frequency event.
    response_cycles: int | None = None

    @property
    def within_ffr_deadline(self) -> bool:
        return self.response_cycles is not None and self.response_cycles <= FFR_DEADLINE_CYCLES

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
    decisions: list[ApprovalDecision] = field(default_factory=list)
    responses: list[JevResponse] = field(default_factory=list)

    @property
    def jev_label(self) -> str:
        source = self.metrics.jev_source
        if source == Source.FALLBACK.value:
            return "Jev offline, rules fallback"
        if source == Source.FIXTURE.value:
            return "Jev (recorded answers)"
        return "Jev (live)"

    def summary(self) -> str:
        m = self.metrics
        return (
            f"{m.scenario}: {m.covered_kw:.1f} of {m.lost_kw:.1f} kW recovered "
            f"({m.covered_pct:.0f}%) in {m.time_to_cover_s}s over {m.messages} messages; "
            f"${m.dollars_recovered:,.2f} of ${m.dollars_at_risk:,.2f} at risk"
            + (" — escalated to a human" if m.escalated else "")
            + (
                f"\n  {self.jev_label}: root cause {m.root_cause} "
                f"(confidence {m.root_cause_confidence:.2f}, truth {m.root_cause_truth}), "
                f"backup risk {m.backup_risk:.2f}, {m.auto_approvals} auto / "
                f"{m.human_approvals} human approvals, median {m.decision_latency_ms:.0f} ms"
            )
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


def _snapshot(
    scenario: Scenario,
    registry: AgentRegistry,
    devices: list[Device],
    bids: list[Bid],
    award_set: AwardSet,
    lost_kw: float,
    dollars_at_risk: float,
    price_mwh: float,
    hours: float,
    grid: GridConditions | None = None,
) -> IncidentSnapshot:
    """Everything Jev is told about the incident — simulated fleet data only."""
    offline = [d for d in devices if d.status is DeviceStatus.OFFLINE]
    by_zone: dict[str, list[Device]] = {}
    for device in devices:
        by_zone.setdefault(device.zone, []).append(device)
    dark_zones = tuple(
        sorted(z for z, group in by_zone.items() if group and all(d in offline for d in group))
    )
    statuses = registry.statuses()
    awarded = {a.agent_id: a for a in award_set.awards}
    bid_by_agent = {b.agent_id: b for b in bids}
    plan_socs = [bid_by_agent[a].soc for a in awarded if a in bid_by_agent]
    spare = [
        registry.card(a).capability("kwh_available") - awarded[a].kw * hours
        for a in awarded
        if a in statuses
    ]
    median_kw = 0.0
    if bids:
        ordered = sorted(b.kw for b in bids)
        median_kw = ordered[len(ordered) // 2]

    suspects: list[Suspect] = []
    flagged = [a for a, s in sorted(statuses.items()) if s is CardStatus.REJECTED]
    biggest = [b.agent_id for b in sorted(bids, key=lambda b: (-b.kw, b.agent_id))]
    for agent_id in list(dict.fromkeys(flagged + biggest))[:SUSPECTS_PER_ROUND]:
        card = registry.card(agent_id)
        bid = bid_by_agent.get(agent_id)
        suspects.append(
            Suspect(
                agent_id=agent_id,
                card_status=statuses[agent_id].value,
                signature_valid=statuses[agent_id] is not CardStatus.REJECTED,
                heartbeat_age_s=max(registry.now_s - card.last_heartbeat_s, 0),
                claimed_kw=card.capability("kw_available"),
                claimed_kwh=card.capability("kwh_available"),
                soc=card.capability("soc"),
                bid_kw=bid.kw if bid else 0.0,
                fleet_median_kw=median_kw,
            )
        )

    return IncidentSnapshot(
        scenario=scenario.slug,
        agents=len(registry),
        batteries=len(devices),
        offline_agents=len(offline),
        offline_zones=dark_zones,
        offline_gateway_rings=len({gateway_id(d.device_id) for d in offline}),
        largest_offline_group_in_one_ring=max(
            Counter(gateway_id(d.device_id) for d in offline).values(), default=0
        ),
        gateway_ring_size=GATEWAY_RING_SIZE,
        rejected_cards=sum(1 for s in statuses.values() if s is CardStatus.REJECTED),
        stale_agents=sum(1 for s in statuses.values() if s is CardStatus.STALE),
        max_telemetry_age_s=max(
            (registry.now_s - registry.card(a).last_heartbeat_s for a in statuses), default=0
        ),
        lost_kw=lost_kw,
        price_usd_mwh=price_mwh,
        event_hours=hours,
        dollars_at_risk=dollars_at_risk,
        bidders=len(bids),
        proposed_kw=award_set.covered_kw,
        uncovered_kw=award_set.uncovered_kw,
        plan_agents=len(award_set.awards),
        plan_mean_soc=round(sum(plan_socs) / len(plan_socs), 4) if plan_socs else 1.0,
        plan_min_spare_kwh=round(min(spare), 3) if spare else BACKUP_RESERVE_KWH,
        backup_reserve_kwh=BACKUP_RESERVE_KWH,
        suspects=tuple(suspects),
        frequency_hz=grid.min_hz if grid else NOMINAL_HZ,
        grid_side_kw=grid.grid_side_kw if grid else 0.0,
        islanded_agents=grid.islanded if grid else 0,
        self_deployed_kw=grid.self_deployed_kw if grid else 0.0,
    )


@dataclass
class GridConditions:
    """Simulated grid-side state a drill adds on top of a plain component failure."""

    min_hz: float = NOMINAL_HZ
    grid_side_kw: float = 0.0
    islanded: int = 0
    self_deployed_kw: float = 0.0


def _log_decision(
    bus: MessageBus,
    t_s: int,
    response: JevResponse,
    decision: ApprovalDecision,
    call_id: str,
) -> None:
    """Put Jev's answers, confidence and latency into the same log as the agent traffic."""
    for question_id, answer in sorted(response.answers.items()):
        if question_id == ROOT_CAUSE:
            summary = f"Root cause: {answer.value.replace('_', ' ')}"
        elif question_id == BACKUP_RISK:
            summary = f"Backup risk to members: {answer.value}"
        else:
            agent = question_id[len(TRUST_PREFIX) :]
            verdict = "trustworthy" if answer.yes else "NOT trustworthy"
            summary = f"{agent} looks {verdict} beyond its signature"
        bus.send(
            t_s,
            MessageKind.JEV_DECISION,
            "jev",
            "coordinator",
            f"{summary} (confidence {answer.confidence:.2f}, {response.latency_ms:.0f} ms)",
            call_id=call_id,
            question=question_id,
            answer=answer.value,
            confidence=answer.confidence,
            probabilities=answer.probabilities,
            latency_ms=round(response.latency_ms, 1),
            model=response.model,
            jev_source=response.source.value,
        )
    bus.send(
        t_s,
        MessageKind.AUTO_APPROVAL if decision.auto_approved else MessageKind.ESCALATION,
        "jev",
        "fleet-operator",
        (
            f"Auto-approving {call_id}: {decision.reason}"
            if decision.auto_approved
            else f"Routing {call_id} to the human gate: {decision.reason}"
        ),
        call_id=call_id,
        **decision.as_dict(),
    )


def run_scenario(
    scenario: Scenario,
    jev: JevClient | None = None,
    policy: ApprovalPolicy | None = None,
) -> RunResult:
    """Replay one YAML scenario end to end.

    ``jev`` defaults to recorded answers for this scenario, so the default run needs no
    key and no network; pass :meth:`JevClient.offline` for the rules-only comparison.
    """
    client = jev or JevClient.for_scenario(scenario.slug, fallback=rules.answers)
    if client.fallback is None:
        client.fallback = rules.answers
    gate = policy or ApprovalPolicy()
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
    # Simulated grid-side state the drills add on top of a plain device failure.
    min_hz = NOMINAL_HZ
    dip_at_s: int | None = None
    extra_demand_kw = 0.0
    coordinator_back_s = 0
    islanded: set[str] = set()
    island_restore_s = 0
    conflicting: set[str] = set()

    for failure in sorted(scenario.failures, key=lambda f: f.at_s):
        registry.advance(max(failure.at_s - registry.now_s, 0))
        heartbeat_all(registry, silent)
        if not first_failure_s:
            first_failure_s = registry.now_s

        if failure.kind in (Injection.GENERATION_TRIP, Injection.FREQUENCY_DIP):
            hz = failure.hz or FFR_TRIGGER_HZ
            min_hz = min(min_hz, hz)
            if dip_at_s is None and hz <= FFR_TRIGGER_HZ:
                dip_at_s = registry.now_s
            extra_demand_kw += failure.kw
            lost = f", {failure.kw:.0f} kW of simulated supply lost" if failure.kw else ""
            bus.send(
                registry.now_s,
                MessageKind.GRID_STRESS,
                "grid-model",
                coordinator.agent_id,
                f"Simulated frequency {hz:.2f} Hz{lost} ({failure.kind.value})",
                injection=failure.kind.value,
                hz=hz,
                kw=failure.kw,
                simulated=True,
            )
            continue
        if failure.kind is Injection.LOAD_RAMP:
            extra_demand_kw += failure.kw
            bus.send(
                registry.now_s,
                MessageKind.GRID_STRESS,
                "grid-model",
                coordinator.agent_id,
                (f"Simulated large-load ramp: +{failure.kw:.0f} kW of demand, reserves tightening"),
                injection=failure.kind.value,
                kw=failure.kw,
                simulated=True,
            )
            continue
        if failure.kind is Injection.COORDINATOR_DOWN:
            coordinator_back_s = max(coordinator_back_s, registry.now_s + failure.for_s)
            bus.send(
                registry.now_s,
                MessageKind.HEARTBEAT_LOST,
                coordinator.agent_id,
                "fleet-operator",
                (
                    f"Coordinator unreachable for {failure.for_s}s — no auction can be "
                    "run while it is down"
                ),
                injection=failure.kind.value,
                for_s=failure.for_s,
            )
            continue
        if failure.kind is Injection.CONFLICTING_BIDS:
            for device_id in failure.devices:
                card = registry.card(device_id)
                registry.publish(
                    registry.sign(
                        AgentCard(
                            agent_id=card.agent_id,
                            kind=card.kind,
                            zone=card.zone,
                            capabilities={**card.capabilities, "kw_available": failure.claim_kw},
                            health=card.health,
                            last_heartbeat_s=card.last_heartbeat_s,
                        )
                    )
                )
                conflicting.add(device_id)
                bus.send(
                    registry.now_s,
                    MessageKind.CONFLICT,
                    device_id,
                    coordinator.agent_id,
                    (
                        f"{device_id} re-published a validly signed card claiming "
                        f"{failure.claim_kw:.1f} kW, contradicting its telemetry"
                    ),
                    claim_kw=failure.claim_kw,
                    telemetry_kw=round(card.capability("kw_available"), 3),
                )
            continue
        if failure.kind is Injection.ISLAND:
            zone = failure.zone
            homes = [d for d in devices if zone is not None and d.zone == zone]
            if failure.for_s:
                island_restore_s = failure.at_s + failure.for_s
            for device in homes:
                lost_kw += device.assigned_kw
                device.assigned_kw = 0.0
                islanded.add(device.device_id)
                # An islanded home keeps every stored kWh for itself, so its card offers
                # nothing to the grid and the auction cannot award its backup away.
                card = card_for(device, hours)
                registry.publish(
                    registry.sign(
                        AgentCard(
                            agent_id=card.agent_id,
                            kind=card.kind,
                            zone=card.zone,
                            capabilities={**card.capabilities, "kw_available": 0.0},
                            health=card.health,
                            last_heartbeat_s=registry.now_s,
                        )
                    )
                )
            bus.send(
                registry.now_s,
                MessageKind.ISLANDED,
                "grid-model",
                coordinator.agent_id,
                (
                    f"{len(homes)} homes in {zone} islanded on their own batteries — "
                    "local backup first, no export while islanded"
                ),
                injection=failure.kind.value,
                zone=zone,
                homes=len(homes),
                simulated=True,
            )
            continue
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

    lost_kw = round(lost_kw + extra_demand_kw, 2)
    dollars_at_risk = energy_value_usd(lost_kw, hours, price_mwh)

    rounds = 0
    awards: list[AwardSet] = []
    covered_kw = 0.0
    gap_kw = lost_kw
    excluded: set[str] = set(silent) | islanded
    backup_violations = 0
    first_commit_s: int | None = None
    response_cycles: int | None = None

    # Under-frequency: every battery already carries the rule in its signed card, so it
    # deploys its pre-agreed share locally instead of waiting for an auction. Nothing
    # safety-critical is decided here — the share is carved out of spare power above the
    # homeowner's reserve, and the coordinator still has to cover whatever is left.
    self_deployed_kw = 0.0
    if dip_at_s is not None:
        deployers = [d for d in devices if d.is_dispatchable and d.device_id not in excluded]
        pledged = round(sum(registry.card(d.device_id).capability("ffr_kw") for d in deployers), 2)
        remaining = gap_kw
        for device in deployers:
            if remaining <= 0.01:
                break
            kw = min(registry.card(device.device_id).capability("ffr_kw"), remaining)
            if kw <= 0.0:
                continue
            # The deployed kW becomes a commitment, so the card it republishes offers
            # only what is left: the auction cannot sell the same kW a second time.
            device.assigned_kw = round(device.assigned_kw + kw, 3)
            registry.publish(registry.sign(card_for(device, hours)))
            self_deployed_kw = round(self_deployed_kw + kw, 2)
            remaining = round(remaining - kw, 3)
        if self_deployed_kw > 0:
            covered_kw = self_deployed_kw
            gap_kw = round(lost_kw - covered_kw, 2)
            response_cycles = LOCAL_DEPLOY_CYCLES
            first_commit_s = dip_at_s
            bus.send(
                dip_at_s,
                MessageKind.SELF_DEPLOY,
                "batteries",
                "grid-model",
                (
                    f"Simulated {min_hz:.2f} Hz crossed {FFR_TRIGGER_HZ:.2f} Hz: "
                    f"{self_deployed_kw:,.0f} kW deployed from the pre-agreed rule on each "
                    f"card in {LOCAL_DEPLOY_CYCLES} cycles, no coordinator involved"
                ),
                hz=round(min_hz, 3),
                kw=self_deployed_kw,
                cycles=LOCAL_DEPLOY_CYCLES,
                pledged_kw=pledged,
                simulated=True,
            )
    grid = GridConditions(
        min_hz=min_hz,
        grid_side_kw=extra_demand_kw,
        islanded=len(islanded),
        self_deployed_kw=self_deployed_kw,
    )
    escalated = False
    time_to_cover_s = 0
    decisions: list[ApprovalDecision] = []
    responses: list[JevResponse] = []

    while gap_kw > 0.01 and rounds < 2:
        rounds += 1
        registry.advance(15)
        heartbeat_all(registry, silent)
        if coordinator_back_s > registry.now_s:
            # Nobody runs the auction while the coordinator is unreachable; the fleet
            # waits it out, and the clock keeps running against the event.
            waited = coordinator_back_s - registry.now_s
            registry.advance(waited)
            heartbeat_all(registry, silent)
            bus.send(
                registry.now_s,
                MessageKind.ESCALATION,
                coordinator.agent_id,
                "fleet-operator",
                f"Coordinator back after {waited}s offline — starting the auction now",
                offline_s=waited,
            )
            coordinator_back_s = 0
            if self_deployed_kw > 0:
                # Reconciliation: the fleet reports what it already deployed and the
                # coordinator asks only for the remainder, so local action and awarded
                # capacity are never counted twice.
                bus.send(
                    registry.now_s,
                    MessageKind.RECONCILE,
                    "batteries",
                    coordinator.agent_id,
                    (
                        f"{self_deployed_kw:,.0f} kW already deployed locally; "
                        f"auctioning the remaining {gap_kw:,.0f} kW only"
                    ),
                    self_deployed_kw=self_deployed_kw,
                    remaining_kw=gap_kw,
                    lost_kw=lost_kw,
                )
        call = coordinator.call_for_capacity(gap_kw, hours, exclude=tuple(sorted(excluded)))
        bids = coordinator.collect_bids(call, log_each=scenario.batteries <= BID_LOG_LIMIT)
        award_set = coordinator.propose(call, bids)

        # Jev decides; the human still approves whenever Jev is unsure, the plan is
        # risky to homeowner backup, or too much money is on the line.
        snapshot = _snapshot(
            scenario,
            registry,
            devices,
            bids,
            award_set,
            lost_kw,
            dollars_at_risk,
            price_mwh,
            hours,
            grid,
        )
        questions = incident_questions(snapshot)
        response = client.ask(snapshot.as_state(), questions)
        decision = decide(
            response,
            dollars=energy_value_usd(award_set.covered_kw, hours, price_mwh),
            covered_fully=not award_set.escalated,
            human_approver=scenario.approver,
            policy=gate,
        )
        responses.append(response)
        decisions.append(decision)
        _log_decision(bus, registry.now_s, response, decision, call.call_id)

        if not decision.auto_approved:
            registry.advance(scenario.approval_delay_s)
            heartbeat_all(registry, silent)
        coordinator.approve(call.call_id, decision.approver)
        if first_commit_s is None and award_set.covered_kw > 0:
            first_commit_s = registry.now_s
        backup_violations += sum(
            1
            for award in award_set.awards
            if registry.card(award.agent_id).capability("kwh_available") - award.kw * hours
            < BACKUP_RESERVE_KWH
        )
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

    # The distribution feed comes back: islanded homes rejoin and republish what they
    # can offer again. Their backup was never awarded away, so there is nothing to undo.
    resynced = 0
    if islanded and island_restore_s:
        registry.advance(max(island_restore_s - registry.now_s, 0))
        heartbeat_all(registry, silent)
        by_id = {d.device_id: d for d in devices}
        for device_id in sorted(islanded):
            registry.publish(registry.sign(card_for(by_id[device_id], hours)))
        resynced = len(islanded)
        bus.send(
            registry.now_s,
            MessageKind.RECONCILE,
            "grid-model",
            coordinator.agent_id,
            (
                f"{resynced} islanded homes resynced after the simulated distribution "
                "outage cleared and republished their capability cards"
            ),
            resynced=resynced,
            simulated=True,
        )

    # Let the scenario run out its clock so agents that stopped answering are visibly
    # stale in the final registry, the way an operator would see them.
    registry.advance(max(scenario.duration_s - registry.now_s, 0))
    heartbeat_all(registry, silent)
    statuses = registry.statuses()
    client.flush()
    root = responses[-1].answer(ROOT_CAUSE) if responses else None
    risk = responses[-1].answer(BACKUP_RISK) if responses else None
    root_cause = root.choice if root and root.choice else "unknown"
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
        jev_source=client.source.value,
        jev_model=responses[-1].model if responses else "rules-fallback",
        root_cause=root_cause,
        root_cause_truth=scenario.ground_truth_root_cause,
        root_cause_correct=root_cause == scenario.ground_truth_root_cause,
        root_cause_confidence=round(root.confidence, 4) if root else 0.0,
        backup_risk=round(risk.score, 4) if risk and risk.score is not None else 0.0,
        human_approvals=sum(1 for d in decisions if not d.auto_approved),
        auto_approvals=sum(1 for d in decisions if d.auto_approved),
        decision_latency_ms=client.median_latency_ms,
        distrusted_agents=len({a for d in decisions for a in d.distrusted}),
        min_frequency_hz=round(min_hz, 3),
        extra_demand_kw=round(extra_demand_kw, 2),
        islanded_agents=len(islanded),
        resynced_agents=resynced,
        conflicting_cards=len(conflicting),
        backup_violations=backup_violations,
        self_deployed_kw=self_deployed_kw,
        response_cycles=(
            response_cycles
            if response_cycles is not None
            else (
                None
                if dip_at_s is None or first_commit_s is None
                else int(round((first_commit_s - dip_at_s) * CYCLES_PER_SECOND))
            )
        ),
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
        decisions=decisions,
        responses=responses,
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
