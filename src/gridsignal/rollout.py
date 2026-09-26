"""Staged firmware rollout across the simulated fleet, as an orchestrated job.

    python -m gridsignal.rollout scenarios/rollout_bad_build.yaml

A build moves through rings — lab, 1% canary, 10%, 50%, 100% — and every ring has to
clear three health gates before the next one opens: telemetry heartbeat, charge and
discharge response, and the homeowner's backup reserve still held. A failed gate halts
the rollout and rolls that ring back. A ring never advances while a grid event is live
or while any home in it is islanded, and every promotion past 10% of the fleet needs a
human approval.

Everything here is simulated: no device is contacted, no firmware exists, and the
temperature, heartbeat and response behaviour are modelled from the scenario's seed.
"""

from __future__ import annotations

import argparse
import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from gridsignal.mesh.messages import MessageBus, MessageKind
from gridsignal.mesh.scenarios import SCENARIO_DIR, TRACE_DIR

#: Rings, in the order they open. ``lab`` is a fixed bench population rather than a
#: share of the fleet; the rest are shares of all devices.
LAB_DEVICES = 5
RING_SHARES: tuple[tuple[str, float], ...] = (
    ("lab", 0.0),
    ("canary_1pct", 0.01),
    ("ring_10pct", 0.10),
    ("ring_50pct", 0.50),
    ("ring_100pct", 1.00),
)
#: Past this share of the fleet a human has to approve the promotion.
APPROVAL_ABOVE_SHARE = 0.10
#: Simulated install throughput and the soak the fleet sits in before gates are read.
DEVICES_PER_SECOND = 400.0
SOAK_S = 60
#: How long the job waits between re-checks when a ring is blocked, and how long it
#: keeps waiting before giving up and leaving the rest of the fleet on the old build.
HOLD_STEP_S = 30
HOLD_GIVE_UP_S = 3_600
#: Default gate thresholds, as a share of the devices in the ring.
HEARTBEAT_GATE = 0.99
RESPONSE_GATE = 0.99
#: Backup energy a home has to still be holding after the update, in kWh. Assumption.
RESERVE_GATE_KWH = 4.0

GATES = ("telemetry_heartbeat", "charge_discharge_response", "backup_reserve_held")


