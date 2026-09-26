"""Agent registry and contract-net negotiation: trust, staleness, idempotency."""

from __future__ import annotations

import pytest

from gridsignal.control_room import ControlRoomEngine
from gridsignal.control_room.models import Device, DeviceStatus
from gridsignal.fleet import FOCUS_DEVICE_ID
from gridsignal.home import DEFAULT_RESERVE_FRACTION, STORM_RESERVE_FRACTION, reserve_kwh
from gridsignal.mesh.build import card_for, gateway_id, heartbeat_all, register_fleet, zone_id
from gridsignal.mesh.cards import (
    AgentCard,
    AgentKind,
    CardStatus,
    Health,
    battery_card,
    derived_signing_key,
    new_signing_key,
)
from gridsignal.mesh.llm import LLMCoordinator
from gridsignal.mesh.messages import MessageBus, MessageKind
from gridsignal.mesh.negotiation import (
    ApprovalRequired,
    Bid,
    Coordinator,
    bid_price_usd_per_kw,
)
from gridsignal.mesh.registry import AgentRegistry

HOURS = 2.0


def device(device_id: str, kw: float = 5.0, kwh: float = 13.5, soc: float = 1.0) -> Device:
    return Device(
        device_id=device_id,
        site=f"Site {device_id}",
        zone="LZ_HOUSTON",
        lat=30.0,
        lon=-97.0,
        capacity_kwh=kwh,
        state_of_charge=soc,
        power_kw=kw,
    )


def mesh(devices: list[Device], stale_after_s: int = 120) -> tuple[AgentRegistry, MessageBus]:
    registry = AgentRegistry(key=derived_signing_key(7), stale_after_s=stale_after_s)
    bus = MessageBus()
    register_fleet(registry, devices, HOURS, bus)
    return registry, bus


# --------------------------------------------------------------------- cards


def test_signing_key_is_random_at_startup() -> None:
    assert new_signing_key() != new_signing_key()
    assert len(new_signing_key()) == 32


def test_card_signature_verifies_and_covers_every_field() -> None:
    key = new_signing_key()
    card = battery_card("BAT-001", "LZ_HOUSTON", kw_available=3.0, kwh_available=9.0).signed(key)
    assert card.verifies(key)
    assert not card.verifies(new_signing_key())


def test_registry_rejects_a_card_whose_signature_does_not_verify() -> None:
    registry = AgentRegistry(key=derived_signing_key(1))
    forged = AgentCard(
        agent_id="BAT-666",
        kind=AgentKind.BATTERY,
        zone="LZ_WEST",
        capabilities={"kw_available": 500.0},
        signature="deadbeef",
    )
    assert registry.register(forged) is CardStatus.REJECTED
    assert registry.status("BAT-666") is CardStatus.REJECTED
    assert registry.discover("kw_available") == []


def test_a_lying_agent_is_rejected_after_editing_its_card() -> None:
    devices = [device("BAT-001"), device("BAT-002")]
    registry, _ = mesh(devices)
    assert registry.status("BAT-001") is CardStatus.VERIFIED

    registry.tamper("BAT-001", kw_available=500.0)

    assert registry.status("BAT-001") is CardStatus.REJECTED
    assert [c.agent_id for c in registry.discover("kw_available", kind=AgentKind.BATTERY)] == [
        "BAT-002"
    ]


# ----------------------------------------------------------------- staleness


def test_agents_go_stale_when_heartbeats_stop_and_come_back_when_they_resume() -> None:
    registry, _ = mesh([device("BAT-001"), device("BAT-002")], stale_after_s=60)
    registry.advance(61)
    heartbeat_all(registry, silent={"BAT-002"})

    assert registry.status("BAT-001") is CardStatus.VERIFIED
    assert registry.status("BAT-002") is CardStatus.STALE
    assert "BAT-002" not in [c.agent_id for c in registry.discover("kw_available")]

    heartbeat_all(registry)
    assert registry.status("BAT-002") is CardStatus.VERIFIED


def test_registry_holds_a_gateway_and_zone_agent_for_every_battery() -> None:
    devices = [device(f"BAT-{i:03d}") for i in range(1, 5)]
    registry, bus = mesh(devices)
    ids = set(registry.agent_ids())

    assert {gateway_id(d.device_id) for d in devices} <= ids
    assert zone_id("LZ_HOUSTON") in ids
    assert len(bus.of_kind(MessageKind.REGISTER)) == 1
    assert registry.card(zone_id("LZ_HOUSTON")).kind is AgentKind.ZONE


def test_discovery_filters_by_capability_kind_and_zone() -> None:
    small = device("BAT-001", kw=0.0, kwh=0.0)
    big = device("BAT-002", kw=5.0)
    registry, _ = mesh([small, big])

    found = registry.discover("kw_available", minimum=1.0, kind=AgentKind.BATTERY)
    assert [c.agent_id for c in found] == ["BAT-002"]
    assert registry.discover("kw_available", kind=AgentKind.BATTERY, zone="LZ_WEST") == []


