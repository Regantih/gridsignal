"""CLI for the operator workflow: alarm grouping and the incident timeline.

A thin entry point over :mod:`gridsignal.control_room.workflow` so the module the
engine imports stays importable without running anything::

    python -m gridsignal.workflow --devices 10000
"""

from __future__ import annotations

import argparse

from gridsignal.control_room.engine import ControlRoomEngine
from gridsignal.control_room.models import Incident
from gridsignal.control_room.workflow import (
    GroupingReport,
    OverrideRecord,
    TimelineStep,
    group_alarms,
    timeline,
)
from gridsignal.fleet import FLEET_SIZE


def lines(
    report: GroupingReport,
    steps: list[TimelineStep],
    incident: Incident | None,
    overrides: list[OverrideRecord] | None = None,
) -> list[str]:
    out = ["Operator workflow (simulated fleet, one incident)", "", "Alarm grouping"]
    for group in report.groups:
        out.append(f"  {group.summary}")
    out += ["", f"  {report.headline}", "", "Incident timeline"]
    for step in steps:
        out.append(f"  {step.elapsed:>7}  {step.stage:<9}{step.actor:<28}{step.summary}")
    if incident is not None:
        out += [
            "",
            f"  {incident.incident_id}: ${incident.dollars_at_risk:,.0f} at risk, "
            f"${incident.dollars_recovered:,.0f} recovered after one approval by "
            f"{incident.approved_by}",
        ]
    for record in overrides or []:
        out += [
            "",
            f"Operator override: {record.device_id} {record.kw_before:.2f} kW -> "
            f"{record.kw_after:.2f} kW by {record.operator}",
            f"  reason (required, logged): {record.reason}",
        ]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--devices", type=int, default=FLEET_SIZE, help="simulated fleet size")
    parser.add_argument(
        "--stale-wave",
        action="store_true",
        help="also inject a fleet-wide stale telemetry wave, a second cause to group apart",
    )
    args = parser.parse_args(argv)

    engine = ControlRoomEngine(fleet_size=args.devices)
    engine.trigger_device_failure()
    if args.stale_wave:
        engine.inject_stale_telemetry()
    report = group_alarms(engine.alarms)
    incident = engine.approve_recovery()
    held_back = next(d for d in engine.devices if d.is_operator_controlled and d.assigned_kw > 0)
    override = engine.override_award(
        held_back.device_id,
        0.0,
        "member called in: medical device on site tonight, hold their battery back",
    )
    print("\n".join(lines(report, timeline(engine.audit, incident), incident, [override])))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
