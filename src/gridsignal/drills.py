"""Held-out chaos drills: scenarios written after the rules and prompts were frozen.

The five scenarios in ``scenarios/`` were the ones the detection rules and the Jev
questions were written against. The drills in ``scenarios/holdout/`` were written
afterwards, from published lessons about how real grids fail, and scored once without
touching a rule, the recovery logic or a prompt — so this table says how the mesh does
on events nobody designed it for.

Every frequency, outage, load and islanding value in these drills is simulated.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from gridsignal.jev import rules
from gridsignal.jev.client import FIXTURE_DIR, JevClient
from gridsignal.mesh.scenarios import HOLDOUT_DIR, Scenario, load_scenario
from gridsignal.simulate import FFR_DEADLINE_CYCLES, RunMetrics

RULES = "rules-only"
JEV = "jev"
#: With no key and no recorded answers the second column is the rules answering twice.
JEV_FALLBACK = "rules (Jev offline)"


@dataclass(frozen=True)
class DrillRow:
    """One drill under one decision layer."""

    drill: str
    mode: str
    root_cause: str
    truth: str
    correct: bool
    lost_kw: float
    covered_kw: float
    covered_pct: float
    time_to_recover_s: int
    backup_violations: int
    self_deployed_kw: float
    response_cycles: int | None
    min_frequency_hz: float
    islanded_agents: int
    conflicting_cards: int
    escalated: bool
    human_approvals: int
    latency_ms: float
    source: str

    @property
    def within_ffr_deadline(self) -> bool | None:
        if self.response_cycles is None:
            return None
        return self.response_cycles <= FFR_DEADLINE_CYCLES


@dataclass(frozen=True)
class DrillReport:
    rows: tuple[DrillRow, ...] = ()

    def of_mode(self, mode: str) -> list[DrillRow]:
        return [r for r in self.rows if r.mode == mode]

    def accuracy(self, mode: str) -> tuple[int, int]:
        rows = self.of_mode(mode)
        return sum(1 for r in rows if r.correct), len(rows)

    @property
    def backup_violations(self) -> int:
        return sum(r.backup_violations for r in self.rows)

    @property
    def jev_label(self) -> str:
        """What the Jev column is honestly called on this run."""
        rows = self.of_mode(JEV)
        return JEV_FALLBACK if rows and all(r.source == "fallback" for r in rows) else "Jev"


def available_drills(directory: Path = HOLDOUT_DIR) -> list[Path]:
    return sorted(directory.glob("*.yaml"))


def _row(drill: str, mode: str, metrics: RunMetrics) -> DrillRow:
    return DrillRow(
        drill=drill,
        mode=mode,
        root_cause=metrics.root_cause,
        truth=metrics.root_cause_truth,
        correct=metrics.root_cause_correct,
        lost_kw=metrics.lost_kw,
        covered_kw=metrics.covered_kw,
        covered_pct=metrics.covered_pct,
        time_to_recover_s=metrics.time_to_cover_s,
        backup_violations=metrics.backup_violations,
        self_deployed_kw=metrics.self_deployed_kw,
        response_cycles=metrics.response_cycles,
        min_frequency_hz=metrics.min_frequency_hz,
        islanded_agents=metrics.islanded_agents,
        conflicting_cards=metrics.conflicting_cards,
        escalated=metrics.escalated,
        human_approvals=metrics.human_approvals,
        latency_ms=metrics.decision_latency_ms,
        source=metrics.jev_source,
    )


def run(paths: list[Path] | None = None, directory: Path = FIXTURE_DIR) -> DrillReport:
    """Score rules-only and Jev on every held-out drill."""
    from gridsignal.simulate import run_scenario

    files = paths if paths is not None else available_drills()
    rows: list[DrillRow] = []
    for path in files:
        scenario: Scenario = load_scenario(path)
        clients = {
            RULES: JevClient.offline(fallback=rules.answers),
            JEV: JevClient.for_scenario(scenario.slug, directory=directory, fallback=rules.answers),
        }
        for mode, client in clients.items():
            metrics = run_scenario(scenario, jev=client).metrics
            rows.append(_row(scenario.slug, mode, metrics))
    return DrillReport(rows=tuple(rows))


def _cycles(row: DrillRow) -> str:
    if row.response_cycles is None:
        return "n/a"
    verdict = "within" if row.within_ffr_deadline else "over"
    return f"{row.response_cycles:,} ({verdict} {FFR_DEADLINE_CYCLES})"


def markdown(report: DrillReport) -> str:
    """The held-out table that sits next to the tuned-five table."""
    correct_rules, total = report.accuracy(RULES)
    correct_jev, _ = report.accuracy(JEV)
    label = report.jev_label
    lines = [
        "| Decision layer | Root-cause accuracy on held-out drills |",
        "| --- | --- |",
        f"| rules-only | {correct_rules}/{total} |",
        f"| {label} | {correct_jev}/{total} |",
        "",
        f"| Drill | Injected root cause | rules-only | {label} | kW recovered | "
        "Time to recover | Backup reserve violations | Self-deployed locally | "
        "Response (cycles) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for drill in sorted({r.drill for r in report.rows}):
        by_mode = {r.mode: r for r in report.rows if r.drill == drill}
        jev_row = by_mode[JEV]
        cells = []
        for mode in (RULES, JEV):
            row = by_mode[mode]
            cells.append(f"{row.root_cause} {'✓' if row.correct else '✗'}")
        lines.append(
            f"| {drill} | {jev_row.truth} | {cells[0]} | {cells[1]} | "
            f"{jev_row.covered_kw:,.0f} of {jev_row.lost_kw:,.0f} kW "
            f"({jev_row.covered_pct:.0f}%) | {jev_row.time_to_recover_s}s | "
            f"{jev_row.backup_violations} | "
            f"{jev_row.self_deployed_kw:,.0f} kW | {_cycles(jev_row)} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score rules-only against Jev on held-out drills.")
    parser.add_argument("--json", type=Path, default=None, help="also write the rows as JSON")
    args = parser.parse_args(argv)

    report = run()
    print(markdown(report))
    if args.json is not None:
        args.json.write_text(
            json.dumps([asdict(r) for r in report.rows], indent=2) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
