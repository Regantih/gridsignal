"""Write the bundled synthetic telemetry samples in the documented JSONL format.

    python scripts/make_telemetry_sample.py

Deterministic: the same seed and the same fleet produce the same files, so the samples
committed to the repo can be regenerated and diffed. Nothing is measured from hardware
— these are the simulated fleet's own numbers written out in the real wire format.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, timedelta

from gridsignal import telemetry
from gridsignal.control_room import ControlRoomEngine
from gridsignal.fleet import GATEWAY_RING_SIZE, build_fleet

#: The control room's own event clock, so the sample reads as an export taken during
#: the event the fleet is dispatching into rather than as an archive from years ago.
AS_OF = ControlRoomEngine(fleet_size=1).event_clock.replace(tzinfo=UTC)
FIRMWARE = ("2.4.1", "2.4.0", "2.3.7")


def rows() -> list[dict[str, object]]:
    rng = random.Random(20230906)
    out: list[dict[str, object]] = []
    for index, device in enumerate(build_fleet(size=48)):
        ring = int(device.device_id.split("-")[1]) % GATEWAY_RING_SIZE
        ts = AS_OF - timedelta(seconds=rng.randrange(0, 45))
        # Deliberately not a copy of the simulated fleet's current state: this reads
        # like a later export, so importing it visibly moves the Control Room.
        drift = rng.uniform(0.72, 1.0)
        soc = round(device.capacity_kwh * device.state_of_charge * drift, 2)
        power = round(rng.uniform(-2.0, min(device.power_kw, 6.0)), 2)
        status = "online"
        if index % 16 == 5:
            status = "degraded"
        if index in (11, 29):
            status, soc, power = "offline", 0.0, 0.0
        out.append(
            {
                "device_id": device.device_id,
                "ts": ts.isoformat().replace("+00:00", "Z"),
                "soc_kwh": soc,
                "power_kw": power,
                "status": status,
                "firmware": FIRMWARE[index % len(FIRMWARE)],
                "gateway": f"GW-{ring:02d}",
            }
        )
    return out


def bad_rows() -> list[str]:
    """One row per rejection reason, so the importer's refusals can be demonstrated."""
    good = rows()[0]
    stale = dict(rows()[1], ts=(AS_OF - timedelta(hours=6)).isoformat().replace("+00:00", "Z"))
    missing = {k: v for k, v in rows()[2].items() if k != "soc_kwh"}
    bad_ts = dict(rows()[3], ts="yesterday evening")
    bad_status = dict(rows()[4], status="sleeping")
    bad_number = dict(rows()[5], power_kw="4.2")
    over_nameplate = dict(rows()[6], soc_kwh=999.0)
    unknown = dict(rows()[7], device_id="BAT-999")
    # A row from 2099: it must be refused, not adopted as the clock everything else
    # is judged against.
    future = dict(rows()[8], ts=AS_OF.replace(year=2099).isoformat().replace("+00:00", "Z"))
    return [
        json.dumps(good),
        json.dumps(stale),
        json.dumps(missing),
        json.dumps(bad_ts),
        json.dumps(bad_status),
        json.dumps(bad_number),
        json.dumps(over_nameplate),
        json.dumps(unknown),
        json.dumps(future),
        "{not json at all",
        "[1, 2, 3]",
    ]


def main() -> int:
    telemetry.SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    telemetry.SAMPLE_FILE.write_text(
        "\n".join(json.dumps(r) for r in rows()) + "\n", encoding="utf-8"
    )
    telemetry.BAD_ROWS_FILE.write_text("\n".join(bad_rows()) + "\n", encoding="utf-8")
    print(
        f"wrote {telemetry.SAMPLE_FILE} ({len(rows())} rows) and "
        f"{telemetry.BAD_ROWS_FILE} ({len(bad_rows())} rows)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
