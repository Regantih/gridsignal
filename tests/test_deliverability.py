"""The pre-award proof that a battery can hold its award for the whole window."""

from __future__ import annotations

import pytest

from gridsignal import deliverability_report
from gridsignal.control_room.models import Device, DeviceStatus
from gridsignal.mesh.build import card_for, heartbeat_all, register_fleet
from gridsignal.mesh.cards import CardStatus, Health, battery_card, derived_signing_key
from gridsignal.mesh.deliverability import DEGRADED_DERATE, check
from gridsignal.mesh.messages import MessageBus, MessageKind
from gridsignal.mesh.negotiation import Coordinator
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
    registry = AgentRegistry(key=derived_signing_key(11), stale_after_s=stale_after_s)
    bus = MessageBus()
    register_fleet(registry, devices, HOURS, bus)
    return registry, bus


def auction(registry: AgentRegistry, bus: MessageBus, gap_kw: float):
    coordinator = Coordinator(registry, bus)
    call = coordinator.call_for_capacity(gap_kw, HOURS)
    return coordinator, call, coordinator.collect_bids(call)


# ------------------------------------------------------------------ the check


def test_energy_binds_when_the_window_is_longer_than_the_battery_can_hold() -> None:
    card = battery_card("BAT-001", "LZ_HOUSTON", kw_available=5.0, kwh_available=4.0)
    verdict = check(card, CardStatus.VERIFIED, hours=4.0, requested_kw=5.0)
    assert verdict.deliverable_kw == pytest.approx(1.0)
    assert not verdict.ok and not verdict.rejected
    assert "holds only" in verdict.reason


def test_the_member_reserve_is_never_sold() -> None:
    card = battery_card("BAT-002", "LZ_HOUSTON", kw_available=5.0, kwh_available=8.0)
    verdict = check(card, CardStatus.VERIFIED, hours=2.0, requested_kw=4.0, reserve_kwh=4.0)
    assert verdict.deliverable_kw == pytest.approx(2.0)
    assert verdict.trimmed_kw == pytest.approx(2.0)


def test_other_awards_come_off_both_limits() -> None:
    card = battery_card("BAT-003", "LZ_HOUSTON", kw_available=5.0, kwh_available=20.0)
    verdict = check(card, CardStatus.VERIFIED, hours=2.0, requested_kw=5.0, committed_kw=3.0)
    assert verdict.deliverable_kw == pytest.approx(2.0)
    assert "already committed" in verdict.reason


def test_a_degraded_card_that_declares_no_derate_is_derated_here() -> None:
    card = battery_card(
        "BAT-004", "LZ_HOUSTON", kw_available=6.0, kwh_available=40.0, health=Health.DEGRADED
    )
    verdict = check(card, CardStatus.VERIFIED, hours=2.0, requested_kw=6.0)
    assert verdict.deliverable_kw == pytest.approx(6.0 * DEGRADED_DERATE)
    assert "degraded" in verdict.reason


def test_a_degraded_battery_is_never_derated_twice() -> None:
    degraded = device("BAT-005", kw=6.0, kwh=40.0)
    degraded.status = DeviceStatus.DEGRADED
    card = card_for(degraded, HOURS)
    verdict = check(card, CardStatus.VERIFIED, HOURS, card.capability("kw_available"))
    assert verdict.ok


@pytest.mark.parametrize("status", [CardStatus.STALE, CardStatus.REJECTED])
def test_a_card_that_cannot_be_trusted_proves_nothing(status: CardStatus) -> None:
    card = battery_card("BAT-006", "LZ_HOUSTON", kw_available=5.0, kwh_available=50.0)
    verdict = check(card, status, HOURS, requested_kw=5.0)
    assert verdict.deliverable_kw == 0.0
    assert verdict.rejected and "unproven" in verdict.reason


def test_an_offline_agent_delivers_nothing() -> None:
    card = battery_card(
        "BAT-007", "LZ_HOUSTON", kw_available=5.0, kwh_available=50.0, health=Health.OFFLINE
    )
    verdict = check(card, CardStatus.VERIFIED, HOURS, requested_kw=5.0)
    assert verdict.rejected and verdict.reason == "agent is offline"


# ------------------------------------------------------------- in the auction


def test_a_second_call_cannot_resell_energy_the_first_award_already_owes() -> None:
    registry, bus = mesh([device("BAT-001")])
    coordinator, call, bids = auction(registry, bus, gap_kw=4.0)
    coordinator.approve(coordinator.propose(call, bids).call_id, "operator")

    second = coordinator.call_for_capacity(4.0, HOURS)
    # The card still advertises the same spare energy: only the commitment ledger knows.
    award_set = coordinator.propose(second, coordinator.collect_bids(second))
    committed = coordinator.commitments["BAT-001"]
    assert (
        award_set.covered_kw + committed
        <= card_for(device("BAT-001"), HOURS).capability("kw_available") + 1e-9
    )
    assert award_set.undeliverable_awards >= 1
    assert award_set.undeliverable_kw > 0.0


def test_every_trim_and_rejection_says_why_in_the_trace() -> None:
    registry, bus = mesh([device("BAT-001")])
    coordinator, call, bids = auction(registry, bus, gap_kw=4.0)
    coordinator.approve(coordinator.propose(call, bids).call_id, "operator")
    second = coordinator.call_for_capacity(4.0, HOURS)
    coordinator.propose(second, coordinator.collect_bids(second))

    logged = [m for m in bus.messages if m.kind is MessageKind.DELIVERABILITY]
    assert logged
    for message in logged:
        assert message.payload["reason"]
        assert message.payload["stage"] == "proposal"
        assert message.payload["deliverable_kw"] < message.payload["requested_kw"]


