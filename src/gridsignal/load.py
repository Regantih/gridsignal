"""Simulated household load, the draw a battery serves before it exports anything.

A synthetic summer-weekday shape for one Texas home, scaled per home so no two
draw the same. Nothing here is measured from a real meter or member.
"""

from __future__ import annotations

import hashlib

#: Simulated whole-home draw by hour of day, in kW.
HOURLY_LOAD_KW: tuple[float, ...] = (
    0.9, 0.8, 0.8, 0.8, 0.8, 0.9, 1.1, 1.3,
    1.2, 1.1, 1.1, 1.2, 1.3, 1.4, 1.6, 1.8,
    2.0, 2.2, 2.3, 2.1, 1.8, 1.5, 1.2, 1.0,
)  # fmt: skip
#: What a home draws once it is islanded and heavy loads are shed.
ESSENTIAL_LOAD_KW = 1.2


def load_multiplier(device_id: str) -> float:
    """Stable 0.7-1.4x household factor, so two homes never draw exactly the same."""
    digest = hashlib.sha256(f"gridsignal-home-{device_id}".encode()).digest()
    return round(0.7 + 0.7 * (digest[0] / 255.0), 3)


def home_load_kw(device_id: str, hour: int) -> float:
    """Simulated household draw for one home at one hour of the day."""
    return round(HOURLY_LOAD_KW[hour % 24] * load_multiplier(device_id), 2)