def test_offline_devices_publish_no_capacity() -> None:
    dead = device("BAT-042")
    dead.status = DeviceStatus.OFFLINE
    card = card_for(dead, HOURS)

    assert card.health is Health.OFFLINE
    assert card.capability("kw_available") == 0.0


# --------------------------------------------------------------- negotiation


def test_bid_price_reflects_wear_and_the_homeowners_backup_reserve() -> None:
    full = bid_price_usd_per_kw(1.0, Health.HEALTHY)
    low = bid_price_usd_per_kw(0.2, Health.HEALTHY)
    degraded = bid_price_usd_per_kw(1.0, Health.DEGRADED)

    assert low > full > 0.0
    assert degraded > full


def test_coordinator_awards_the_cheapest_set_that_covers_the_gap() -> None:
    devices = [
        device("BAT-001", soc=1.0),
        device("BAT-002", soc=0.6),
        device("BAT-003", soc=1.0),
    ]
    registry, bus = mesh(devices)
    coordinator = Coordinator(registry, bus)

    call = coordinator.call_for_capacity(3.0, HOURS)
    bids = coordinator.collect_bids(call)
    award_set = coordinator.propose(call, bids)

    assert len(bids) == 3
    assert award_set.coverage_pct == 100.0
    # The expensive, low-state-of-charge battery is not needed, so it is not used.
    assert "BAT-002" not in {a.agent_id for a in award_set.awards}
    assert bus.of_kind(MessageKind.AWARD_PROPOSED)


def test_nothing_is_committed_until_a_human_approves() -> None:
    registry, bus = mesh([device("BAT-001"), device("BAT-002")])
    coordinator = Coordinator(registry, bus)
    call = coordinator.call_for_capacity(2.0, HOURS)
    award_set = coordinator.propose(call, coordinator.collect_bids(call))

    with pytest.raises(ApprovalRequired):
        coordinator.execute(call.call_id)
    assert coordinator.total_committed_kw() == 0.0
    assert not award_set.executed

    coordinator.approve(call.call_id, "M. Alvarez (Fleet Operator)")

    assert coordinator.execute(call.call_id).approved_by == "M. Alvarez (Fleet Operator)"
    assert coordinator.total_committed_kw() == pytest.approx(2.0, abs=0.01)


def test_approving_an_unproposed_call_is_refused() -> None:
    registry, bus = mesh([device("BAT-001")])
    with pytest.raises(ApprovalRequired):
        Coordinator(registry, bus).approve("CFC-999", "nobody")


def test_double_approval_and_repeat_triggers_never_double_count() -> None:
    registry, bus = mesh([device("BAT-001"), device("BAT-002")])
    coordinator = Coordinator(registry, bus)

    call = coordinator.call_for_capacity(2.0, HOURS, call_id="CFC-001")
    first = coordinator.propose(call, coordinator.collect_bids(call))
    coordinator.approve(call.call_id, "operator")
    committed = coordinator.total_committed_kw()

    # Same trigger again, then a double-click on approve.
    repeat = coordinator.call_for_capacity(2.0, HOURS, call_id="CFC-001")
    again = coordinator.propose(repeat, coordinator.collect_bids(repeat))
    coordinator.approve(call.call_id, "operator")
    coordinator.approve(call.call_id, "someone else")

    assert repeat is call
    assert again is first
    assert coordinator.total_committed_kw() == committed
    assert len(bus.of_kind(MessageKind.AWARD_EXECUTED)) == 1
    assert len(bus.of_kind(MessageKind.AWARD_IGNORED)) == 2


def test_partial_cover_escalates_instead_of_pretending() -> None:
    registry, bus = mesh([device("BAT-001", kw=2.0), device("BAT-002", kw=2.0)])
    coordinator = Coordinator(registry, bus)

    call = coordinator.call_for_capacity(50.0, HOURS)
    award_set = coordinator.propose(call, coordinator.collect_bids(call))

    assert award_set.escalated
    assert 0.0 < award_set.covered_kw < 50.0
    assert award_set.uncovered_kw > 0.0
    assert bus.of_kind(MessageKind.ESCALATION)


def test_a_bidder_that_goes_silent_after_the_award_is_reported_as_a_defaulter() -> None:
    registry, bus = mesh([device("BAT-001"), device("BAT-002")], stale_after_s=60)
    coordinator = Coordinator(registry, bus)
    call = coordinator.call_for_capacity(3.0, HOURS)
    award_set = coordinator.propose(call, coordinator.collect_bids(call))
    coordinator.approve(call.call_id, "operator")

    registry.advance(61)
    heartbeat_all(registry, silent={"BAT-001"})

    assert coordinator.defaulters(award_set) == ["BAT-001"]
    assert bus.of_kind(MessageKind.HEARTBEAT_LOST)


def test_awards_never_include_a_rejected_or_stale_agent() -> None:
    registry, bus = mesh(
        [device("BAT-001"), device("BAT-002"), device("BAT-003")], stale_after_s=60
    )
    registry.tamper("BAT-001", kw_available=999.0)
    registry.advance(61)
    heartbeat_all(registry, silent={"BAT-002"})

    coordinator = Coordinator(registry, bus)
    call = coordinator.call_for_capacity(2.0, HOURS)
    award_set = coordinator.propose(call, coordinator.collect_bids(call))

    assert {a.agent_id for a in award_set.awards} == {"BAT-003"}


