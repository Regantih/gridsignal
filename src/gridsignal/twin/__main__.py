"""Command line for the twin.

python -m gridsignal.twin validate|stress|report [--quick]   # the frozen study
python -m gridsignal.twin feed [--watch]                     # live ERCOT prices
python -m gridsignal.twin learn [--file history.jsonl]       # measured failure rates
python -m gridsignal.twin risk [--fleet 1000]                # who fails together
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict

from gridsignal.paths import ROOT
from gridsignal.twin import stress
from gridsignal.twin.data import TWIN_DIR, load_days
from gridsignal.twin.planner import SCENARIOS
from gridsignal.twin.sim import POLICIES, FleetAssumptions
from gridsignal.twin.validate import reproduce, rolling_origin
from gridsignal.twin.world import PriceWorldModel

REPORTS = TWIN_DIR
DOCS = ROOT / "docs" / "twin"


def cmd_validate(quick: bool) -> dict:
    days = load_days()
    sims = 20 if quick else 80
    configs = {
        "all years, latest regime": {"regime": "latest", "level_risk": True},
        "trailing year": {"expanding": False, "regime": None, "level_risk": True},
    }
    out = {}
    for name, kw in configs.items():
        rows = rolling_origin(days, sims=sims, **kw)
        out[name] = {
            "rows": rows,
            "world_covered": int(sum(r["world_covers"] for r in rows)),
            "bootstrap_covered": int(sum(r["boot_covers"] for r in rows)),
            "checks": len(rows),
        }
    rep = reproduce(days, sims=sims)
    out["reproduce"] = {
        "rows": rep,
        "covered": int(sum(r["covers"] for r in rep)),
        "checks": len(rep),
    }
    model = PriceWorldModel().fit(days)
    out["model"] = {
        "explained_variance": model.explained_,
        "components": model.components,
        "clusters": model.clusters,
        "level_sigma": model.level_sigma_,
        "train_days": len(days),
        "first": str(days.dates.min().date()),
        "last": str(days.dates.max().date()),
    }
    return out


def cmd_stress(quick: bool) -> dict:
    days = load_days()
    model = PriceWorldModel().fit(days)
    years = 3 if quick else 30
    a = FleetAssumptions()
    out = {"assumptions": asdict(a), "years_per_scenario": years, "scenarios": {}}
    for name, kw in SCENARIOS.items():
        t = time.time()
        res = stress.run(model, days, years=years, assumptions=a, world_kw=kw)
        out["scenarios"][name] = {
            "results": [r.row() for r in res],
            "safe_ratio": {p: stress.max_safe_ratio(res, p) for p in POLICIES},
            "seconds": round(time.time() - t, 1),
        }
    real = days.between("2025-01-01", "2025-12-31")
    res = stress.run(model, days.between("2021-01-01", "2024-12-31"), assumptions=a, real=real)
    out["scenarios"]["Real 2025 days (reality check)"] = {
        "results": [r.row() for r in res],
        "safe_ratio": {p: stress.max_safe_ratio(res, p) for p in POLICIES},
    }
    out["sensitivity"] = stress.sensitivity(
        model, days, a, years=2 if quick else 8, world_kw={"regime": 2025}
    )
    return out


def cmd_feed(watch_: bool, every: int) -> None:
    from gridsignal.twin import feed

    if watch_:
        feed.watch(every)
        return
    s = feed.refresh()
    print(
        f"live ERCOT store: {s.days_in_store} days ({s.first_day} to {s.last_full_day} settled), "
        f"latest interval ending {s.latest_interval}, {s.intervals_today} intervals today, "
        f"{s.days_fetched} page(s) fetched, {s.revised_intervals} revised"
    )
    for err in s.errors:
        print(f"  could not load {err}")


def _engine(fleet: int, playbook: bool = False):
    from gridsignal.control_room import ControlRoomEngine

    eng = ControlRoomEngine(fleet_size=fleet)
    if playbook:
        eng.approve_playbook("M. Alvarez (Fleet Operator)")
    return eng


def cmd_learn(path: str | None, fleet: int, days: int, quick: bool) -> None:
    from gridsignal.twin import learn, planner, risk

    eng = _engine(fleet)
    groups = risk.device_groups(eng)
    feeders = {k: v["feeder"] for k, v in groups.items()}
    if path:
        with open(path, encoding="utf-8") as fh:
            frame, rejected = learn.frame_from_lines(fh)
        source = path
    else:
        lines = learn.synthetic_history(
            list(eng.mine),
            days=days,
            feeders=feeders,
            ring_of={k: v["ring"] for k, v in groups.items()},
        )
        frame, rejected = learn.frame_from_lines(lines)
        source = f"SYNTHETIC {days}-day history (known rates: {learn.SyntheticTruth()})"
    cal = learn.learn(frame, {d.device_id: d.zone for d in eng.mine}, feeders, source=source)
    print(f"learned from {source}")
    print(
        f"  {cal.reports:,} reports ({rejected:,} rejected by the importer), {cal.devices:,} "
        f"devices, {cal.days} days, {cal.failures:,} drops, {len(cal.clusters)} correlated"
    )
    for e in cal.estimates.values():
        print(
            f"  {e.label}: assumed {e.prior:.4g}, learned {e.learned:.4g} "
            f"(90% {e.low:.4g} to {e.high:.4g}); {e.evidence}"
        )
    for n in cal.notes:
        print(f"  note: {n}")
    years = 2 if quick else 4
    before = planner.plan_commitment(eng, years=years)
    after = planner.plan_commitment(eng, years=years, calibration=cal)
    print(
        f"  safe commitment with the playbook: {before.recommended_ratio:.0%} assumed, "
        f"{after.recommended_ratio:.0%} learned ({years} simulated years each)"
    )


def cmd_risk(fleet: int) -> None:
    from gridsignal.twin import risk

    eng = _engine(fleet, playbook=True)
    groups = risk.groups(eng)
    print(f"{fleet:,}-device fleet, playbook approved: {risk.summary(groups)}")
    for g in groups[:12]:
        print(
            f"  {g.status:<22} {g.kind:<12} {g.key:<14} {len(g.devices):>5} devices "
            f"{g.lost_kw:>9,.1f} kW ({g.worst_zone_share:.0%} of its zone), "
            f"uncovered {g.uncovered_kw:,.1f} kW"
        )


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m gridsignal.twin")
    ap.add_argument("command", choices=["validate", "stress", "report", "feed", "learn", "risk"])
    ap.add_argument("--quick", action="store_true", help="small run for CI and smoke tests")
    ap.add_argument("--watch", action="store_true", help="feed: refresh every --every seconds")
    ap.add_argument("--every", type=int, default=900, help="feed --watch interval, seconds")
    ap.add_argument("--file", help="learn: telemetry history, JSON lines")
    ap.add_argument("--fleet", type=int, default=1_000, help="learn/risk: simulated fleet size")
    ap.add_argument("--days", type=int, default=90, help="learn: synthetic history length")
    args = ap.parse_args(argv)
    if args.command == "feed":
        cmd_feed(args.watch, args.every)
        return
    if args.command == "learn":
        cmd_learn(args.file, args.fleet, 30 if args.quick else args.days, args.quick)
        return
    if args.command == "risk":
        cmd_risk(args.fleet)
        return
    REPORTS.mkdir(exist_ok=True)
    if args.command in ("validate", "report"):
        v = cmd_validate(args.quick)
        (REPORTS / "validation.json").write_text(json.dumps(v, indent=1, default=float))
        for k in ("trailing year", "all years, latest regime"):
            print(
                f"validate[{k}]: next-year range covers {v[k]['world_covered']}/{v[k]['checks']}, "
                f"bootstrap {v[k]['bootstrap_covered']}/{v[k]['checks']}"
            )
        r = v["reproduce"]
        print(f"reproduce: imitation of each year covers {r['covered']}/{r['checks']}")
    if args.command in ("stress", "report"):
        s = cmd_stress(args.quick)
        (REPORTS / "stress.json").write_text(json.dumps(s, indent=1, default=float))
        for name, sc in s["scenarios"].items():
            print(f"stress[{name}]: safe commit ratio at 99% kept days {sc['safe_ratio']}")
    if args.command == "report":
        from gridsignal.twin.report import write_report

        print(f"wrote {write_report(REPORTS, DOCS)}")


if __name__ == "__main__":
    main()
