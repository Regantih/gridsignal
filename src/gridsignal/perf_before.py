"""The pre-optimisation code paths, kept so the "before" column is reproducible.

Q9 replaced three hot paths. This module holds the versions they replaced, exactly as
they stood at commit ``ed4fec2``, and swaps them back in for the duration of a
benchmark run so the before and after numbers come out of the same machine, the same
fleet and the same command::

    python -m gridsignal.perf --before

Nothing in the product imports this: it exists for the benchmark and for the test
that asserts the old and the new signature cover the same fields.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace

from gridsignal import home
from gridsignal.control_room.engine import MIN_DISPATCH_HOURS, ControlRoomEngine
from gridsignal.control_room.models import Device, DeviceStatus
from gridsignal.mesh.cards import AgentCard, sign


def body(card: AgentCard) -> dict[str, object]:
    """The dict the old signature was taken over."""
    return {
        "agent_id": card.agent_id,
        "kind": card.kind.value,
        "zone": card.zone,
        "capabilities": {k: round(float(v), 4) for k, v in sorted(card.capabilities.items())},
        "health": card.health.value,
        "last_heartbeat_s": card.last_heartbeat_s,
        "controller": card.controller,
    }


def canonical(card: AgentCard) -> str:
    """Old: serialise the whole card to JSON on every sign and every verify."""
    return json.dumps(body(card), sort_keys=True, separators=(",", ":"))


def signed(card: AgentCard, key: bytes) -> AgentCard:
    """Old: rebuild the frozen card through :func:`dataclasses.replace`."""
    return replace(card, signature=sign(card, key))


def discharge_headroom_kw(engine: ControlRoomEngine, device: Device) -> float:
    """Old: recompute the event window from the clock for every device, every call."""
    if not device.is_dispatchable or not device.is_operator_controlled:
        return 0.0
    trust = 0.5 if device.status is DeviceStatus.DEGRADED else 1.0
    hours_left = max(engine.remaining_hours(), MIN_DISPATCH_HOURS)
    reserve_kwh = home.reserve_kwh(device, engine.reserve_fraction)
    energy_kw = max(0.0, device.available_kwh - reserve_kwh) / hours_left
    return round(max(min(device.power_kw * trust, energy_kw), 0.0), 3)


def _share(engine: ControlRoomEngine, pool: list[Device], target: float) -> float:
    """Old: read each device's headroom again for the sum and again for the split."""
    total_headroom = sum(engine._headroom_kw(d) for d in pool)
    if total_headroom <= 0 or target <= 0:
        return 0.0
    share = min(target, total_headroom)
    for device in pool:
        device.assigned_kw = round(share * engine._headroom_kw(device) / total_headroom, 2)
    return round(sum(d.assigned_kw for d in pool), 2)


def allocate_dispatch(engine: ControlRoomEngine) -> float:
    """Old: the same allocation, with headroom recomputed at every step."""
    for device in engine.mine:
        device.assigned_kw = 0.0
    pool = [d for d in engine.mine if engine._headroom_kw(d) > 0]
    total_headroom = sum(engine._headroom_kw(d) for d in pool)
    if total_headroom <= 0:
        return 0.0
    target = min(engine.grid_event.target_kw, total_headroom)
    first = [d for d in pool if d.zone == engine.priority_zone]
    if not first:
        return _share(engine, pool, target)
    committed = _share(engine, first, target)
    rest = [d for d in pool if d.zone != engine.priority_zone]
    if not rest:
        return committed
    return round(committed + _share(engine, rest, target - committed), 2)


@contextmanager
def slow_paths() -> Iterator[None]:
    """Run the block against the pre-Q9 implementations, then restore the fast ones."""
    fast = (
        AgentCard.canonical,
        AgentCard.signed,
        ControlRoomEngine.discharge_headroom_kw,
        ControlRoomEngine._allocate_dispatch,
    )
    AgentCard.canonical = canonical  # type: ignore[method-assign]
    AgentCard.signed = signed  # type: ignore[method-assign]
    ControlRoomEngine.discharge_headroom_kw = discharge_headroom_kw  # type: ignore[method-assign]
    ControlRoomEngine._allocate_dispatch = allocate_dispatch  # type: ignore[method-assign]
    try:
        yield
    finally:
        (
            AgentCard.canonical,  # type: ignore[method-assign]
            AgentCard.signed,  # type: ignore[method-assign]
            ControlRoomEngine.discharge_headroom_kw,  # type: ignore[method-assign]
            ControlRoomEngine._allocate_dispatch,  # type: ignore[method-assign]
        ) = fast
