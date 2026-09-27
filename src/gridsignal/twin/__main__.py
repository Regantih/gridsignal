"""Command line: ``python -m gridsignal.twin {validate,stress,report} [--quick]``."""

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


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m gridsignal.twin")
    ap.add_argument("command", choices=["validate", "stress", "report"])
    ap.add_argument("--quick", action="store_true", help="small run for CI and smoke tests")
    args = ap.parse_args(argv)
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