def test_excluded_agents_do_not_bid() -> None:
    registry, bus = mesh([device("BAT-001"), device("BAT-002")])
    coordinator = Coordinator(registry, bus)
    call = coordinator.call_for_capacity(2.0, HOURS, exclude=("BAT-001",))

    assert [b.agent_id for b in coordinator.collect_bids(call)] == ["BAT-002"]


def test_large_fleets_summarise_bids_instead_of_logging_every_one() -> None:
    registry, bus = mesh([device(f"BAT-{i:03d}") for i in range(1, 6)])
    coordinator = Coordinator(registry, bus)
    call = coordinator.call_for_capacity(2.0, HOURS)
    coordinator.collect_bids(call, log_each=False)

    assert len(bus.of_kind(MessageKind.BID)) == 1


# ------------------------------------------------------------ llm coordinator


def test_llm_coordinator_is_off_by_default_and_needs_no_key() -> None:
    registry, bus = mesh([device("BAT-001"), device("BAT-002")])
    coordinator = LLMCoordinator(registry, bus)

    assert coordinator.enabled is False
    call = coordinator.call_for_capacity(2.0, HOURS)
    award_set = coordinator.propose(call, coordinator.collect_bids(call))
    assert award_set.coverage_pct == 100.0
    assert not bus.of_kind(MessageKind.ESCALATION)


def test_enabled_llm_coordinator_without_a_provider_falls_back_and_says_so() -> None:
    registry, bus = mesh([device("BAT-001"), device("BAT-002")])
    coordinator = LLMCoordinator(registry, bus, enabled=True)
    call = coordinator.call_for_capacity(2.0, HOURS)
    coordinator.propose(call, coordinator.collect_bids(call))

    assert bus.of_kind(MessageKind.ESCALATION)


def test_a_supplied_ranker_decides_the_award_order() -> None:
    registry, bus = mesh([device("BAT-001"), device("BAT-002")])

    def most_expensive_first(_: object, bids: list[Bid]) -> list[Bid]:
        return sorted(bids, key=lambda b: (-b.price_usd_per_kw, b.agent_id))

    coordinator = LLMCoordinator(registry, bus, enabled=True, ranker=most_expensive_first)
    call = coordinator.call_for_capacity(1.0, HOURS)
    bids = coordinator.collect_bids(call)
    award_set = coordinator.propose(call, bids)

    assert award_set.awards[0].agent_id == most_expensive_first(call, bids)[0].agent_id


# --------------------------------------------- one fleet state: reserve held exactly once


def test_cards_hold_back_the_same_member_reserve_the_control_room_does():
    """A card's spare energy is what is left after the event, the home and the reserve."""
    eng = ControlRoomEngine()
    hours = 2.0

    checked = 0
    for device in eng.mine:
        card = card_for(device, hours)
        committed_kwh = (device.assigned_kw + device.home_load_kw) * hours
        reserve = reserve_kwh(device, eng.reserve_fraction)
        assert card.capability("reserve_kwh") == reserve
        assert (
            card.capability("kwh_available")
            <= device.available_kwh - committed_kwh - reserve + 1e-6
        )
        checked += 1
    assert checked > 0


def test_raising_the_reserve_floor_shrinks_what_the_mesh_may_bid():
    """The two engines share one reserve policy, so a storm floor reaches the auction."""
    eng = ControlRoomEngine()
    device = eng.device(FOCUS_DEVICE_ID)

    normal = card_for(device, 2.0, DEFAULT_RESERVE_FRACTION).capability("kwh_available")
    storm = card_for(device, 2.0, STORM_RESERVE_FRACTION).capability("kwh_available")

    extra_reserve = reserve_kwh(device, STORM_RESERVE_FRACTION) - reserve_kwh(
        device, DEFAULT_RESERVE_FRACTION
    )
    assert extra_reserve > 0
    assert storm == pytest.approx(max(normal - extra_reserve, 0.0), abs=0.01)
    assert storm < normal


def test_an_award_taken_in_full_still_leaves_the_member_their_reserve():
    """Run the auction, then spend every awarded kW for the whole window."""
    eng = ControlRoomEngine()
    registry = AgentRegistry()
    bus = MessageBus()
    hours = 2.0
    register_fleet(registry, eng.devices, hours, bus, eng.reserve_fraction)
    coordinator = Coordinator(registry, bus)

    call = coordinator.call_for_capacity(gap_kw=200.0, hours=hours)
    bids = coordinator.collect_bids(call)
    award_set = coordinator.propose(call, bids)
    assert award_set.awards

    for award in award_set.awards:
        device = eng.device(award.agent_id)
        spent = (device.assigned_kw + device.home_load_kw + award.kw) * hours
        left = device.available_kwh - spent
        assert left >= reserve_kwh(device, eng.reserve_fraction) - 1e-6, device.device_id
