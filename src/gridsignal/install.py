"""Install wave: new batteries joining the mesh while a grid event is already live.

    python -m gridsignal.install scenarios/install_wave.yaml

Each new unit is commissioned by a simulated installer phone check that registers its
signed capability card. A unit that arrives without a valid signature is rejected, and
every accepted unit starts on **probation**: it publishes zero biddable kW until it has
passed the same three health gates the firmware rollout uses (heartbeat, charge and
discharge response, homeowner reserve held). Only then can it be awarded capacity.

The point of the drill is what does *not* happen: no award ever lands on an unverified
or probationary unit, and the awards already executed before the wave started do not
move while several hundred agents join. Everything is simulated.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from gridsignal.control_room.engine import TARGET_KW_PER_DEVICE
from gridsignal.fleet import build_fleet
from gridsignal.mesh.build import card_for, register_fleet
from gridsignal.mesh.cards import AgentCard, AgentKind, CardStatus, Health, derived_signing_key
from gridsignal.mesh.messages import MessageBus, MessageKind
from gridsignal.mesh.negotiation import Coordinator
from gridsignal.mesh.registry import AgentRegistry
from gridsignal.mesh.scenarios import SCENARIO_DIR, TRACE_DIR

#: Event window the mesh is covering while the wave arrives, in hours. Assumption.
EVENT_HOURS = 2.0
#: A newly installed unit's simulated nameplate, before its own card is trusted.
NEW_UNIT_KW = 5.0
NEW_UNIT_KWH = 13.5
#: Backup energy a new unit must still be holding to clear the probation gate, in kWh.
PROBATION_RESERVE_KWH = 4.0
#: How long the commissioning health check takes, in simulated seconds.
DEFAULT_HEALTH_CHECK_S = 120
#: Simulated installer throughput, units commissioned per hour across all crews.
DEFAULT_UNITS_PER_HOUR = 100.0


@dataclass(frozen=True)
class InstallScenario:
    name: str
    description: str = ""
    seed: int = 42
    existing: int = 1_000
    joining: int = 400
    units_per_hour: float = DEFAULT_UNITS_PER_HOUR
    health_check_s: int = DEFAULT_HEALTH_CHECK_S
    #: Every nth arriving unit fails the installer's signature check, so its card is
    #: rejected at the door. 0 means every unit is properly commissioned.
    bad_signature_every: int = 0
    #: Every nth accepted unit fails its probation health check and stays ineligible.
    failing_health_every: int = 0
    approver: str = "M. Alvarez (Fleet Operator)"
    source: Path | None = field(default=None, compare=False)

    @property
    def slug(self) -> str:
        return (self.source.stem if self.source else self.name.lower().replace(" ", "_")).lower()


@dataclass(frozen=True)
class InstallMetrics:
    scenario: str
    existing_agents: int
    arrived: int
    registered: int
    rejected_cards: int
    promoted: int
    failed_health: int
    #: Simulated seconds from the first installer check to the last promotion.
    wave_duration_s: int
    joins_per_hour: float
    #: Seconds from the first arrival to the first award a newly installed unit could
    #: legitimately win. None when no new unit was awarded.
    time_to_first_eligible_award_s: int | None
    awards_to_unverified: int
    awards_to_probation: int
    #: kW an already-committed agent lost while the wave joined. Must be 0: new agents
    #: arriving may add capacity, never take an existing commitment away.
    existing_kw_lost: float
    new_unit_awards: int
    new_unit_kw: float
    messages: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class InstallResult:
    scenario: InstallScenario
    metrics: InstallMetrics
    bus: MessageBus
    registry: AgentRegistry
    coordinator: Coordinator

    def summary(self) -> str:
        m = self.metrics
        first = (
            "no new unit awarded"
            if m.time_to_first_eligible_award_s is None
            else f"first eligible award at {m.time_to_first_eligible_award_s}s"
        )
        return (
            f"{m.scenario}: {m.arrived:,} units arrived during a live event — "
            f"{m.registered:,} registered, {m.rejected_cards:,} rejected at the door, "
            f"{m.promoted:,} cleared probation ({m.joins_per_hour:,.0f} joins/h simulated), "
            f"{first}; {m.new_unit_awards:,} new units awarded {m.new_unit_kw:,.1f} kW, "
            f"{m.awards_to_unverified} awards to unverified units, "
            f"{m.awards_to_probation} to probationary units, "
            f"{m.existing_kw_lost:.2f} kW of existing commitment lost"
        )


def load_install(path: str | Path) -> InstallScenario:
    file = Path(path)
    if not file.exists() and not file.is_absolute():
        file = SCENARIO_DIR / file.name
    raw = yaml.safe_load(file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{file} is not an install-wave mapping")
    fleet = raw.get("fleet") or {}
    install = raw.get("install") or {}
    approval = raw.get("approval") or {}
    return InstallScenario(
        name=str(raw.get("name", file.stem)),
        description=str(raw.get("description", "")),
        seed=int(raw.get("seed", 42)),
        existing=int(fleet.get("existing", 1_000)),
        joining=int(fleet.get("joining", 400)),
        units_per_hour=float(install.get("units_per_hour", DEFAULT_UNITS_PER_HOUR)),
        health_check_s=int(install.get("health_check_s", DEFAULT_HEALTH_CHECK_S)),
        bad_signature_every=int(install.get("bad_signature_every", 0)),
        failing_health_every=int(install.get("failing_health_every", 0)),
        approver=str(approval.get("approver", "M. Alvarez (Fleet Operator)")),
        source=file,
    )


def new_unit_card(unit_id: str, zone: str, biddable: bool) -> AgentCard:
    """A newly installed unit's card. On probation it advertises no biddable kW."""
    return AgentCard(
        agent_id=unit_id,
        kind=AgentKind.BATTERY,
        zone=zone,
        capabilities={
            "kw_available": NEW_UNIT_KW if biddable else 0.0,
            "kwh_available": NEW_UNIT_KWH if biddable else 0.0,
            "soc": 0.9,
            "probation": 0.0 if biddable else 1.0,
        },
        health=Health.HEALTHY,
    )


