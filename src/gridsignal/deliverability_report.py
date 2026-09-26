"""How many awards would have been undeliverable without the pre-award check.

Runs every chaos scenario and held-out drill twice — once with the deliverability proof
in :mod:`gridsignal.mesh.deliverability`, once with it switched off — and reports what
the unchecked auction would have committed to batteries that cannot hold it for the
whole award window.

Everything here is simulated fleet behaviour on bundled ERCOT prices.

Reproduce with ``python -m gridsignal.deliverability_report``.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from gridsignal.jev import rules
from gridsignal.jev.client import JevClient
from gridsignal.mesh.scenarios import HOLDOUT_DIR, SCENARIO_DIR, load_scenario

# Rollout and install-wave scenarios drive their own modules, not the auction.
SKIP = ("rollout_bad_build", "rollout_good_build", "install_wave")


@dataclass(frozen=True)
class Row:
    """One scenario, scored with the check and without it."""

    scenario: str
    awards: int
    trimmed: int
    rejected: int
    undeliverable_awards: int
    undeliverable_kw: float
    covered_kw: float
    unchecked_covered_kw: float
    backup_violations: int
    unchecked_backup_violations: int

    @property
    def overstated_kw(self) -> float:
        """Coverage the unchecked auction would have reported but not delivered."""
        return round(self.unchecked_covered_kw - self.covered_kw, 2)


@dataclass(frozen=True)
class Report:
    rows: tuple[Row, ...] = ()

    @property
    def undeliverable_awards(self) -> int:
        return sum(r.undeliverable_awards for r in self.rows)

    @property
    def awards(self) -> int:
        return sum(r.awards for r in self.rows)

    @property
    def undeliverable_kw(self) -> float:
        return round(sum(r.undeliverable_kw for r in self.rows), 2)

    @property
    def trimmed(self) -> int:
        return sum(r.trimmed for r in self.rows)

    @property
    def rejected(self) -> int:
        return sum(r.rejected for r in self.rows)

    @property
    def backup_violations(self) -> int:
        return sum(r.backup_violations for r in self.rows)

    @property
    def unchecked_backup_violations(self) -> int:
        return sum(r.unchecked_backup_violations for r in self.rows)

    @property
    def overstated_kw(self) -> float:
        """Coverage the unchecked auction would have reported but not delivered."""
        return round(sum(r.overstated_kw for r in self.rows), 2)

    @property
    def share_pct(self) -> float:
        if not self.awards:
            return 0.0
        return round(100.0 * self.undeliverable_awards / self.awards, 1)


def scenario_paths() -> list[Path]:
    """Every auction scenario: the tuned five first, then the held-out drills."""
    tuned = sorted(p for p in SCENARIO_DIR.glob("*.yaml") if p.stem not in SKIP)
    return tuned + sorted(HOLDOUT_DIR.glob("*.yaml"))


def run(paths: list[Path] | None = None) -> Report:
    """Score each scenario with the deliverability check on and off."""
    from gridsignal.simulate import run_scenario

    rows: list[Row] = []
    for path in paths if paths is not None else scenario_paths():
        scenario = load_scenario(path)

        def client(slug: str = scenario.slug) -> JevClient:
            return JevClient.for_scenario(slug, fallback=rules.answers)

        checked = run_scenario(scenario, jev=client())
        unchecked = run_scenario(scenario, jev=client(), check_deliverability=False)
        rows.append(
            Row(
                scenario=scenario.slug,
                awards=sum(len(a.awards) for a in unchecked.awards),
                trimmed=checked.metrics.awards_trimmed,
                rejected=checked.metrics.awards_rejected,
                undeliverable_awards=unchecked.metrics.undeliverable_awards,
                undeliverable_kw=unchecked.metrics.undeliverable_kw,
                covered_kw=checked.metrics.covered_kw,
                unchecked_covered_kw=unchecked.metrics.covered_kw,
                backup_violations=checked.metrics.backup_violations,
                unchecked_backup_violations=unchecked.metrics.backup_violations,
            )
        )
    return Report(rows=tuple(rows))


def headline(report: Report) -> str:
    return (
        f"Without the pre-award check, {report.undeliverable_awards} of {report.awards} awards "
        f"({report.share_pct:.0f}%) across {len(report.rows)} simulated scenarios would have "
        f"committed {report.undeliverable_kw:,.2f} kW that the battery could not have held for "
        "the whole award window. The check trimmed "
        f"{report.trimmed} and refused {report.rejected}, with "
        f"{report.backup_violations} member backup reserve violations "
        f"({report.unchecked_backup_violations} unchecked). The unchecked run would have "
        f"reported {report.overstated_kw:,.2f} kW more coverage than it could deliver."
    )


def lines(report: Report) -> list[str]:
    out = [
        "Deliverability check before awards (simulated fleet, bundled ERCOT prices)",
        "",
        f"{'scenario':<32}{'awards':>8}{'undeliverable':>15}{'kW':>10}"
        f"{'trimmed':>9}{'rejected':>10}{'reserve hits':>14}",
    ]
    for row in report.rows:
        out.append(
            f"{row.scenario:<32}{row.awards:>8}{row.undeliverable_awards:>15}"
            f"{row.undeliverable_kw:>10.2f}{row.trimmed:>9}{row.rejected:>10}"
            f"{row.backup_violations:>14}"
        )
    out += ["", headline(report)]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=None, help="also write the rows as JSON")
    args = parser.parse_args(argv)

    report = run()
    print("\n".join(lines(report)))
    if args.json is not None:
        args.json.write_text(
            json.dumps([asdict(r) for r in report.rows], indent=2) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
