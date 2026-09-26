"""Telemetry ingestion: a documented JSON-lines format and a validating importer.

The rest of this repository invents its fleet. This module is the one door a real
export can come through: one JSON object per line, one row per device per report,
validated, then applied to the same :class:`~gridsignal.control_room.engine.ControlRoomEngine`
state the Control Room renders. Nothing here contacts a device or a network.

Format (``.jsonl``, UTF-8, one object per line, unknown keys ignored):

===============  ========  ==================================================
field            type      meaning
===============  ========  ==================================================
``device_id``    string    the battery, e.g. ``BAT-042``
``ts``           string    ISO-8601 with an offset, e.g. ``2023-09-06T17:00:00Z``
``soc_kwh``      number    energy in the pack right now, 0 to nameplate
``power_kw``     number    measured output, positive discharging, negative charging
``status``       string    ``online``, ``degraded``, ``offline`` or ``unavailable``
``firmware``     string    build on the device, e.g. ``2.4.1``
``gateway``      string    gateway the device reports through, e.g. ``GW-07``
===============  ========  ==================================================

Every row that cannot be trusted is rejected with a reason and counted; the import
never half-applies. Rows are stale when they are older than ``max_age_s`` relative to
the newest row in the file, which is what a batch export from yesterday looks like.

    python -m gridsignal.telemetry data/telemetry/synthetic_sample.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from gridsignal import paths
from gridsignal.control_room.engine import ControlRoomEngine
from gridsignal.control_room.models import DeviceStatus, Reading, Rejection

#: Bundled, clearly synthetic examples in the documented format.
SAMPLE_DIR = paths.ROOT / "data" / "telemetry"
SAMPLE_FILE = SAMPLE_DIR / "synthetic_sample.jsonl"
BAD_ROWS_FILE = SAMPLE_DIR / "synthetic_bad_rows.jsonl"

REQUIRED_FIELDS: tuple[str, ...] = (
    "device_id",
    "ts",
    "soc_kwh",
    "power_kw",
    "status",
    "firmware",
    "gateway",
)
#: A row older than this, measured against the newest row in the same file, is not
#: current enough to dispatch on. Fifteen minutes is one ERCOT settlement interval.
DEFAULT_MAX_AGE_S = 900


class TelemetryError(ValueError):
    """Raised when a file cannot be read as telemetry at all."""


@dataclass(frozen=True)
class ImportResult:
    """What one file did: readings applied, rows refused, devices touched."""

    applied: tuple[Reading, ...]
    rejected: tuple[Rejection, ...]
    superseded: int
    as_of: datetime | None
    source: str

    @property
    def rows(self) -> int:
        return len(self.applied) + len(self.rejected) + self.superseded

    @property
    def devices(self) -> int:
        return len({r.device_id for r in self.applied})

    @property
    def reasons(self) -> dict[str, int]:
        """Rejection reasons, most common first, with the numbers stripped out."""
        counts: dict[str, int] = {}
        for rejection in self.rejected:
            key = rejection.reason.split(":")[0]
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


@dataclass
class _Parsed:
    readings: list[Reading] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)


def _parse_timestamp(raw: str) -> datetime:
    text = raw.strip().replace("Z", "+00:00")
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise ValueError("timestamp has no UTC offset")
    return moment


def _read_row(line_no: int, line: str) -> Reading | Rejection:
    """Validate one line's shape, types and ranges. Freshness is checked per file."""
    try:
        row = json.loads(line)
    except json.JSONDecodeError as exc:
        return Rejection(line_no, f"malformed: not valid JSON ({exc.msg})")
    if not isinstance(row, dict):
        return Rejection(line_no, "malformed: row is not a JSON object")

    device_id = row.get("device_id") if isinstance(row.get("device_id"), str) else ""
    missing = [f for f in REQUIRED_FIELDS if f not in row]
    if missing:
        return Rejection(line_no, f"malformed: missing {', '.join(missing)}", device_id)
    if not device_id:
        return Rejection(line_no, "malformed: device_id is not a non-empty string")

    try:
        ts = _parse_timestamp(str(row["ts"]))
    except ValueError as exc:
        return Rejection(line_no, f"malformed: bad ts ({exc})", device_id)

    numbers: dict[str, float] = {}
    for key in ("soc_kwh", "power_kw"):
        value = row[key]
        if isinstance(value, bool) or not isinstance(value, int | float):
            return Rejection(line_no, f"malformed: {key} is not a number", device_id)
        if not math.isfinite(float(value)):
            return Rejection(line_no, f"malformed: {key} is not finite", device_id)
        numbers[key] = float(value)
    if numbers["soc_kwh"] < 0:
        return Rejection(line_no, "malformed: soc_kwh is negative", device_id)

    try:
        status = DeviceStatus(str(row["status"]).strip().lower())
    except ValueError:
        known = ", ".join(s.value for s in DeviceStatus)
        return Rejection(line_no, f"malformed: status {row['status']!r} is not one of {known}")

    for key in ("firmware", "gateway"):
        if not isinstance(row[key], str) or not row[key].strip():
            return Rejection(line_no, f"malformed: {key} is not a non-empty string", device_id)

    return Reading(
        line_no=line_no,
        device_id=device_id,
        ts=ts,
        soc_kwh=numbers["soc_kwh"],
        power_kw=numbers["power_kw"],
        status=status,
        firmware=str(row["firmware"]).strip(),
        gateway=str(row["gateway"]).strip(),
    )


