"""Every number spoken in the demo, recomputed from the code in one command.

``python -m gridsignal.demo_numbers`` prints the figures the script in ``docs/DEMO.md``
quotes on screen, in the order the demo walks them, and then the headline figures the
README and the judging docs quote. ``tests/test_docs.py`` reads both and fails when a
number in the docs no longer appears here, so the prose cannot drift away from the app.

:func:`canonical` names the claims the docs are allowed to make and the single value each
of them may carry, so the same figure cannot be stale in one file and current in another.

Everything below is simulated fleet state settled against bundled historical ERCOT prices.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from gridsignal import holdout, insight, judgment_report, member, pipeline
from gridsignal.control_room import ControlRoomEngine
from gridsignal.control_room.workflow import group_alarms
from gridsignal.fleet import FOCUS_DEVICE_ID
from gridsignal.prices import load_scenario
from gridsignal.rollout import APPROVAL_ABOVE_SHARE, RING_SHARES, load_rollout, run_rollout
from gridsignal.simulate import run_file

DEMO_FLEET = 10_000
LYING_AGENT = "scenarios/lying_agent.yaml"
BAD_BUILD = "scenarios/rollout_bad_build.yaml"


@dataclass(frozen=True)
class Beat:
    """One demo beat and the lines of numbers it puts on screen."""

    title: str
    lines: tuple[str, ...]


def control_room_beat(fleet_size: int = DEMO_FLEET) -> Beat:
    """Scarcity day, Base-scale fleet: what one approval is worth."""
    eng = ControlRoomEngine(price_trace=load_scenario("scarcity"), fleet_size=fleet_size)
    target_kw = eng.grid_event.target_kw
    incident = eng.trigger_device_failure(FOCUS_DEVICE_ID)
    at_risk_pct = eng.snapshot().coverage_pct
    grouped = group_alarms(eng.alarms)
    eng.approve_recovery()
    snap = eng.snapshot()
    return Beat(
        "Beat 1 — Control Room, scarcity day, 10,000 simulated devices",
        (
            f"fleet: {fleet_size:,} devices, {len(eng.mine):,} of them ours to dispatch, "
            f"{target_kw:,.0f} kW committed",
            f"outage: {len(incident.cohort)} devices out, {incident.lost_kw:,.0f} kW lost, "
            f"coverage {at_risk_pct:.0f}% before approval",
            f"dollars: ${incident.dollars_at_risk:,.0f} at risk, "
            f"${incident.dollars_recovered:,.0f} recovered after one approval, "
            f"coverage back to {snap.coverage_pct:.0f}%",
            f"operator workflow: {grouped.alarms:,} raw alarms grouped into "
            f"{grouped.incidents} incident to work",
        ),
    )


def member_beat(fleet_size: int = DEMO_FLEET) -> Beat:
    """The same event from the kitchen: hours of backup kept, and what the home earned."""
    eng = ControlRoomEngine(price_trace=load_scenario("scarcity"), fleet_size=fleet_size)
    eng.trigger_device_failure(FOCUS_DEVICE_ID)
    eng.approve_recovery()
    neighbour = member.member_summary(eng, "BAT-001")
    affected = member.member_summary(eng, FOCUS_DEVICE_ID)
    return Beat(
        "Beat 2 — Member App: the home the fleet is standing on",
        (
            f"neighbour BAT-001, dispatching: {neighbour.backup_hours:,.1f} h of backup kept "
            f"({neighbour.backup_kwh:,.1f} kWh held above the reserve), home taking "
            f"{neighbour.home_load_kw:,.1f} kW before {neighbour.export_kw:,.1f} kW is exported, "
            f"${neighbour.earned_usd:,.2f} earned",
            f"affected home {FOCUS_DEVICE_ID}: {affected.headline}",
        ),
    )


def judgment_beat() -> Beat:
    """Who decides: the deterministic layer, with the model as a second opinion."""
    report_ = judgment_report.build()
    rules = report_.blind[judgment_report.RULES]
    jev = report_.blind[judgment_report.JEV]
    cal = report_.calibration
    moved = round((cal.after - cal.before) * cal.holdout)
    return Beat(
        "Beat 3 — who decides: rules and vetoes, with the model as a second opinion",
        (
            f"blind safety pack, committed before it was scored: rules fallback "
            f"{rules.correct} of {rules.total}, Jev {jev.correct} of {jev.total}",
            f"calibration on a simulated override log: tuning did not meaningfully improve "
            f"held-out agreement, {cal.before:.0%} to {cal.after:.0%}, {moved:+d} episode of "
            f"{cal.holdout}",
        ),
    )


def mesh_beat() -> Beat:
    """The dishonest agent and the build that lies about its own health."""
    run = run_file(LYING_AGENT)
    m = run.metrics
    at_scale = replace(load_rollout(BAD_BUILD), devices=DEMO_FLEET)
    rollout = run_rollout(at_scale, trace=None)
    r = rollout.metrics
    rings = " -> ".join(name if share == 0.0 else f"{share:.0%}" for name, share in RING_SHARES)
    return Beat(
        "Beat 4 — Agent Mesh: the lying agent and the bad build",
        (
            f"rollout rings: {rings} of the fleet, human approval required above "
            f"{APPROVAL_ABOVE_SHARE:.0%}",
            f"lying_agent: {m.agents} agents, {m.rejected_cards} card rejected on signature, "
            f"{m.covered_kw:.0f} of {m.lost_kw:.0f} kW recovered ({m.covered_pct:.0f}%), "
            f"{m.human_approvals} human approvals, {m.auto_approvals} auto-approvals",
            f"rollout bad build: {r.devices:,} devices, halted at {r.halted_ring} on "
            f"{r.failed_gate} after {r.time_to_detect_s}s simulated, "
            f"{r.homes_touched:,} homes touched, {r.homes_affected} affected, "
            f"{r.rolled_back:,} rolled back, {r.reserve_violations} reserve violations",
        ),
    )


def signals_beat() -> Beat:
    """What the public data hides, and how the policy scores on days it never saw."""
    view = insight.summarize(insight.analyze())
    corrected = holdout.summarize(holdout.evaluate())
    first_scored = holdout.summarize(holdout.evaluate(same_interval_price=True))
    return Beat(
        "Beat 5 — Grid Signals: the insight and the held-out days",
        (
            f"insight: on the {view.scarcity_days} bundled scarcity days the day-ahead curve "
            f"exposed only {view.scarcity_visible_share:.0%} of the capturable value; "
            f"${view.scarcity_blind_usd:,.2f} per battery showed up only in real time, worth "
            f"about {view.ordinary_days_equivalent} ordinary trading days; "
            f"{view.divergent_intervals} of {view.intervals:,} intervals printed 5x their "
            f"day-ahead hour",
            f"held out ({corrected.days} days, scored once): {corrected.days_won} of "
            f"{corrected.days} beat the naive schedule, mean "
            f"${corrected.mean_uplift_usd:,.2f}, median ${corrected.median_uplift_usd:,.2f}, "
            f"worst ${corrected.worst_uplift_usd:,.2f} per battery per day",
            f"as first scored (same-interval price): {first_scored.days_won} of "
            f"{first_scored.days} days, mean ${first_scored.mean_uplift_usd:,.2f} — the "
            "corrected run above decides each interval on the last settled print instead",
        ),
    )


def docs_beat(fleet_size: int = DEMO_FLEET) -> Beat:
    """The figures the README, WRITEUP, SUBMISSION and JUDGING_MAP quote."""
    scenario = pipeline.run(scenario="scarcity").summary
    grid_only = holdout.summarize(holdout.evaluate(serve_home=False))
    home_first = holdout.summarize(holdout.evaluate())
    return Beat(
        "Headline numbers quoted in README, WRITEUP, SUBMISSION and JUDGING_MAP",
        (
            f"scenario day: ${scenario.fleet_usd(fleet_size):,.0f}/day across {fleet_size:,} "
            f"batteries on the bundled scarcity day, ${scenario.uplift_usd:,.2f} per battery",
            f"held out, home-first (the product, and the canonical held-out claim): "
            f"{home_first.days_won} of {home_first.days} days, mean "
            f"${home_first.mean_uplift_usd:,.2f}, median ${home_first.median_uplift_usd:,.2f}",
            f"held out, grid-only (comparison only, not the product): {grid_only.days_won} of "
            f"{grid_only.days} days, mean ${grid_only.mean_uplift_usd:,.2f}",
        ),
    )


def canonical() -> dict[str, str]:
    """Claim name -> the one value every doc must quote for it.

    ``tests/test_docs.py`` matches each claim's wording across the docs and fails when a
    file quotes anything else, which is how a number retired by a code change stops
    living on in prose.
    """
    eng = ControlRoomEngine(price_trace=load_scenario("scarcity"), fleet_size=DEMO_FLEET)
    incident = eng.trigger_device_failure(FOCUS_DEVICE_ID)
    eng.approve_recovery()
    scenario = pipeline.run(scenario="scarcity").summary
    home_first = holdout.summarize(holdout.evaluate())
    grid_only = holdout.summarize(holdout.evaluate(serve_home=False))
    view = insight.summarize(insight.analyze())
    return {
        "dollars_at_risk": f"{incident.dollars_at_risk:,.0f}",
        "dollars_recovered": f"{incident.dollars_recovered:,.0f}",
        "scenario_fleet_usd": f"{scenario.fleet_usd(DEMO_FLEET):,.0f}",
        "holdout_days_won": str(home_first.days_won),
        "holdout_mean_usd": f"{home_first.mean_uplift_usd:,.2f}",
        "holdout_median_usd": f"{home_first.median_uplift_usd:,.2f}",
        "grid_only_days_won": str(grid_only.days_won),
        "grid_only_mean_usd": f"{grid_only.mean_uplift_usd:,.2f}",
        "scarcity_visible_share": f"{view.scarcity_visible_share:.0%}",
    }


def beats() -> list[Beat]:
    return [
        control_room_beat(),
        member_beat(),
        judgment_beat(),
        mesh_beat(),
        signals_beat(),
        docs_beat(),
    ]


def report() -> str:
    blocks = []
    for beat in beats():
        blocks.append("\n".join([beat.title] + [f"  {line}" for line in beat.lines]))
    return "\n\n".join(blocks)


def main() -> None:
    print(report())


if __name__ == "__main__":  # pragma: no cover - CLI
    main()
