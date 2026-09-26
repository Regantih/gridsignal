"""Chaos scenarios declared in YAML, NANDA-Town style.

A scenario is a seed, an agent population, a list of failure injections and a duration.
Nothing in it is fetched at run time, so `python -m gridsignal.simulate <file>` replays
identically on any machine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import yaml

SCENARIO_DIR = Path(__file__).resolve().parents[3] / "scenarios"
TRACE_DIR = Path(__file__).resolve().parents[3] / "data" / "traces"


class Injection(StrEnum):
    DEVICE_FAILURE = "device_failure"
    GATEWAY_OUTAGE = "gateway_outage"
    ZONE_OUTAGE = "zone_outage"
    STALE_TELEMETRY = "stale_telemetry"
    LYING_AGENT = "lying_agent"
    SILENT_AFTER_AWARD = "silent_after_award"


@dataclass(frozen=True)
class Failure:
    kind: Injection
    at_s: int = 45
    devices: tuple[str, ...] = ()
    zone: str | None = None
    claim_kw: float = 0.0


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str = ""
    seed: int = 42
    batteries: int = 48
    price_scenario: str = "scarcity"
    stale_after_s: int = 120
    duration_s: int = 600
    approver: str = "M. Alvarez (Fleet Operator)"
    approval_delay_s: int = 60
    llm_coordinator: bool = False
    failures: tuple[Failure, ...] = ()
    source: Path | None = field(default=None, compare=False)
    declared_root_cause: str | None = None

    @property
    def ground_truth_root_cause(self) -> str:
        """What actually caused the capacity loss, for scoring the decision layer.

        A group outage outranks the agents inside it: in a scenario that drops a whole
        zone *and* plants a lying agent, the gateway is why the kW went missing.
        """
        if self.declared_root_cause:
            return self.declared_root_cause
        kinds = {f.kind for f in self.failures}
        if kinds & {Injection.ZONE_OUTAGE, Injection.GATEWAY_OUTAGE}:
            return "gateway_outage"
        if Injection.DEVICE_FAILURE in kinds:
            return "device_fault"
        if Injection.LYING_AGENT in kinds:
            return "spoofed_agent"
        if Injection.STALE_TELEMETRY in kinds:
            return "telemetry_lag"
        return "grid_event"

    @property
    def slug(self) -> str:
        return (self.source.stem if self.source else self.name.lower().replace(" ", "_")).lower()

    def of_kind(self, kind: Injection) -> list[Failure]:
        return [f for f in self.failures if f.kind is kind]


def _failure(raw: dict[str, object]) -> Failure:
    kind = Injection(str(raw["kind"]))
    devices = raw.get("devices") or ([raw["device"]] if "device" in raw else [])
    if not isinstance(devices, list):
        raise ValueError(f"{kind}: devices must be a list")
    zone = raw.get("zone")
    return Failure(
        kind=kind,
        at_s=int(raw.get("at_s", 45)),  # type: ignore[arg-type]
        devices=tuple(str(d) for d in devices),
        zone=None if zone is None else str(zone),
        claim_kw=float(raw.get("claim_kw", 0.0)),  # type: ignore[arg-type]
    )


def load_scenario(path: str | Path) -> Scenario:
    """Read one YAML chaos scenario."""
    file = Path(path)
    if not file.exists() and not file.is_absolute():
        file = SCENARIO_DIR / file.name
    raw = yaml.safe_load(file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{file} is not a scenario mapping")

    agents = raw.get("agents") or {}
    event = raw.get("event") or {}
    approval = raw.get("approval") or {}
    coordinator = raw.get("coordinator") or {}
    failures = raw.get("injections") or []
    return Scenario(
        name=str(raw.get("name", file.stem)),
        description=str(raw.get("description", "")),
        seed=int(raw.get("seed", 42)),
        batteries=int(agents.get("batteries", 48)),
        price_scenario=str(event.get("scenario", "scarcity")),
        stale_after_s=int(raw.get("stale_after_s", 120)),
        duration_s=int(raw.get("duration_s", 600)),
        approver=str(approval.get("approver", "M. Alvarez (Fleet Operator)")),
        approval_delay_s=int(approval.get("delay_s", 60)),
        llm_coordinator=bool(coordinator.get("llm", False)),
        failures=tuple(_failure(item) for item in failures),
        source=file,
        declared_root_cause=(
            None
            if raw.get("ground_truth") is None
            else str(dict(raw["ground_truth"])["root_cause"])  # type: ignore[arg-type]
        ),
    )


def available_scenarios(directory: Path = SCENARIO_DIR) -> list[Path]:
    return sorted(directory.glob("*.yaml"))
