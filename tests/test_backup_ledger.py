"""The backup promise: the arithmetic, the walk, and the proof it is not free."""

from __future__ import annotations

import pytest

from gridsignal import backtest, backup_ledger, home
from gridsignal.load import ESSENTIAL_LOAD_KW


@pytest.fixture(scope="module")
def proof() -> backup_ledger.Proof:
    return backup_ledger.prove()


def row(**kwargs: float | str) -> backup_ledger.IntervalRow:
    fields: dict[str, float | str] = {
        "family": backup_ledger.FLEET,
        "source": "normal day",
        "member": "BAT-001",
        "interval": "+0.17 h",
        "promised_kwh": 8.0,
        "held_kwh": 8.0,
        "taken_kwh": 0.0,
    }
    fields.update(kwargs)
    return backup_ledger.IntervalRow(**fields)  # type: ignore[arg-type]


def test_the_promise_is_the_same_share_settlement_and_the_control_room_hold_back() -> None:
    assert backup_ledger.PROMISE_SHARE == backtest.RESERVE_SHARE
    assert backup_ledger.PROMISE_SHARE == home.DEFAULT_RESERVE_FRACTION


def test_an_interval_reads_in_hours_of_essential_load() -> None:
    interval = row(promised_kwh=8.0, held_kwh=4.8)
    assert interval.promised_hours == round(8.0 / ESSENTIAL_LOAD_KW, 2)
    assert interval.held_hours == round(4.8 / ESSENTIAL_LOAD_KW, 2)
    assert interval.kept


def test_only_energy_taken_out_of_the_promise_breaks_it() -> None:
    """A pack that is merely low has not broken anything; delivering from the floor has."""
    assert row(held_kwh=0.0, taken_kwh=0.0).kept
    assert not row(held_kwh=0.0, taken_kwh=0.5).kept


def test_a_run_keeps_the_worst_interval_and_the_total_taken() -> None:
    runs = backup_ledger.collapse(
        [
            row(interval="a", held_kwh=8.0),
            row(interval="b", held_kwh=2.0, taken_kwh=1.5),
            row(interval="c", held_kwh=5.0, taken_kwh=0.5),
            row(member="BAT-002", interval="a"),
        ]
    )
    mine = next(r for r in runs if r.member == "BAT-001")
    assert (mine.intervals, mine.worst_held_kwh, mine.taken_kwh, mine.violations) == (
        3,
        2.0,
        2.0,
        2,
    )
    assert len(runs) == 2


def test_the_market_walk_covers_every_held_out_day_both_packs_and_both_dispatches() -> None:
    rows = backup_ledger.market_rows()
    runs = backup_ledger.collapse(rows)
    assert {r.family for r in rows} == {backup_ledger.MARKET}
    assert len({r.source for r in runs}) == len(runs) // 2  # two pack sizes per source
    assert all(r.intervals == 96 for r in runs)
    assert sum(r.violations for r in runs) == 0


def test_the_market_walk_breaches_the_promise_once_the_floor_is_removed() -> None:
    bare = backup_ledger.collapse(backup_ledger.market_rows(guarded=False))
    assert sum(r.violations for r in bare) > 0
    assert sum(r.taken_kwh for r in bare) > 0


def test_the_grid_event_walk_holds_every_member_above_the_floor() -> None:
    rows = backup_ledger.fleet_rows("scarcity")
    assert rows
    assert all(r.kept for r in rows)
    assert {r.interval for r in rows} == {
        r.interval for r in backup_ledger.fleet_rows("scarcity", guarded=False)
    }


def test_the_grid_event_spends_member_backup_with_the_floor_at_zero() -> None:
    bare = backup_ledger.collapse(backup_ledger.fleet_rows("normal", guarded=False))
    assert sum(r.violations for r in bare) > 0


def test_every_bundled_chaos_scenario_is_audited_and_none_breaches() -> None:
    rows = backup_ledger.chaos_rows()
    sources = {r.source for r in rows}
    assert len(sources) > 3
    assert all(r.kept for r in rows)
    assert any(not r.kept for r in backup_ledger.chaos_rows(guarded=False))


def test_the_ledger_aggregates_every_path_without_losing_an_interval(
    proof: backup_ledger.Proof,
) -> None:
    guarded = proof.guarded
    walked = (
        backup_ledger.market_rows()
        + [r for day in backup_ledger.FLEET_DAYS for r in backup_ledger.fleet_rows(day)]
        + backup_ledger.chaos_rows()
    )
    assert guarded.intervals == len(walked)
    assert {r.family for r in guarded.runs} == {
        backup_ledger.MARKET,
        backup_ledger.FLEET,
        backup_ledger.CHAOS,
    }
    assert guarded.members > 100


def test_the_guard_is_load_bearing_not_true_by_construction(
    proof: backup_ledger.Proof,
) -> None:
    """Zero breaches only counts because the same walk without the floor does breach."""
    assert proof.guarded.violations == 0
    assert proof.guarded.taken_kwh == 0.0
    assert proof.unguarded.violations > 0
    assert proof.unguarded.runs_breached > 0
    assert proof.energy_protected_kwh > 0
    assert proof.caught_violations == proof.unguarded.violations
    for family in (backup_ledger.MARKET, backup_ledger.FLEET, backup_ledger.CHAOS):
        assert proof.guarded.family_violations(family) == 0
        assert proof.unguarded.family_violations(family) > 0


def test_the_counterfactual_walks_the_same_days_and_only_the_awards_move(
    proof: backup_ledger.Proof,
) -> None:
    """Same members, same intervals, except where a free floor changes who gets awarded."""
    for family in (backup_ledger.MARKET, backup_ledger.FLEET):
        guarded = proof.guarded.family(family)
        bare = proof.unguarded.family(family)
        assert sum(r.intervals for r in guarded) == sum(r.intervals for r in bare)
        assert {(r.source, r.member) for r in guarded} == {(r.source, r.member) for r in bare}
    assert {r.source for r in proof.guarded.family(backup_ledger.CHAOS)} == {
        r.source for r in proof.unguarded.family(backup_ledger.CHAOS)
    }


def test_the_focus_member_is_one_the_floor_actually_saves() -> None:
    day, member = backup_ledger.focus_member()
    assert day in backup_ledger.FLEET_DAYS
    held = backup_ledger.member_day(member, day)
    without = backup_ledger.member_day(member, day, guarded=False)
    assert len(held) == len(without) == backup_ledger.EVENT_STEPS
    assert all(r.kept for r in held)
    assert min(r.held_hours for r in without) < min(r.held_hours for r in held)


def test_the_report_is_deterministic_and_says_what_the_walk_found(
    proof: backup_ledger.Proof,
) -> None:
    printed = backup_ledger.report()
    assert printed == backup_ledger.report()
    assert f"{proof.unguarded.violations} breaches" in printed
    assert f"{proof.unguarded.taken_kwh:,.1f} kWh" in printed
    assert "simulated" in printed
    assert "not a Base commitment" in printed


def test_the_cli_prints_the_report(capsys: pytest.CaptureFixture[str]) -> None:
    backup_ledger.main()
    assert "Backup promise ledger" in capsys.readouterr().out
