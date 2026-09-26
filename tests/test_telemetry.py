"""The one door real battery data comes through: format, validation, fleet state."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, timedelta
from types import ModuleType

import pytest

from gridsignal import paths, telemetry
from gridsignal.control_room import ControlRoomEngine
from gridsignal.control_room.models import DeviceStatus

#: The clock the fleet judges telemetry against: its own grid event, not the file's.
AS_OF = ControlRoomEngine(fleet_size=1).event_clock.replace(tzinfo=UTC)


def load_script(name: str) -> ModuleType:
    """Import a file from ``scripts/``, which is not an installed package."""
    spec = importlib.util.spec_from_file_location(name, paths.ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def row(**overrides: object) -> str:
    base: dict[str, object] = {
        "device_id": "BAT-001",
        "ts": AS_OF.isoformat().replace("+00:00", "Z"),
        "soc_kwh": 12.0,
        "power_kw": 3.0,
        "status": "online",
        "firmware": "2.4.1",
        "gateway": "GW-01",
    }
    base.update(overrides)
    return json.dumps(base)


@pytest.fixture
def engine() -> ControlRoomEngine:
    return ControlRoomEngine(fleet_size=48)


def test_a_valid_row_lands_on_the_device_the_control_room_reads(
    engine: ControlRoomEngine,
) -> None:
    """The importer writes to fleet state, not to a copy beside it."""
    result = telemetry.apply_text(engine, row(soc_kwh=9.5, status="degraded"))

    assert not result.rejected
    assert len(result.applied) == 1
    device = engine.device("BAT-001")
    assert device.status is DeviceStatus.DEGRADED
    assert device.firmware == "2.4.1"
    assert device.gateway == "GW-01"
    assert device.measured_power_kw == 3.0
    assert device.available_kwh == pytest.approx(9.5, abs=0.02)
    assert engine.snapshot().devices[0] is device


def test_an_offline_row_drops_the_device_out_of_the_commitment(
    engine: ControlRoomEngine,
) -> None:
    before = engine.device("BAT-001").assigned_kw
    assert before > 0

    telemetry.apply_text(engine, row(status="offline", soc_kwh=0.0, power_kw=0.0))

    assert engine.device("BAT-001").assigned_kw == 0.0
    assert engine.snapshot().offline == 1


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("{not json", "not valid JSON"),
        ("[1, 2, 3]", "not a JSON object"),
        (json.dumps({"device_id": "BAT-001", "ts": "2026-09-22T20:45:00Z"}), "missing"),
        (row(ts="yesterday"), "bad ts"),
        (row(ts="2026-09-22T20:45:00"), "no UTC offset"),
        (row(status="sleeping"), "is not one of"),
        (row(power_kw="4.2"), "power_kw is not a number"),
        (row(soc_kwh=float("nan")).replace("NaN", "null"), "soc_kwh is not a number"),
        (row(soc_kwh=-1.0), "soc_kwh is negative"),
        (row(firmware=" "), "firmware is not a non-empty string"),
        (row(gateway=""), "gateway is not a non-empty string"),
        (row(device_id=""), "device_id is not a non-empty string"),
        (row(device_id="BAT-9999"), "unknown device"),
        (row(soc_kwh=999.0), "above the"),
        (row(power_kw=500.0), "above the"),
    ],
)
def test_every_malformed_row_is_rejected_with_a_reason_an_operator_can_act_on(
    engine: ControlRoomEngine, line: str, reason: str
) -> None:
    result = telemetry.apply_text(engine, line)

    assert not result.applied
    assert len(result.rejected) == 1
    assert reason in result.rejected[0].reason
    assert str(result.rejected[0]).startswith("line 1")


def test_a_nan_reading_is_refused(engine: ControlRoomEngine) -> None:
    """Python's json reads bare NaN happily; a state of charge of NaN is not a reading."""
    result = telemetry.apply_text(engine, row(soc_kwh=float("nan")))

    assert not result.applied
    assert "soc_kwh is not finite" in result.rejected[0].reason


