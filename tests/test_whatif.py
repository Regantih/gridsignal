"""The what-if console: parser, arithmetic and the answer it gives at fleet scale."""

from __future__ import annotations

import time

import pytest

from gridsignal import whatif
from gridsignal.control_room import ControlRoomEngine
from gridsignal.fleet import GATEWAY_RING_SIZE
from gridsignal.whatif import ParseError, ScenarioKind

#: The console has to answer inside the time an operator will wait while talking.
ANSWER_BUDGET_S = 2.0
FLEET_AT_SCALE = 10_000


@pytest.fixture(scope="module")
def big() -> ControlRoomEngine:
    return ControlRoomEngine(fleet_size=FLEET_AT_SCALE)


def test_the_parser_reads_the_three_scenarios_the_console_advertises() -> None:
    zone, ring, spike = (whatif.parse(text) for text in whatif.EXAMPLES)

    assert zone.kind is ScenarioKind.ZONE_OUTAGE
    assert (zone.zone, zone.share, zone.at) == ("LZ_HOUSTON", pytest.approx(0.2), (17, 0))
    assert ring.kind is ScenarioKind.RING_DARK
    assert ring.ring == 3
    assert spike.kind is ScenarioKind.PRICE_SPIKE
    assert spike.price_mwh == 3_000.0


def test_the_parser_reads_the_shapes_around_the_examples() -> None:
    whole_zone = whatif.parse("LZ_WEST offline")
    assert (whole_zone.kind, whole_zone.zone, whole_zone.share) == (
        ScenarioKind.ZONE_OUTAGE,
        "LZ_WEST",
        1.0,
    )

    timed = whatif.parse("lz_north down at 19:30")
    assert (timed.kind, timed.at) == (ScenarioKind.ZONE_OUTAGE, (19, 30))

    device = whatif.parse("BAT-042 offline")
    assert (device.kind, device.device_id) == (ScenarioKind.DEVICE_OUTAGE, "BAT-042")

    ring = whatif.parse("gateway ring 7 dark")
    assert (ring.kind, ring.ring) == (ScenarioKind.RING_DARK, 7)

    bare = whatif.parse("price spike to 1500")
    assert (bare.kind, bare.price_mwh) == (ScenarioKind.PRICE_SPIKE, 1_500.0)

    dollars = whatif.parse("$250 price")
    assert (dollars.kind, dollars.price_mwh) == (ScenarioKind.PRICE_SPIKE, 250.0)


@pytest.mark.parametrize(
    "text", ["", "make me a sandwich", "what happens tomorrow", "price spike", "LZ_NORTH at 99:00"]
)
def test_a_scenario_it_cannot_read_is_refused_with_examples_not_guessed_at(text: str) -> None:
    with pytest.raises(ParseError):
        whatif.parse(text)


def test_a_zone_outage_prices_only_the_kw_this_operator_committed(big: ControlRoomEngine) -> None:
    answer = whatif.evaluate(big, whatif.parse("20% of LZ_HOUSTON offline at 17:00"))
    houston = [d for d in big.mine if d.zone == "LZ_HOUSTON" and d.is_dispatchable]

    assert answer.devices_affected == pytest.approx(len(houston) * 0.2, abs=1)
    assert answer.lost_kw > 0
    # Only our own devices: the utility partner's zone is never in the plan or the loss.
    assert "LZ_WEST" not in " ".join(answer.plan)
    assert answer.dollars_at_risk == pytest.approx(
        answer.lost_kw * answer.hours * answer.price_mwh / 1000.0, abs=0.01
    )
    assert answer.recoverable_kw <= min(answer.lost_kw, answer.spare_kw) + 0.01
    assert answer.dollars_recoverable <= answer.dollars_at_risk + 0.01


def test_a_dark_gateway_ring_covers_the_ring_and_nothing_else(big: ControlRoomEngine) -> None:
    answer = whatif.evaluate(big, whatif.parse("gateway ring 3 dark"))
    ring = [
        d
        for d in big.mine
        if d.is_dispatchable and int(d.device_id.split("-")[1]) % GATEWAY_RING_SIZE == 3
    ]

    assert answer.devices_affected == len(ring)
    assert answer.lost_kw == pytest.approx(sum(d.assigned_kw for d in ring), abs=0.01)


def test_a_price_spike_is_reported_as_upside_and_never_sells_the_reserve(
    big: ControlRoomEngine,
) -> None:
    answer = whatif.evaluate(big, whatif.parse("price spike to $3,000"))

    assert answer.lost_kw == 0.0
    assert "upside, not exposure" in answer.headline
    assert answer.recoverable_kw == pytest.approx(sum(big.spare_kw(d) for d in big.mine), abs=0.05)
    assert answer.reserve_kwh_touched == 0.0
    assert any("backup reserve is not part of it" in step for step in answer.plan)
    # Spare headroom is what is left after the reserve, so it can never be the whole pack.
    assert answer.recoverable_kw < sum(d.power_kw for d in big.mine)


def test_the_console_never_moves_a_kw_or_opens_an_incident(big: ControlRoomEngine) -> None:
    before = [(d.device_id, d.assigned_kw, d.status) for d in big.devices]
    incidents, audit = len(big.incidents), len(big.audit)

    for text in whatif.EXAMPLES:
        whatif.evaluate(big, whatif.parse(text))

    assert [(d.device_id, d.assigned_kw, d.status) for d in big.devices] == before
    assert (len(big.incidents), len(big.audit)) == (incidents, audit)
    for text in whatif.EXAMPLES:
        plan = " ".join(whatif.evaluate(big, whatif.parse(text)).plan).lower()
        assert "a human approves" in plan


def test_the_same_question_asked_twice_gives_the_same_answer(big: ControlRoomEngine) -> None:
    once = whatif.evaluate(big, whatif.parse("20% of LZ_HOUSTON offline at 17:00"))
    twice = whatif.evaluate(big, whatif.parse("20% of LZ_HOUSTON offline at 17:00"))

    assert (once.lost_kw, once.dollars_at_risk, once.plan) == (
        twice.lost_kw,
        twice.dollars_at_risk,
        twice.plan,
    )


@pytest.mark.slow
def test_every_scenario_answers_in_under_two_seconds_at_ten_thousand_devices(
    big: ControlRoomEngine,
) -> None:
    for text in whatif.EXAMPLES:
        started = time.perf_counter()
        answer = whatif.evaluate(big, whatif.parse(text))
        elapsed = time.perf_counter() - started
        assert elapsed < ANSWER_BUDGET_S, f"{text}: {elapsed:.2f}s"
        assert answer.elapsed_ms < ANSWER_BUDGET_S * 1000


def test_the_cli_prints_an_answer_and_refuses_what_it_cannot_read(
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = ["--scenario", "gateway ring 3 dark", "--scenario", "?", "--fleet", "48"]
    assert whatif.main(argv) == 0
    out = capsys.readouterr().out
    assert "Gateway ring 3 dark" in out
    assert "refused:" in out
    assert "Nothing is dispatched" in out
