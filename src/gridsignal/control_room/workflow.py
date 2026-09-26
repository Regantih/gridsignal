"""One screen for the operator: what happened, in order, and what they changed.

Three things an operator needs during an incident and no dashboard usually gives
them together:

* a **timeline** of the incident from detection to recovery, with the dollars
  attached to the step that moved them;
* **alarm grouping**, so a gateway ring going dark is one incident to work rather
  than one page per device;
* an **override** path, where any award can be changed by hand but never without a
  reason, and the reason lands in the same append-only audit trail as everything else.

Everything here is simulated, and nothing is dispatched.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from gridsignal.control_room.models import AuditEvent, Incident

#: Audit kinds, in the order they happen, mapped to the stage an operator thinks in.
STAGES: dict[str, str] = {
    "detection": "detect",
    "incident_opened": "diagnose",
    "recommendation": "diagnose",
    "human_approval": "approve",
    "override": "approve",
    "quarantine": "reassign",
    "reassignment": "reassign",
    "surplus_offered": "reassign",
    "surplus_held": "reassign",
    "recovered": "recover",
}

#: Alarm kinds a naive monitor raises per device, before anything is grouped.
TELEMETRY_LOST = "telemetry_lost"
CAPACITY_DROPPED = "capacity_dropped"
#: Alarms on one ring further apart than this are two events, not one.
GROUP_WINDOW_S = 120


class OverrideError(ValueError):
    """Raised when an override is missing a reason or asks for kW that is not there."""


@dataclass(frozen=True)
class Alarm:
    """One raw alarm about one device, as a per-device monitor would raise it."""

    at: datetime
    device_id: str
    ring: int
    kind: str
    kw: float = 0.0


@dataclass(frozen=True)
class AlarmGroup:
    """Alarms that share a cause: one gateway ring, however many symptoms it threw."""

    ring: int
    alarms: int
    kinds: tuple[str, ...]
    device_ids: tuple[str, ...]
    first_at: datetime
    last_at: datetime
    kw: float

    @property
    def summary(self) -> str:
        symptoms = ", ".join(k.replace("_", " ") for k in self.kinds)
        return (
            f"gateway ring {self.ring}: {self.alarms:,} alarms from "
            f"{len(self.device_ids):,} devices ({symptoms}), {self.kw:,.0f} kW"
        )


@dataclass(frozen=True)
class GroupingReport:
    """How much of the operator's reading is saved by grouping alarms into incidents."""

    alarms: int
    groups: tuple[AlarmGroup, ...]

    @property
    def incidents(self) -> int:
        return len(self.groups)

    @property
    def before(self) -> float:
        """Alarms per incident with no grouping: every alarm is its own thing to read."""
        return 1.0

    @property
    def after(self) -> float:
        """Alarms per incident once alarms sharing a cause are grouped."""
        if not self.groups:
            return 0.0
        return round(self.alarms / self.incidents, 1)

    @property
    def headline(self) -> str:
        plural = "incident" if self.incidents == 1 else "incidents"
        return (
            f"{self.alarms:,} raw alarms grouped into {self.incidents} {plural}: "
            f"{self.after:,.1f} alarms per incident, against {self.before:.1f} with no "
            f"grouping, so the operator opens {self.incidents} {plural} instead of "
            f"reading {self.alarms:,} pages"
        )


def group_alarms(alarms: list[Alarm]) -> GroupingReport:
    """Group alarms by what caused them: one gateway ring is one incident to work.

    A ring going dark throws a telemetry alarm and a lost-capacity alarm from every
    device on it. Those arrive together and share a cause, so they are one group — the
    operator should be reading a ring, not a page per battery per symptom. Alarms on
    the same ring more than :data:`GROUP_WINDOW_S` apart are a second event and stay
    separate, so grouping never hides a later failure inside an earlier one.
    """
    buckets: list[list[Alarm]] = []
    for alarm in sorted(alarms, key=lambda a: (a.ring, a.at)):
        last = buckets[-1][-1] if buckets else None
        near = (
            last is not None
            and last.ring == alarm.ring
            and (alarm.at - last.at).total_seconds() <= GROUP_WINDOW_S
        )
        if near:
            buckets[-1].append(alarm)
        else:
            buckets.append([alarm])
    groups = tuple(
        AlarmGroup(
            ring=bucket[0].ring,
            alarms=len(bucket),
            kinds=tuple(sorted({a.kind for a in bucket})),
            device_ids=tuple(sorted({a.device_id for a in bucket})),
            first_at=min(a.at for a in bucket),
            last_at=max(a.at for a in bucket),
            kw=round(sum(a.kw for a in bucket), 2),
        )
        for bucket in buckets
    )
    return GroupingReport(alarms=len(alarms), groups=groups)


@dataclass(frozen=True)
class TimelineStep:
    """One audit event placed on the incident's clock, in the operator's language."""

    at: datetime
    stage: str
    actor: str
    summary: str
    detail: str
    seconds_from_open: int

    @property
    def elapsed(self) -> str:
        return f"+{self.seconds_from_open}s"


@dataclass(frozen=True)
class OverrideRecord:
    """A hand-made change to one battery's award, with the reason it was made."""

    at: datetime
    operator: str
    device_id: str
    kw_before: float
    kw_after: float
    reason: str

    @property
    def delta_kw(self) -> float:
        return round(self.kw_after - self.kw_before, 2)


def timeline(audit: list[AuditEvent], incident: Incident | None = None) -> list[TimelineStep]:
    """The incident as a sequence of steps, timed from the moment it was detected.

    Only events the operator acts on are kept: the baseline log and anything with no
    stage of its own stays in the full audit trail.
    """
    events = [e for e in audit if e.kind in STAGES]
    if incident is not None:
        events = [e for e in events if e.at >= incident.opened_at or e.kind == "detection"]
    if not events:
        return []
    start = events[0].at
    return [
        TimelineStep(
            at=event.at,
            stage=STAGES[event.kind],
            actor=event.actor,
            summary=event.summary,
            detail=event.detail,
            seconds_from_open=int((event.at - start).total_seconds()),
        )
        for event in events
    ]
