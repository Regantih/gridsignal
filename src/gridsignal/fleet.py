"""Deterministic synthetic home-battery fleet used by the control room.

The fleet is simulated. No real device, utility or operational system is contacted.
"""

from __future__ import annotations

import random

from gridsignal.control_room.models import Device, DeviceStatus

ZONES: list[tuple[str, str, float, float]] = [
    ("LZ_HOUSTON", "Houston", 29.76, -95.37),
    ("LZ_SOUTH", "San Antonio", 29.42, -98.49),
    ("LZ_NORTH", "Dallas", 32.78, -96.80),
    ("LZ_WEST", "Midland", 31.99, -102.08),
    ("LZ_AUSTIN", "Austin", 30.27, -97.74),
]

FLEET_SIZE = 48
FOCUS_DEVICE_ID = "BAT-042"
DEFAULT_SEED = 42


def _street(rng: random.Random, city: str) -> str:
    names = ["Pecan", "Comal", "Brazos", "Guadalupe", "Sabine", "Nueces", "Lavaca", "Colorado"]
    return f"{rng.randrange(100, 9999)} {rng.choice(names)} St, {city}"


def build_fleet(seed: int = DEFAULT_SEED, size: int = FLEET_SIZE) -> list[Device]:
    """Build the same fleet every time for a given seed."""
    rng = random.Random(seed)
    devices: list[Device] = []
    for i in range(1, size + 1):
        zone, city, lat, lon = ZONES[i % len(ZONES)]
        device = Device(
            device_id=f"BAT-{i:03d}",
            site=_street(rng, city),
            zone=zone,
            lat=round(lat + rng.uniform(-0.35, 0.35), 4),
            lon=round(lon + rng.uniform(-0.35, 0.35), 4),
            capacity_kwh=round(rng.choice([25.0, 27.5, 30.0]), 1),
            state_of_charge=round(rng.uniform(0.62, 0.98), 3),
            power_kw=round(rng.choice([5.0, 7.5, 10.0]), 1),
            status=DeviceStatus.ONLINE,
            last_telemetry_s=rng.randrange(2, 25),
        )
        devices.append(device)

    # A couple of devices start degraded so the fleet looks realistic, not perfect.
    for device_id in ("BAT-007", "BAT-031"):
        for device in devices:
            if device.device_id == device_id:
                device.status = DeviceStatus.DEGRADED
                device.last_telemetry_s = 95
    return devices
