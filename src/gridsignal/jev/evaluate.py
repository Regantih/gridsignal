"""Rules-only versus Jev over every bundled chaos scenario.

Each scenario declares (or implies) the root cause that was actually injected, so the
decision layer can be scored rather than demonstrated: root-cause accuracy, how often a
human was still asked to approve, and the median decision latency.

Runs offline from the recorded fixtures, so the numbers in the README and the dashboard
are reproducible without a key.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, replace
from pathlib import Path

from gridsignal.jev import rules
from gridsignal.jev.client import FIXTURE_DIR, JevClient, Source
from gridsignal.mesh.scenarios import available_scenarios, load_scenario

RULES = "rules-only"
JEV = "jev"
#: What the second column is really called when there is no key and no recorded answer:
#: the deterministic rules answered, so calling the row "jev" would overstate it.
JEV_FALLBACK = "jev (rules fallback)"


@dataclass(frozen=True)
class EvalRow:
    scenario: str
    mode: str
    root_cause: str
    truth: str
    correct: bool
    human_approvals: int
    auto_approvals: int
    latency_ms: float
    source: str


@dataclass(frozen=True)
class ModeSummary:
    mode: str
    scenarios: int
    correct: int
    human_approvals: int
    auto_approvals: int
    median_latency_ms: float

    @property
    def accuracy(self) -> float:
        return self.correct / self.scenarios if self.scenarios else 0.0


@dataclass(frozen=True)
class EvalReport:
    rows: tuple[EvalRow, ...] = ()
    summaries: tuple[ModeSummary, ...] = ()
    fixtures: int = 0

    def summary(self, mode: str) -> ModeSummary | None:
        return next((s for s in self.summaries if s.mode == mode), None)


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[middle], 1)
    return round(0.5 * (ordered[middle - 1] + ordered[middle]), 1)


def _summarise(mode: str, rows: list[EvalRow]) -> ModeSummary:
    return ModeSummary(
        mode=mode,
        scenarios=len(rows),
        correct=sum(1 for r in rows if r.correct),
        human_approvals=sum(r.human_approvals for r in rows),
        auto_approvals=sum(r.auto_approvals for r in rows),
        median_latency_ms=_median([r.latency_ms for r in rows]),
    )


def evaluate(paths: list[Path] | None = None, directory: Path = FIXTURE_DIR) -> EvalReport:
    """Score both decision layers on every scenario."""
    from gridsignal.simulate import run_scenario

    files = paths if paths is not None else available_scenarios()
    rows: list[EvalRow] = []
    by_mode: dict[str, list[EvalRow]] = {RULES: [], JEV: []}
    fixtures = 0
    for path in files:
        scenario = load_scenario(path)
        clients = {
            RULES: JevClient.offline(fallback=rules.answers),
            JEV: JevClient.for_scenario(scenario.slug, directory=directory, fallback=rules.answers),
        }
        fixtures += len(clients[JEV].fixtures or [])
        for mode, client in clients.items():
            metrics = run_scenario(scenario, jev=client).metrics
            row = EvalRow(
                scenario=scenario.slug,
                mode=mode,
                root_cause=metrics.root_cause,
                truth=metrics.root_cause_truth,
                correct=metrics.root_cause_correct,
                human_approvals=metrics.human_approvals,
                auto_approvals=metrics.auto_approvals,
                latency_ms=metrics.decision_latency_ms,
                source=metrics.jev_source,
            )
            rows.append(row)
            by_mode[mode].append(row)

    # With no key and no fixtures every "Jev" answer came from the rules fallback; say so
    # rather than crediting the model for an answer it never gave.
    jev_rows = by_mode[JEV]
    if jev_rows and all(r.source == Source.FALLBACK.value for r in jev_rows):
        del by_mode[JEV]
        rows = [replace(r, mode=JEV_FALLBACK) if r.mode == JEV else r for r in rows]
        by_mode[JEV_FALLBACK] = [replace(r, mode=JEV_FALLBACK) for r in jev_rows]

    return EvalReport(
        rows=tuple(rows),
        summaries=tuple(_summarise(mode, mode_rows) for mode, mode_rows in by_mode.items()),
        fixtures=fixtures,
    )


def markdown(report: EvalReport) -> str:
    """The table that goes in the README and the Agent Mesh view."""
    jev_mode = JEV_FALLBACK if any(r.mode == JEV_FALLBACK for r in report.rows) else JEV
    lines = [
        "| Decision layer | Root-cause accuracy | Human approvals | Auto-approvals | "
        "Median decision latency |",
        "| --- | --- | --- | --- | --- |",
    ]
    for summary in report.summaries:
        lines.append(
            f"| {summary.mode} | {summary.correct}/{summary.scenarios} "
            f"({summary.accuracy:.0%}) | {summary.human_approvals} | "
            f"{summary.auto_approvals} | {summary.median_latency_ms:.0f} ms |"
        )
    lines.append("")
    lines.append(f"| Scenario | Injected root cause | {RULES} | {jev_mode} |")
    lines.append("| --- | --- | --- | --- |")
    scenarios = sorted({r.scenario for r in report.rows})
    for scenario in scenarios:
        rows = {r.mode: r for r in report.rows if r.scenario == scenario}
        truth = rows[jev_mode].truth
        cells = []
        for mode in (RULES, jev_mode):
            row = rows[mode]
            mark = "✓" if row.correct else "✗"
            cells.append(f"{row.root_cause} {mark}")
        lines.append(f"| {scenario} | {truth} | {cells[0]} | {cells[1]} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score rules-only against Jev on all scenarios.")
    parser.add_argument("--json", type=Path, default=None, help="also write the rows as JSON")
    args = parser.parse_args(argv)

    report = evaluate()
    print(markdown(report))
    if args.json is not None:
        args.json.write_text(
            json.dumps([r.__dict__ for r in report.rows], indent=2) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
