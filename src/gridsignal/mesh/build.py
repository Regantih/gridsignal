"""Turn the simulated fleet into a mesh of agents: batteries, gateways, zones.

The Control Room engine owns fleet state; the mesh is derived from it. Every card here
is a projection of a :class:`Device` at one instant, and the mesh never writes a device
back — awards are applied through the Control Room's dispatch path. See
``docs/architecture.md`` ("One fleet state").

Each device publishes what it could still do *on top of* what it already promised to
the grid event and to its own home, so a bid is always spare capacity: never a re-sale
of committed kW, and never the member's backup reserve, which is netted out here with
the same :func:`gridsignal.home.reserve_kwh` the Control Room holds back.
"""

from __future__ import annotations

from collections import defaultdict

from gridsignal import home
from gridsignal.control_room.models import Device, DeviceStatus
from gridsignal.fleet import GATEWAY_RING_SIZE
from gridsignal.mesh.cards import AgentCard, AgentKind, Health, battery_card
from gridsignal.mesh.messages import MessageBus, MessageKind
from gridsignal.mesh.registry import AgentRegistry

#: Share of a battery's spare power it pre-agrees, in its card, to deploy locally on an
#: under-frequency crossing without waiting for the coordinator. The remainder stays
#: biddable, so a local deployment never eats the homeowner's backup reserve.
FFR_SHARE = 0.25
#: The simulated under-frequency threshold written into every battery card. Concept
#: borrowed from ERCOT Fast Frequency Response; no real frequency feed is read.
FFR_TRIGGER_HZ = 59.85

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


def power_derate(device: Device) -> float:
    """How much of the inverter's nameplate power a device is trusted for.

    Published on the card so the deliverability check does not derate a degraded
    battery a second time on top of what is already netted out here.
    """
    return 0.5 if device.status is DeviceStatus.DEGRADED else 1.0


def spare_kw(device: Device, hours: float, reserve_fraction: float | None = None) -> float:
    """Exportable power beyond the current commitment, after the home is served."""
    if not device.is_dispatchable or not device.is_operator_controlled:
        return 0.0
    trust = power_derate(device)
    # The energy limit already nets off what this device promised the event, so the
    # commitment comes out of the inverter limit only — subtracting it from both would
    # hide spare kW twice over.
    energy_limit = spare_kwh(device, hours, reserve_fraction) / max(hours, 1e-6)
    power_limit = device.power_kw * trust - device.home_load_kw - device.assigned_kw
    return round(max(min(power_limit, energy_limit), 0.0), 3)


def spare_kwh(device: Device, hours: float, reserve_fraction: float | None = None) -> float:
    """Stored energy promised neither to the event, nor the home, nor the reserve.

    The member's backup reserve is the same figure the Control Room holds back, so a
    bid can never reach into it whatever the fleet's reserve policy is set to.
    """
    if not device.is_dispatchable or not device.is_operator_controlled:
        return 0.0
    fraction = home.DEFAULT_RESERVE_FRACTION if reserve_fraction is None else reserve_fraction
    used = (device.assigned_kw + device.home_load_kw) * hours
    reserve = home.reserve_kwh(device, fraction)
    return round(max(device.available_kwh - used - reserve, 0.0), 3)


def card_for(device: Device, hours: float, reserve_fraction: float | None = None) -> AgentCard:
    """Project one device into the card its agent publishes.

    ``reserve_fraction`` is the fleet's current reserve policy, so a Control Room that
    raises the floor before a storm immediately shrinks what the mesh may bid.
    """
    fraction = home.DEFAULT_RESERVE_FRACTION if reserve_fraction is None else reserve_fraction
    soc = device.state_of_charge if device.is_dispatchable else 0.0
    card = battery_card(
        agent_id=device.device_id,
        zone=device.zone,
        kw_available=spare_kw(device, hours, fraction),
        kwh_available=spare_kwh(device, hours, fraction),
        health=HEALTH_OF[device.status],
        controller=device.controller.value,
    )
    return AgentCard(
        agent_id=card.agent_id,
        kind=card.kind,
        zone=card.zone,
        capabilities={
            **card.capabilities,
            "soc": round(soc, 4),
            # Declared so a coordinator can see the member reserve is already netted
            # out of kwh_available and need not guess at a flat floor of its own.
            "reserve_kwh": (
                home.reserve_kwh(device, fraction) if device.is_operator_controlled else 0.0
            ),
            # The derate already applied to kw_available, so a checker downstream
            # holds this battery to it without applying its own on top.
            "power_derate": power_derate(device),
            # The pre-agreed local rule, signed into the card: how much this battery
            # deploys by itself if frequency crosses the trigger.
            "ffr_kw": round(FFR_SHARE * spare_kw(device, hours, fraction), 3),
            "ffr_trigger_hz": FFR_TRIGGER_HZ,
        },
        health=card.health,
        last_heartbeat_s=card.last_heartbeat_s,
        controller=card.controller,
    )


def register_fleet(
    registry: AgentRegistry,
    devices: list[Device],
    hours: float,
    bus: MessageBus | None = None,
    reserve_fraction: float | None = None,
) -> None:
    """Register one agent per battery, per gateway ring and per load zone."""
    gateway_kw: dict[str, float] = defaultdict(float)
    zone_kw: dict[str, float] = defaultdict(float)
    for device in devices:
        card = registry.sign(card_for(device, hours, reserve_fraction))
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
