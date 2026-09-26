"""Turn the simulated fleet into a mesh of agents: batteries, gateways, zones.

Each device publishes what it could still do *on top of* what it already promised to
the grid event, so a bid is always spare capacity, never a re-sale of committed kW.
"""

from __future__ import annotations

from collections import defaultdict

from gridsignal.control_room.models import Device, DeviceStatus
from gridsignal.fleet import GATEWAY_RING_SIZE
from gridsignal.mesh.cards import AgentCard, AgentKind, Health, battery_card
from gridsignal.mesh.messages import MessageBus, MessageKind
from gridsignal.mesh.registry import AgentRegistry

HEALTH_OF: dict[DeviceStatus, Health] = {
    DeviceStatus.ONLINE: Health.HEALTHY,
    DeviceStatus.DEGRADED: Health.DEGRADED,
    DeviceStatus.OFFLINE: Health.OFFLINE,
    DeviceStatus.UNAVAILABLE: Health.OFFLINE,
}


def gateway_id(device_id: str) -> str:
    """The gateway agent that fronts a device's firmware ring."""
    return f"GW-{int(device_id.split('-')[1]) % GATEWAY_RING_SIZE:02d}"


def zone_id(zone: str) -> str:
    return f"ZONE-{zone}"


def spare_kw(device: Device, hours: float) -> float:
    """Power the device could add beyond its current commitment, energy permitting."""
    if not device.is_dispatchable:
        return 0.0
    trust = 0.5 if device.status is DeviceStatus.DEGRADED else 1.0
    energy_limit = spare_kwh(device, hours) / max(hours, 1e-6)
    return round(max(min(device.power_kw * trust, energy_limit) - device.assigned_kw, 0.0), 3)


def spare_kwh(device: Device, hours: float) -> float:
    """Stored energy not already promised to the event."""
    if not device.is_dispatchable:
        return 0.0
    return round(max(device.available_kwh - device.assigned_kw * hours, 0.0), 3)


def card_for(device: Device, hours: float) -> AgentCard:
    soc = device.state_of_charge if device.is_dispatchable else 0.0
    card = battery_card(
        agent_id=device.device_id,
        zone=device.zone,
        kw_available=spare_kw(device, hours),
        kwh_available=spare_kwh(device, hours),
        health=HEALTH_OF[device.status],
    )
    return AgentCard(
        agent_id=card.agent_id,
        kind=card.kind,
        zone=card.zone,
        capabilities={**card.capabilities, "soc": round(soc, 4)},
        health=card.health,
        last_heartbeat_s=card.last_heartbeat_s,
    )


def register_fleet(
    registry: AgentRegistry,
    devices: list[Device],
    hours: float,
    bus: MessageBus | None = None,
) -> None:
    """Register one agent per battery, per gateway ring and per load zone."""
    gateway_kw: dict[str, float] = defaultdict(float)
    zone_kw: dict[str, float] = defaultdict(float)
    for device in devices:
        card = registry.sign(card_for(device, hours))
        registry.register(card)
        gateway_kw[gateway_id(device.device_id)] += card.capability("kw_available")
        zone_kw[device.zone] += card.capability("kw_available")

    for gateway, kw in sorted(gateway_kw.items()):
        registry.register(
            registry.sign(
                AgentCard(
                    agent_id=gateway,
                    kind=AgentKind.GATEWAY,
                    zone="mesh",
                    capabilities={"kw_available": round(kw, 2)},
                )
            )
        )
    for zone, kw in sorted(zone_kw.items()):
        registry.register(
            registry.sign(
                AgentCard(
                    agent_id=zone_id(zone),
                    kind=AgentKind.ZONE,
                    zone=zone,
                    capabilities={"kw_available": round(kw, 2)},
                )
            )
        )
    if bus is not None:
        bus.send(
            registry.now_s,
            MessageKind.REGISTER,
            "registry",
            "mesh",
            f"{len(registry)} agents registered with signed capability cards",
            batteries=len(devices),
            gateways=len(gateway_kw),
            zones=len(zone_kw),
        )


def heartbeat_all(registry: AgentRegistry, silent: set[str] | None = None) -> None:
    """Every agent except the silent ones checks in at the current simulated second."""
    quiet = silent or set()
    for agent_id in registry.agent_ids():
        if agent_id not in quiet:
            registry.heartbeat(agent_id)