def test_an_award_is_reproved_at_the_approval_gate() -> None:
    """A card can go stale while the operator is deciding; the gate catches it."""
    registry, bus = mesh([device("BAT-001"), device("BAT-002")], stale_after_s=60)
    coordinator, call, bids = auction(registry, bus, gap_kw=6.0)
    award_set = coordinator.propose(call, bids)
    assert len(award_set.awards) == 2

    registry.advance(120)  # both cards fall silent during the approval delay
    registry.heartbeat("BAT-001")  # one of them checks back in, the other does not
    coordinator.approve(call.call_id, "operator")

    assert [a.agent_id for a in award_set.awards] == ["BAT-001"]
    approval_stage = [
        m
        for m in bus.messages
        if m.kind is MessageKind.DELIVERABILITY and m.payload["stage"] == "approval"
    ]
    assert [m.payload["agent_id"] for m in approval_stage] == ["BAT-002"]
    assert coordinator.commitments.get("BAT-002", 0.0) == 0.0


def test_the_check_never_hands_out_more_than_the_gap_or_breaks_idempotency() -> None:
    registry, bus = mesh([device(f"BAT-{i:03d}") for i in range(1, 6)])
    coordinator, call, bids = auction(registry, bus, gap_kw=7.0)
    award_set = coordinator.propose(call, bids)
    assert award_set.covered_kw <= call.gap_kw + 1e-9

    first = coordinator.approve(call.call_id, "operator")
    again = coordinator.approve(call.call_id, "operator")
    assert again is first
    assert sum(coordinator.commitments.values()) == pytest.approx(first.covered_kw, abs=0.01)


def test_an_award_never_outruns_the_energy_behind_it() -> None:
    small = device("BAT-001", kw=5.0, kwh=6.0)  # plenty of power, little energy
    big = device("BAT-002", kw=5.0, kwh=30.0)
    registry, bus = mesh([small, big])
    coordinator, call, bids = auction(registry, bus, gap_kw=9.0)
    award_set = coordinator.propose(call, bids)
    assert {a.agent_id for a in award_set.awards} == {"BAT-001", "BAT-002"}
    for award in award_set.awards:
        # kwh_available already has the member's reserve netted out of it.
        card = registry.card(award.agent_id)
        assert award.kw * HOURS <= card.capability("kwh_available") + 1e-6


# --------------------------------------------------------------- chaos drills


@pytest.mark.parametrize(
    "slug", ["cascade_spain_style", "frequency_dip_coordinator_down", "neighborhood_island"]
)
def test_held_out_drills_commit_nothing_undeliverable(slug: str) -> None:
    from gridsignal.mesh.scenarios import HOLDOUT_DIR, load_scenario
    from gridsignal.simulate import run_scenario

    result = run_scenario(load_scenario(HOLDOUT_DIR / f"{slug}.yaml"))
    assert result.metrics.backup_violations == 0
    for award_set in result.awards:
        for verdict in award_set.checks:
            assert verdict.reason
            assert verdict.deliverable_kw < verdict.requested_kw
        for award in award_set.awards:
            assert award.kw > 0.0
    for message in result.bus.messages:
        if message.kind is MessageKind.DELIVERABILITY:
            assert message.payload["reason"]


def test_a_stale_telemetry_wave_never_wins_an_award() -> None:
    from gridsignal.mesh.scenarios import load_scenario
    from gridsignal.paths import SCENARIO_DIR
    from gridsignal.simulate import run_scenario

    result = run_scenario(load_scenario(SCENARIO_DIR / "silent_bidder.yaml"))
    for award_set in result.awards:
        for award in award_set.awards:
            assert result.registry.status(award.agent_id) in {
                CardStatus.VERIFIED,
                CardStatus.STALE,  # may fall silent only after the award is executed
            }
    assert result.metrics.backup_violations == 0


# -------------------------------------------------------------------- report


def test_the_report_is_deterministic_and_counts_the_awards_it_saved() -> None:
    paths = [deliverability_report.SCENARIO_DIR / "silent_bidder.yaml"]
    first = deliverability_report.run(paths)
    second = deliverability_report.run(paths)
    assert first == second

    row = first.rows[0]
    assert row.undeliverable_awards >= 1
    assert row.undeliverable_kw > 0.0
    assert row.backup_violations == 0
    assert "Without the pre-award check" in deliverability_report.headline(first)
    assert any("undeliverable" in line for line in deliverability_report.lines(first))


def test_turning_the_check_off_is_what_makes_the_counterfactual_honest() -> None:
    from gridsignal.mesh.scenarios import load_scenario
    from gridsignal.paths import SCENARIO_DIR
    from gridsignal.simulate import run_scenario

    scenario = load_scenario(SCENARIO_DIR / "silent_bidder.yaml")
    unchecked = run_scenario(scenario, check_deliverability=False)
    checked = run_scenario(scenario)
    assert unchecked.metrics.covered_kw >= checked.metrics.covered_kw
    assert unchecked.metrics.awards_trimmed == 0
    assert checked.metrics.awards_trimmed + checked.metrics.awards_rejected >= 1


def test_heartbeats_keep_a_healthy_fleet_fully_deliverable() -> None:
    devices = [device(f"BAT-{i:03d}") for i in range(1, 11)]
    registry, bus = mesh(devices)
    heartbeat_all(registry, set())
    coordinator, call, bids = auction(registry, bus, gap_kw=10.0)
    award_set = coordinator.propose(call, bids)
    assert award_set.checks == ()
    assert award_set.undeliverable_kw == 0.0