def run_install_wave(scenario: InstallScenario, trace: Path | None = None) -> InstallResult:
    """Run a live grid event, then let several hundred new units join it."""
    bus = MessageBus()
    registry = AgentRegistry(key=derived_signing_key(scenario.seed))
    devices = build_fleet(seed=scenario.seed, size=scenario.existing)
    register_fleet(registry, devices, EVENT_HOURS, bus)
    coordinator = Coordinator(registry, bus)

    # The event is already running and already covered when the installers arrive.
    gap_kw = round(TARGET_KW_PER_DEVICE * scenario.existing * 0.2, 2)
    call = coordinator.call_for_capacity(gap_kw, EVENT_HOURS)
    first_awards = coordinator.propose(call, coordinator.collect_bids(call, log_each=False))
    coordinator.approve(call.call_id, scenario.approver)
    before = dict(coordinator.commitments)

    # An awarded agent republishes what it has *left*, so the same kW is never bid twice.
    by_id = {d.device_id: d for d in devices}
    for award in first_awards.awards:
        device = by_id[award.agent_id]
        device.assigned_kw = round(device.assigned_kw + award.kw, 3)
        registry.publish(registry.sign(card_for(device, EVENT_HOURS)))

    zones = sorted({d.zone for d in devices})
    seconds_per_unit = 3_600.0 / max(scenario.units_per_hour, 1e-6)
    start_s = registry.now_s
    registered: list[str] = []
    rejected: list[str] = []
    probation: list[tuple[int, str]] = []  # (ready_at_s, unit_id)
    failed_health: list[str] = []

    for i in range(1, scenario.joining + 1):
        arrival = start_s + int(round(i * seconds_per_unit))
        registry.now_s = arrival
        unit_id = f"NEW-{i:04d}"
        zone = zones[i % len(zones)]
        card = new_unit_card(unit_id, zone, biddable=False)
        forged = scenario.bad_signature_every and i % scenario.bad_signature_every == 0
        # A forged card is what an uncommissioned unit looks like: it never went
        # through the installer's phone check, so it carries no valid signature.
        status = registry.register(card if forged else registry.sign(card))
        if status is CardStatus.REJECTED:
            rejected.append(unit_id)
            bus.send(
                arrival,
                MessageKind.REJECT,
                unit_id,
                "registry",
                f"{unit_id} failed the installer check — card signature does not verify",
                unit=unit_id,
            )
            continue
        registered.append(unit_id)
        probation.append((arrival + scenario.health_check_s, unit_id))

    bus.send(
        registry.now_s,
        MessageKind.REGISTER,
        "installer-app",
        "mesh",
        (
            f"{len(registered):,} units commissioned and placed on probation, "
            f"{len(rejected):,} rejected at the door"
        ),
        registered=len(registered),
        rejected=len(rejected),
    )

    promoted: list[str] = []
    for index, (ready_at, unit_id) in enumerate(probation, start=1):
        registry.now_s = ready_at
        if scenario.failing_health_every and index % scenario.failing_health_every == 0:
            failed_health.append(unit_id)
            bus.send(
                ready_at,
                MessageKind.ESCALATION,
                unit_id,
                "registry",
                f"{unit_id} failed its probation health check — stays ineligible for awards",
                unit=unit_id,
            )
            continue
        card = registry.card(unit_id)
        registry.publish(
            registry.sign(new_unit_card(unit_id, card.zone, biddable=True)),
        )
        promoted.append(unit_id)

    wave_end_s = registry.now_s
    bus.send(
        wave_end_s,
        MessageKind.METRICS,
        "registry",
        "mesh",
        (
            f"{len(promoted):,} units cleared the probation gate "
            f"(heartbeat, charge/discharge response, reserve held)"
        ),
        promoted=len(promoted),
        failed_health=len(failed_health),
    )

    # Keep every existing agent alive so the second auction is about the new units.
    for agent_id in registry.agent_ids():
        registry.heartbeat(agent_id)

    # A gateway outage takes the incumbent batteries in one zone dark, so the only
    # capacity left in that zone is the units that just finished commissioning.
    dark_zone = zones[0]
    for device in devices:
        if device.zone == dark_zone:
            registry.set_health(device.device_id, Health.OFFLINE)
    bus.send(
        wave_end_s,
        MessageKind.HEARTBEAT_LOST,
        f"ZONE-{dark_zone}",
        "coordinator-01",
        f"Simulated gateway outage takes the incumbent batteries in {dark_zone} dark",
        zone=dark_zone,
    )

    # A second shortfall in that zone, now that the wave has landed.
    follow_up = coordinator.call_for_capacity(gap_kw * 0.05, EVENT_HOURS, zone=dark_zone)
    bids = coordinator.collect_bids(follow_up, log_each=False)
    award_set = coordinator.propose(follow_up, bids)
    coordinator.approve(follow_up.call_id, scenario.approver)

    awarded_new = [a for a in award_set.awards if a.agent_id.startswith("NEW-")]
    to_unverified = sum(1 for a in awarded_new if a.agent_id in set(rejected))
    to_probation = sum(
        1 for a in awarded_new if registry.card(a.agent_id).capability("probation") > 0.0
    )
    lost = round(
        sum(max(kw - coordinator.commitments.get(a, 0.0), 0.0) for a, kw in before.items()), 3
    )
    first_award_s: int | None = None
    if awarded_new:
        first_award_s = min(
            ready for ready, unit in probation if unit in {a.agent_id for a in awarded_new}
        )
        first_award_s -= start_s

    metrics = InstallMetrics(
        scenario=scenario.slug,
        existing_agents=len(devices),
        arrived=scenario.joining,
        registered=len(registered),
        rejected_cards=len(rejected),
        promoted=len(promoted),
        failed_health=len(failed_health),
        wave_duration_s=wave_end_s - start_s,
        joins_per_hour=round(
            3_600.0 * len(promoted) / max(wave_end_s - start_s, 1),
            1,
        ),
        time_to_first_eligible_award_s=first_award_s,
        awards_to_unverified=to_unverified,
        awards_to_probation=to_probation,
        existing_kw_lost=lost,
        new_unit_awards=len(awarded_new),
        new_unit_kw=round(sum(a.kw for a in awarded_new), 2),
        messages=len(bus),
    )
    result = InstallResult(
        scenario=scenario,
        metrics=metrics,
        bus=bus,
        registry=registry,
        coordinator=coordinator,
    )
    bus.send(wave_end_s, MessageKind.METRICS, "mesh", "fleet-operator", result.summary())
    if trace is not None:
        bus.write_jsonl(trace)
    return result


def trace_path(scenario: InstallScenario, directory: Path = TRACE_DIR) -> Path:
    return directory / f"{scenario.slug}.jsonl"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a simulated install wave.")
    parser.add_argument("scenario", type=Path, help="path to an install wave YAML scenario")
    parser.add_argument("--no-trace", action="store_true", help="do not write the JSONL trace")
    args = parser.parse_args(argv)

    scenario = load_install(args.scenario)
    trace = None if args.no_trace else trace_path(scenario)
    result = run_install_wave(scenario, trace=trace)
    print(result.summary())
    if trace is not None:
        print(f"trace: {trace}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