def _unit(seed: int, device_id: str, salt: str) -> float:
    """A stable pseudo-random number in [0, 1) for one device and one property."""
    digest = hashlib.sha256(f"{seed}:{device_id}:{salt}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


@dataclass(frozen=True)
class BuildFault:
    """A defect the build carries, expressed as the population it silently breaks."""

    #: Share of the *hot* devices that stop responding to charge/discharge commands.
    silent_failure_share: float = 0.0
    #: Above this simulated ambient temperature a device is exposed to the defect.
    high_temp_c: float = 35.0
    #: Whether the defect also stops the heartbeat. A silent failure does not, which
    #: is exactly why a heartbeat-only gate would miss it.
    breaks_heartbeat: bool = False
    #: Whether the defect eats into the homeowner's reserve.
    breaks_reserve: bool = False


@dataclass(frozen=True)
class RolloutScenario:
    name: str
    description: str = ""
    build: str = "2026.09.1"
    previous_build: str = "2026.08.4"
    seed: int = 42
    devices: int = 10_000
    fault: BuildFault = field(default_factory=BuildFault)
    heartbeat_gate: float = HEARTBEAT_GATE
    response_gate: float = RESPONSE_GATE
    reserve_gate_kwh: float = RESERVE_GATE_KWH
    #: Simulated window during which a grid event is live, so no ring may advance.
    grid_event_s: tuple[int, int] | None = None
    #: Devices whose home is islanded; their ring waits for them.
    islanded: tuple[str, ...] = ()
    approver: str | None = "M. Alvarez (Fleet Operator)"
    approval_delay_s: int = 60
    source: Path | None = field(default=None, compare=False)

    @property
    def slug(self) -> str:
        return (self.source.stem if self.source else self.name.lower().replace(" ", "_")).lower()


@dataclass(frozen=True)
class GateResult:
    gate: str
    passed: bool
    observed: float
    threshold: float
    detail: str = ""


@dataclass(frozen=True)
class RingResult:
    ring: str
    devices: int
    cumulative_devices: int
    started_at_s: int
    finished_at_s: int
    gates: tuple[GateResult, ...]
    approved_by: str | None = None
    held_s: int = 0
    rolled_back: int = 0

    @property
    def passed(self) -> bool:
        return all(g.passed for g in self.gates)

    @property
    def failed_gate(self) -> str | None:
        return next((g.gate for g in self.gates if not g.passed), None)


@dataclass(frozen=True)
class RolloutMetrics:
    """What a fleet operator reads off a rollout run."""

    scenario: str
    build: str
    devices: int
    rings_completed: int
    halted: bool
    halted_ring: str | None
    failed_gate: str | None
    #: Devices that ever received this build, including the ones rolled back.
    homes_touched: int
    #: Devices the build silently broke before it was stopped.
    homes_affected: int
    rolled_back: int
    #: Simulated seconds from the first device updating to the gate that caught it.
    time_to_detect_s: int | None
    duration_s: int
    human_approvals: int
    held_s: int
    messages: int
    reserve_violations: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class RolloutResult:
    scenario: RolloutScenario
    metrics: RolloutMetrics
    rings: list[RingResult]
    bus: MessageBus

    def summary(self) -> str:
        m = self.metrics
        head = (
            f"{m.scenario}: build {m.build} across {m.devices:,} simulated devices — "
            f"{m.rings_completed} of {len(RING_SHARES)} rings"
        )
        if m.halted:
            when = "" if m.time_to_detect_s is None else f" after {m.time_to_detect_s}s"
            return (
                f"{head}, halted at {m.halted_ring} on {m.failed_gate}{when}; "
                f"{m.homes_touched:,} homes touched, "
                f"{m.homes_affected:,} affected, {m.rolled_back:,} rolled back, "
                f"{m.reserve_violations} reserve violations"
            )
        return (
            f"{head} completed in {m.duration_s}s with {m.human_approvals} human "
            f"approvals; {m.homes_touched:,} homes updated, "
            f"{m.reserve_violations} reserve violations"
        )


def load_rollout(path: str | Path) -> RolloutScenario:
    """Read one YAML rollout scenario."""
    file = Path(path)
    if not file.exists() and not file.is_absolute():
        file = SCENARIO_DIR / file.name
    raw = yaml.safe_load(file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{file} is not a rollout mapping")

    build = raw.get("build") or {}
    fault_raw = raw.get("fault") or {}
    gates = raw.get("gates") or {}
    approval = raw.get("approval") or {}
    conditions = raw.get("conditions") or {}
    window = conditions.get("grid_event_s")
    return RolloutScenario(
        name=str(raw.get("name", file.stem)),
        description=str(raw.get("description", "")),
        build=str(build.get("version", "2026.09.1")),
        previous_build=str(build.get("previous", "2026.08.4")),
        seed=int(raw.get("seed", 42)),
        devices=int(raw.get("devices", 10_000)),
        fault=BuildFault(
            silent_failure_share=float(fault_raw.get("silent_failure_share", 0.0)),
            high_temp_c=float(fault_raw.get("high_temp_c", 35.0)),
            breaks_heartbeat=bool(fault_raw.get("breaks_heartbeat", False)),
            breaks_reserve=bool(fault_raw.get("breaks_reserve", False)),
        ),
        heartbeat_gate=float(gates.get("heartbeat", HEARTBEAT_GATE)),
        response_gate=float(gates.get("response", RESPONSE_GATE)),
        reserve_gate_kwh=float(gates.get("reserve_kwh", RESERVE_GATE_KWH)),
        grid_event_s=None if window is None else (int(window[0]), int(window[1])),
        islanded=tuple(str(d) for d in (conditions.get("islanded") or [])),
        approver=None if approval.get("approver") is None else str(approval["approver"]),
        approval_delay_s=int(approval.get("delay_s", 60)),
        source=file,
    )


def available_rollouts(directory: Path = SCENARIO_DIR) -> list[Path]:
    return sorted(directory.glob("rollout_*.yaml"))


def device_ids(count: int) -> list[str]:
    return [f"BAT-{i:05d}" for i in range(1, count + 1)]


def ambient_c(scenario: RolloutScenario, device_id: str) -> float:
    """Simulated ambient temperature at the device, 18-44 C, stable for a seed."""
    return round(18.0 + 26.0 * _unit(scenario.seed, device_id, "temp"), 1)


def is_broken(scenario: RolloutScenario, device_id: str) -> bool:
    """Does the build break this device? Only hot devices are exposed to the defect."""
    fault = scenario.fault
    if fault.silent_failure_share <= 0.0:
        return False
    if ambient_c(scenario, device_id) < fault.high_temp_c:
        return False
    return _unit(scenario.seed, device_id, "fault") < fault.silent_failure_share


def ring_plan(devices: int) -> list[tuple[str, list[str]]]:
    """The devices each ring adds, cumulative shares turned into disjoint batches."""
    ids = device_ids(devices)
    plan: list[tuple[str, list[str]]] = []
    placed = 0
    for name, share in RING_SHARES:
        target = LAB_DEVICES if name == "lab" else max(int(round(share * devices)), 1)
        target = min(max(target, placed), devices)
        plan.append((name, ids[placed:target]))
        placed = target
    return plan


def _blocked(scenario: RolloutScenario, t_s: int, cohort: list[str]) -> str | None:
    """Why this ring may not advance right now, if it may not."""
    window = scenario.grid_event_s
    if window is not None and window[0] <= t_s < window[1]:
        return "grid event active"
    islanded = sorted(set(cohort) & set(scenario.islanded))
    if islanded:
        return f"{len(islanded)} home(s) islanded"
    return None


def evaluate_gates(scenario: RolloutScenario, cohort: list[str]) -> tuple[GateResult, ...]:
    """Read the three health gates over the devices that just took the build."""
    if not cohort:
        return tuple(GateResult(g, True, 1.0, 1.0, "no devices in ring") for g in GATES)

    broken = [d for d in cohort if is_broken(scenario, d)]
    fault = scenario.fault
    heartbeat = 1.0 - (len(broken) / len(cohort) if fault.breaks_heartbeat else 0.0)
    responding = 1.0 - len(broken) / len(cohort)
    below_reserve = len(broken) if fault.breaks_reserve else 0
    return (
        GateResult(
            "telemetry_heartbeat",
            heartbeat >= scenario.heartbeat_gate,
            round(heartbeat, 4),
            scenario.heartbeat_gate,
            f"{len(cohort) - (len(broken) if fault.breaks_heartbeat else 0):,} of "
            f"{len(cohort):,} devices checked in",
        ),
        GateResult(
            "charge_discharge_response",
            responding >= scenario.response_gate,
            round(responding, 4),
            scenario.response_gate,
            f"{len(broken):,} of {len(cohort):,} devices took the command and did nothing",
        ),
        GateResult(
            "backup_reserve_held",
            below_reserve == 0,
            float(below_reserve),
            0.0,
            f"{below_reserve} home(s) below the {scenario.reserve_gate_kwh:.0f} kWh reserve",
        ),
    )


def run_rollout(
    scenario: RolloutScenario,
    approver: str | None = None,
    trace: Path | None = None,
) -> RolloutResult:
    """Walk the rings, gate each one, halt and roll back on the first failure."""
    bus = MessageBus()
    who = approver if approver is not None else scenario.approver
    t_s = 0
    rings: list[RingResult] = []
    touched: list[str] = []
    approvals = 0
    held_total = 0
    detect_s: int | None = None
    halted_ring: str | None = None
    failed_gate: str | None = None
    reserve_violations = 0

    bus.send(
        t_s,
        MessageKind.REGISTER,
        "rollout-coordinator",
        "fleet",
        (
            f"Rollout of build {scenario.build} queued for {scenario.devices:,} simulated "
            f"devices, previous build {scenario.previous_build}"
        ),
        build=scenario.build,
        devices=scenario.devices,
    )

    for (name, cohort), (_, share) in zip(ring_plan(scenario.devices), RING_SHARES, strict=True):
        held = 0
        reason: str | None = None
        while (reason := _blocked(scenario, t_s, cohort)) is not None:
            bus.send(
                t_s,
                MessageKind.ESCALATION,
                "rollout-coordinator",
                "fleet-operator",
                f"{name} held: {reason} — no ring advances during it",
                ring=name,
                reason=reason,
            )
            t_s += HOLD_STEP_S
            held += HOLD_STEP_S
            if held >= HOLD_GIVE_UP_S:  # a simulated hour blocked: stop trying
                break
        held_total += held
        if reason is not None:
            halted_ring = name
            failed_gate = "blocked"
            bus.send(
                t_s,
                MessageKind.ESCALATION,
                "rollout-coordinator",
                "fleet-operator",
                (
                    f"{name} still blocked after {held}s ({reason}) — rollout stops with "
                    f"{len(touched):,} devices on {scenario.build}"
                ),
                ring=name,
                reason=reason,
            )
            break

        approved_by: str | None = None
        if share > APPROVAL_ABOVE_SHARE:
            if who is None:
                bus.send(
                    t_s,
                    MessageKind.ESCALATION,
                    "rollout-coordinator",
                    "fleet-operator",
                    (
                        f"{name} covers {share:.0%} of the fleet and has no human approval — "
                        "rollout stops here"
                    ),
                    ring=name,
                    share=share,
                )
                halted_ring = name
                failed_gate = "human_approval"
                break
            t_s += scenario.approval_delay_s
            approved_by = who
            approvals += 1
            bus.send(
                t_s,
                MessageKind.APPROVAL,
                who,
                "rollout-coordinator",
                f"{who} approved promotion to {name} ({share:.0%} of the fleet)",
                ring=name,
                share=share,
            )

        started = t_s
        t_s += int(round(len(cohort) / DEVICES_PER_SECOND)) + SOAK_S
        touched.extend(cohort)
        bus.send(
            t_s,
            MessageKind.AWARD_EXECUTED,
            "rollout-coordinator",
            name,
            f"{len(cohort):,} devices updated to {scenario.build} in {name}",
            ring=name,
            devices=len(cohort),
            cumulative=len(touched),
            build=scenario.build,
        )

        gates = evaluate_gates(scenario, cohort)
        reserve_violations += sum(
            int(g.observed) for g in gates if g.gate == "backup_reserve_held" and not g.passed
        )
        for gate in gates:
            bus.send(
                t_s,
                MessageKind.METRICS,
                name,
                "rollout-coordinator",
                f"{gate.gate}: {'pass' if gate.passed else 'FAIL'} — {gate.detail}",
                ring=name,
                gate=gate.gate,
                passed=gate.passed,
                observed=gate.observed,
                threshold=gate.threshold,
            )

        rolled_back = 0
        result_gates = gates
        if not all(g.passed for g in gates):
            failed_gate = next(g.gate for g in gates if not g.passed)
            halted_ring = name
            detect_s = t_s
            rolled_back = len(touched)
            bus.send(
                t_s,
                MessageKind.ESCALATION,
                "rollout-coordinator",
                "fleet-operator",
                (
                    f"{name} failed {failed_gate} — halting the rollout and rolling "
                    f"{rolled_back:,} devices back to {scenario.previous_build}"
                ),
                ring=name,
                gate=failed_gate,
                rolled_back=rolled_back,
            )
            rings.append(
                RingResult(
                    ring=name,
                    devices=len(cohort),
                    cumulative_devices=len(touched),
                    started_at_s=started,
                    finished_at_s=t_s,
                    gates=result_gates,
                    approved_by=approved_by,
                    held_s=held,
                    rolled_back=rolled_back,
                )
            )
            break

        rings.append(
            RingResult(
                ring=name,
                devices=len(cohort),
                cumulative_devices=len(touched),
                started_at_s=started,
                finished_at_s=t_s,
                gates=result_gates,
                approved_by=approved_by,
                held_s=held,
            )
        )

    halted = halted_ring is not None
    affected = sum(1 for d in touched if is_broken(scenario, d))
    metrics = RolloutMetrics(
        scenario=scenario.slug,
        build=scenario.build,
        devices=scenario.devices,
        rings_completed=sum(1 for r in rings if r.passed),
        halted=halted,
        halted_ring=halted_ring,
        failed_gate=failed_gate,
        homes_touched=len(touched),
        homes_affected=affected,
        rolled_back=sum(r.rolled_back for r in rings),
        time_to_detect_s=detect_s,
        duration_s=t_s,
        human_approvals=approvals,
        held_s=held_total,
        messages=len(bus),
        reserve_violations=reserve_violations,
    )
    bus.send(
        t_s,
        MessageKind.METRICS,
        "rollout-coordinator",
        "fleet-operator",
        RolloutResult(scenario, metrics, rings, bus).summary(),
        **metrics.as_dict(),
    )
    result = RolloutResult(scenario=scenario, metrics=metrics, rings=rings, bus=bus)
    if trace is not None:
        bus.write_jsonl(trace)
    return result


def trace_path(scenario: RolloutScenario, directory: Path = TRACE_DIR) -> Path:
    return directory / f"{scenario.slug}.jsonl"


def ring_table(result: RolloutResult) -> list[dict[str, object]]:
    """One row per ring, for the dashboard and the CLI."""
    rows: list[dict[str, object]] = []
    for ring in result.rings:
        failed = ring.failed_gate
        rows.append(
            {
                "ring": ring.ring,
                "devices": ring.devices,
                "cumulative": ring.cumulative_devices,
                "gate": "all three held" if failed is None else f"{failed} FAILED",
                "approval": ring.approved_by or "not required",
                "held_s": ring.held_s,
                "t_s": ring.finished_at_s,
                "rolled_back": ring.rolled_back,
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a simulated staged firmware rollout.")
    parser.add_argument("scenario", type=Path, help="path to a rollout_*.yaml scenario")
    parser.add_argument("--devices", type=int, default=None, help="override the device count")
    parser.add_argument("--no-trace", action="store_true", help="do not write the JSONL trace")
    args = parser.parse_args(argv)

    scenario = load_rollout(args.scenario)
    if args.devices is not None:
        scenario = RolloutScenario(**{**asdict(scenario), "devices": args.devices})  # type: ignore[arg-type]
    trace = None if args.no_trace else trace_path(scenario)
    result = run_rollout(scenario, trace=trace)
    print(result.summary())
    for row in ring_table(result):
        print(
            f"  {row['ring']:<12} {row['devices']:>7,} devices  "
            f"cumulative {row['cumulative']:>7,}  {row['gate']}"
        )
    if trace is not None:
        print(f"trace: {trace}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