def read_lines(lines: Iterable[str]) -> _Parsed:
    parsed = _Parsed()
    for line_no, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        outcome = _read_row(line_no, line)
        if isinstance(outcome, Rejection):
            parsed.rejected.append(outcome)
        else:
            parsed.readings.append(outcome)
    return parsed


def _latest_per_device(readings: list[Reading]) -> tuple[list[Reading], int]:
    """Keep the newest row per device; older rows for the same device are superseded."""
    newest: dict[str, Reading] = {}
    superseded = 0
    for reading in readings:
        seen = newest.get(reading.device_id)
        if seen is None:
            newest[reading.device_id] = reading
            continue
        superseded += 1
        if reading.ts >= seen.ts:
            newest[reading.device_id] = reading
    return sorted(newest.values(), key=lambda r: r.device_id), superseded


def apply_file(
    engine: ControlRoomEngine,
    path: Path | str,
    max_age_s: int = DEFAULT_MAX_AGE_S,
) -> ImportResult:
    """Read a JSONL file and apply it to ``engine``'s fleet."""
    file = Path(path)
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        raise TelemetryError(f"cannot read {file}: {exc}") from exc
    return apply_text(engine, text, source=file.name, max_age_s=max_age_s)


def apply_text(
    engine: ControlRoomEngine,
    text: str,
    source: str = "telemetry",
    max_age_s: int = DEFAULT_MAX_AGE_S,
) -> ImportResult:
    """Validate ``text`` as JSONL telemetry and apply what survives to ``engine``."""
    parsed = read_lines(text.splitlines())
    rejected = list(parsed.rejected)
    fresh, superseded = _latest_per_device(parsed.readings)

    as_of = max((r.ts for r in fresh), default=None)
    kept: list[Reading] = []
    for reading in fresh:
        if as_of is not None and reading.ts < as_of - timedelta(seconds=max_age_s):
            age = int((as_of - reading.ts).total_seconds())
            rejected.append(
                Rejection(
                    reading.line_no,
                    f"stale: {age:,} s older than the newest row (limit {max_age_s:,} s)",
                    reading.device_id,
                )
            )
            continue
        kept.append(reading)

    applied, more = engine.ingest_telemetry(kept, source=source, as_of=as_of)
    rejected.extend(more)
    rejected.sort(key=lambda r: r.line_no)
    return ImportResult(
        applied=tuple(applied),
        rejected=tuple(rejected),
        superseded=superseded,
        as_of=as_of,
        source=source,
    )


def report(result: ImportResult) -> Iterator[str]:
    yield f"Telemetry import: {result.source}"
    yield (
        f"  {result.rows:,} rows -> {len(result.applied):,} applied across "
        f"{result.devices:,} devices, {len(result.rejected):,} rejected, "
        f"{result.superseded:,} superseded by a newer row"
    )
    if result.as_of is not None:
        yield f"  newest row: {result.as_of.isoformat()}"
    for reason, count in result.reasons.items():
        yield f"  rejected, {reason}: {count:,}"
    for rejection in result.rejected[:20]:
        yield f"    {rejection}"
    if len(result.rejected) > 20:
        yield f"    ... {len(result.rejected) - 20:,} more"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a JSON-lines telemetry file and apply it to the fleet."
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=str(SAMPLE_FILE),
        help="JSONL telemetry file (default: the bundled synthetic sample)",
    )
    parser.add_argument("--devices", type=int, default=48, help="simulated fleet size")
    parser.add_argument(
        "--max-age-s",
        type=int,
        default=DEFAULT_MAX_AGE_S,
        help="how far behind the newest row a row may be before it is stale",
    )
    args = parser.parse_args(argv)

    engine = ControlRoomEngine(fleet_size=args.devices)
    # The snapshot reads the live device list, so the "before" numbers have to be
    # copied out now, not held as an object that the import will move underneath us.
    start = engine.snapshot()
    before = (
        start.online,
        start.degraded,
        start.offline,
        start.unavailable,
        start.available_capacity_kwh,
        start.committed_kw,
    )
    result = apply_file(engine, args.path, max_age_s=args.max_age_s)
    after = engine.snapshot()

    for line in report(result):
        print(line)
    print(
        f"  fleet after import: {after.online:,} online, {after.degraded:,} degraded, "
        f"{after.offline:,} offline, {after.unavailable:,} unavailable "
        f"(was {before[0]:,}/{before[1]:,}/{before[2]:,}/{before[3]:,})"
    )
    print(
        f"  available energy {before[4]:,.1f} kWh -> "
        f"{after.available_capacity_kwh:,.1f} kWh, committed "
        f"{before[5]:,.1f} kW -> {after.committed_kw:,.1f} kW"
    )
    print("  Simulated fleet, synthetic file: no device or utility system was contacted.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