def test_a_row_older_than_the_window_is_stale_and_says_how_old(
    engine: ControlRoomEngine,
) -> None:
    """Freshness is measured against the event clock the fleet is dispatching into."""
    old = (AS_OF - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    text = "\n".join([row(), row(device_id="BAT-002", ts=old)])

    result = telemetry.apply_text(engine, text)

    assert [r.device_id for r in result.applied] == ["BAT-001"]
    assert len(result.rejected) == 1
    assert "stale: 7,200 s behind the event clock (limit 900 s)" in result.rejected[0].reason
    assert engine.device("BAT-002").firmware == ""


def test_one_row_from_2099_is_refused_and_does_not_age_out_the_honest_rows(
    engine: ControlRoomEngine,
) -> None:
    """A file that sets its own reference lets one hostile row reject the whole export."""
    hostile = AS_OF.replace(year=2099).isoformat().replace("+00:00", "Z")
    text = "\n".join(
        [row(), row(device_id="BAT-002"), row(device_id="BAT-003", ts=hostile)],
    )

    result = telemetry.apply_text(engine, text)

    assert [r.device_id for r in result.applied] == ["BAT-001", "BAT-002"]
    assert len(result.rejected) == 1
    assert "ahead of the event clock" in result.rejected[0].reason
    assert result.rejected[0].device_id == "BAT-003"
    assert result.as_of == AS_OF  # the clock is the fleet's, whatever the file says


def test_a_little_clock_skew_ahead_of_the_event_is_still_accepted(
    engine: ControlRoomEngine,
) -> None:
    """Gateways run fast; a minute ahead is skew, not a forged timestamp."""
    skewed = (AS_OF + timedelta(seconds=60)).isoformat().replace("+00:00", "Z")

    result = telemetry.apply_text(engine, row(ts=skewed))

    assert len(result.applied) == 1
    assert not result.rejected


def test_staleness_on_the_device_is_measured_from_the_event_clock(
    engine: ControlRoomEngine,
) -> None:
    behind = (AS_OF - timedelta(seconds=120)).isoformat().replace("+00:00", "Z")

    telemetry.apply_text(engine, row(ts=behind))

    assert engine.device("BAT-001").last_telemetry_s == 120


def test_the_stale_window_is_configurable(engine: ControlRoomEngine) -> None:
    old = (AS_OF - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    text = "\n".join([row(), row(device_id="BAT-002", ts=old)])

    result = telemetry.apply_text(engine, text, max_age_s=3 * 3600)

    assert len(result.applied) == 2
    assert not result.rejected


def test_a_device_reporting_twice_keeps_the_newest_row(engine: ControlRoomEngine) -> None:
    older = (AS_OF - timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
    text = "\n".join([row(ts=older, soc_kwh=5.0), row(soc_kwh=11.0)])

    result = telemetry.apply_text(engine, text)

    assert result.superseded == 1
    assert len(result.applied) == 1
    assert engine.device("BAT-001").available_kwh == pytest.approx(11.0, abs=0.02)


def test_blank_and_comment_lines_are_skipped_not_rejected(engine: ControlRoomEngine) -> None:
    result = telemetry.apply_text(engine, "\n# a note\n\n" + row() + "\n")

    assert len(result.applied) == 1
    assert not result.rejected


def test_unknown_keys_are_ignored_so_a_richer_export_still_loads(
    engine: ControlRoomEngine,
) -> None:
    result = telemetry.apply_text(engine, row(temperature_c=31.4, cycles=812))

    assert len(result.applied) == 1
    assert not result.rejected


def test_the_import_is_written_to_the_audit_trail(engine: ControlRoomEngine) -> None:
    telemetry.apply_text(engine, row(), source="export.jsonl")

    event = engine.audit[-1]
    assert event.kind == "telemetry"
    assert "export.jsonl" in event.summary
    assert "committed" in event.detail


def test_the_bundled_sample_is_valid_and_moves_the_fleet(engine: ControlRoomEngine) -> None:
    """The shipped sample must load clean, or 'point it at an export' is a claim only."""
    start = engine.snapshot()
    before = (start.online, start.offline, start.available_capacity_kwh)

    result = telemetry.apply_file(engine, telemetry.SAMPLE_FILE)
    after = engine.snapshot()

    assert result.rejected == ()
    assert len(result.applied) == 48 == result.devices
    assert after.offline > before[1]
    assert after.available_capacity_kwh < before[2]
    assert all(d.firmware for d in engine.devices)


def test_the_bundled_bad_rows_demonstrate_every_class_of_rejection(
    engine: ControlRoomEngine,
) -> None:
    result = telemetry.apply_file(engine, telemetry.BAD_ROWS_FILE)

    assert len(result.applied) == 1
    assert set(result.reasons) == {
        "malformed",
        "stale",
        "ahead of the event clock",
        "out of range",
        "unknown device in this fleet",
    }
    assert sum(result.reasons.values()) == len(result.rejected) == 10


def test_the_sample_files_are_exactly_what_the_generator_writes() -> None:
    """Regenerating the bundled samples must reproduce the committed bytes."""
    maker = load_script("make_telemetry_sample")

    expected = "\n".join(json.dumps(r) for r in maker.rows()) + "\n"
    assert telemetry.SAMPLE_FILE.read_text(encoding="utf-8") == expected
    assert telemetry.BAD_ROWS_FILE.read_text(encoding="utf-8") == "\n".join(maker.bad_rows()) + "\n"


def test_a_missing_file_says_so_rather_than_traceback(engine: ControlRoomEngine) -> None:
    with pytest.raises(telemetry.TelemetryError, match="cannot read"):
        telemetry.apply_file(engine, telemetry.SAMPLE_DIR / "not_here.jsonl")


def test_the_cli_reports_rows_applied_rejected_and_the_fleet_after(capsys) -> None:  # type: ignore[no-untyped-def]
    assert telemetry.main([str(telemetry.BAD_ROWS_FILE), "--devices", "48"]) == 0

    out = capsys.readouterr().out
    assert "10 rejected" in out
    assert "stale:" in out
    assert "fleet after import" in out
    assert "no device or utility system was contacted" in out
