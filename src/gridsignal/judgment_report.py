"""Score the safety question pack v2 and the operator judgment model, in one command.

    python -m gridsignal.judgment_report

Three things come out of it, in the order they were actually done:

1. **Blind score.** The question pack (``src/gridsignal/jev/principles.yaml``) and the
   answer key (``data/holdout_safety_labels.yaml``) were committed before either layer
   answered a single question. Both are then scored on the held-out drills, per
   principle, against that key.
2. **Rules versus Jev, and who was right.** Every question where the two layers disagree
   is listed with the key's answer next to it, so a disagreement is settled rather than
   noted.
3. **Calibration on simulated overrides.** A simulated operator's decisions
   (:mod:`gridsignal.jev.overrides`) are split in half; the soft-principle weights and
   the two certainty bars are fitted on one half and agreement is reported on the other,
   before and after. The override log is simulated and labelled as such everywhere.

Offline and keyless: answers come from the recorded fixtures in ``data/jev_fixtures``,
and with neither key nor fixture the rules fallback answers with zero confidence.
"""

from __future__ import annotations

import argparse
import json
import threading
from dataclasses import dataclass
from pathlib import Path

import yaml

from gridsignal import paths
from gridsignal.jev import judgment, overrides
from gridsignal.jev.client import FIXTURE_DIR, JevClient, JevResponse, Source
from gridsignal.jev.judgment import (
    PACK,
    TUNED_PATH,
    Calibration,
    Situation,
    Verdict,
    judge,
)
from gridsignal.mesh.negotiation import BACKUP_RESERVE_KWH, reserve_floor_kwh
from gridsignal.mesh.scenarios import HOLDOUT_DIR, load_scenario

LABELS_PATH = paths.DATA_DIR / "holdout_safety_labels.yaml"
HOLDOUT_FIXTURE = "judgment_holdout"
OVERRIDE_FIXTURE = "judgment_overrides"
RULES = "rules fallback"
JEV = "Jev"


def _client(name: str, directory: Path, offline: bool) -> JevClient:
    if offline:
        return JevClient.offline(fallback=judgment.rules_answers)
    return JevClient.for_scenario(name, directory=directory, fallback=judgment.rules_answers)


def ask(client: JevClient, situation: Situation) -> JevResponse:
    response = client.ask(situation.as_state(), PACK.questions())
    client.flush()
    return response


# ----------------------------------------------------------------- blind scoring


@dataclass(frozen=True)
class Labels:
    answers: dict[str, dict[str, bool]]
    why: dict[str, str]

    def of(self, drill: str) -> dict[str, bool]:
        return self.answers.get(drill, {})


def load_labels(path: Path = LABELS_PATH) -> Labels:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    drills = dict(raw.get("drills") or {})
    return Labels(
        answers={
            name: {str(k): bool(v) for k, v in dict(body.get("answers") or {}).items()}
            for name, body in drills.items()
        },
        why={name: str(body.get("why", "")).strip() for name, body in drills.items()},
    )


@dataclass(frozen=True)
class DrillAnswers:
    drill: str
    situation: Situation
    responses: dict[str, JevResponse]

    def answer(self, mode: str, principle_id: str) -> bool:
        answer = self.responses[mode].answer(f"{judgment.PREFIX}{principle_id}")
        return bool(answer and answer.yes)

    def probability(self, mode: str, principle_id: str) -> float:
        answer = self.responses[mode].answer(f"{judgment.PREFIX}{principle_id}")
        if answer is None:
            return 0.0
        return float(answer.probabilities.get("yes", 1.0 if answer.yes else 0.0))


def drill_situation(drill_path: Path) -> Situation:
    """Build the step the mesh was about to take in one held-out drill."""
    from gridsignal.jev import rules
    from gridsignal.simulate import run_scenario

    scenario = load_scenario(drill_path)
    result = run_scenario(scenario, jev=JevClient.offline(fallback=rules.answers))
    m = result.metrics
    hours = max(scenario.duration_s / 3600.0, 1e-6)
    price = (m.dollars_at_risk / (m.lost_kw / 1000.0 * hours)) if m.lost_kw > 0 else 0.0
    reserve = BACKUP_RESERVE_KWH
    return Situation(
        label=scenario.slug,
        dollars=m.dollars_at_risk,
        committed_kw=m.covered_kw,
        uncovered_kw=round(max(m.lost_kw - m.covered_kw, 0.0), 2),
        spare_kwh_above_reserve=_spare_above_reserve(result, hours, reserve),
        backup_reserve_kwh=reserve,
        mean_soc=_mean_soc(result),
        bidders=len(result.awards[-1].awards) if result.awards else 0,
        unverified_bidders=m.rejected_cards + m.conflicting_cards,
        undeliverable_kw=m.undeliverable_kw,
        frequency_hz=m.min_frequency_hz,
        islanded_homes=m.islanded_agents,
        price_usd_mwh=round(price, 2),
        reversible=True,
        root_cause_confidence=m.root_cause_confidence,
    )


def _spare_above_reserve(result, hours: float, reserve: float) -> float:  # type: ignore[no-untyped-def] # noqa: E501
    """Thinnest margin over a member's backup reserve once the award window has run.

    Negative means the plan would spend energy the member is holding for backup.
    """
    if not result.awards:
        return reserve
    statuses = result.registry.statuses()
    spare = []
    for award in result.awards[-1].awards:
        if award.agent_id not in statuses:
            continue
        card = result.registry.card(award.agent_id)
        spare.append(card.capability("kwh_available") - award.kw * hours - reserve_floor_kwh(card))
    return round(min(spare), 3) if spare else reserve


def _mean_soc(result) -> float:  # type: ignore[no-untyped-def]
    if not result.awards or not result.awards[-1].awards:
        return 1.0
    socs = [result.registry.card(a.agent_id).capability("soc") for a in result.awards[-1].awards]
    return round(sum(socs) / len(socs), 4)


def score_drills(
    directory: Path = FIXTURE_DIR, drills: list[Path] | None = None
) -> list[DrillAnswers]:
    """Ask both layers the committed pack about every held-out drill."""
    files = drills if drills is not None else sorted(HOLDOUT_DIR.glob("*.yaml"))
    out: list[DrillAnswers] = []
    for path in files:
        situation = drill_situation(path)
        responses = {
            RULES: ask(_client(HOLDOUT_FIXTURE, directory, offline=True), situation),
            JEV: ask(_client(HOLDOUT_FIXTURE, directory, offline=False), situation),
        }
        out.append(DrillAnswers(drill=situation.label, situation=situation, responses=responses))
    return out


@dataclass(frozen=True)
class BlindScore:
    mode: str
    correct: int
    total: int

    @property
    def share(self) -> float:
        return self.correct / self.total if self.total else 0.0


def blind_scores(answers: list[DrillAnswers], labels: Labels) -> dict[str, BlindScore]:
    scores: dict[str, BlindScore] = {}
    for mode in (RULES, JEV):
        correct = total = 0
        for drill in answers:
            for principle_id, expected in labels.of(drill.drill).items():
                total += 1
                correct += int(drill.answer(mode, principle_id) == expected)
        scores[mode] = BlindScore(mode=mode, correct=correct, total=total)
    return scores


@dataclass(frozen=True)
class Disagreement:
    drill: str
    principle: str
    rules: bool
    jev: bool
    truth: bool

    @property
    def winner(self) -> str:
        if self.jev == self.truth and self.rules != self.truth:
            return JEV
        if self.rules == self.truth and self.jev != self.truth:
            return RULES
        return "neither"


def disagreements(answers: list[DrillAnswers], labels: Labels) -> list[Disagreement]:
    rows: list[Disagreement] = []
    for drill in answers:
        for principle_id, expected in labels.of(drill.drill).items():
            rules_answer = drill.answer(RULES, principle_id)
            jev_answer = drill.answer(JEV, principle_id)
            if rules_answer != jev_answer:
                rows.append(
                    Disagreement(
                        drill=drill.drill,
                        principle=principle_id,
                        rules=rules_answer,
                        jev=jev_answer,
                        truth=expected,
                    )
                )
    return rows


# ------------------------------------------------------- calibration on overrides


@dataclass(frozen=True)
class CalibrationResult:
    episodes: int
    train: int
    holdout: int
    before: float
    after: float
    train_before: float
    train_after: float
    calibration: Calibration
    overridden: int


def calibration_result(directory: Path = FIXTURE_DIR) -> CalibrationResult:
    log = overrides.episodes()
    client = _client(OVERRIDE_FIXTURE, directory, offline=False)
    answers = {episode.episode_id: ask(client, episode.situation) for episode in log}
    train, holdout = overrides.split(log)
    before = PACK.calibration
    after = overrides.calibrate(train, answers, start=before)
    return CalibrationResult(
        episodes=len(log),
        train=len(train),
        holdout=len(holdout),
        before=overrides.agreement(holdout, answers, before),
        after=overrides.agreement(holdout, answers, after),
        train_before=overrides.agreement(train, answers, before),
        train_after=overrides.agreement(train, answers, after),
        calibration=after,
        overridden=sum(1 for e in log if e.overridden),
    )


# ------------------------------------------------------------------- reporting


@dataclass(frozen=True)
class Report:
    drills: tuple[DrillAnswers, ...]
    labels: Labels
    blind: dict[str, BlindScore]
    disagreements: tuple[Disagreement, ...]
    calibration: CalibrationResult
    verdicts_before: dict[str, Verdict]
    verdicts_after: dict[str, Verdict]

    @property
    def jev_live(self) -> bool:
        return any(d.responses[JEV].source is not Source.FALLBACK for d in self.drills)


def build(directory: Path = FIXTURE_DIR, drills: list[Path] | None = None) -> Report:
    labels = load_labels()
    answers = score_drills(directory=directory, drills=drills)
    result = calibration_result(directory=directory)
    before = {
        d.drill: judge(d.responses[JEV], d.situation, calibration=PACK.calibration) for d in answers
    }
    after = {
        d.drill: judge(d.responses[JEV], d.situation, calibration=result.calibration)
        for d in answers
    }
    return Report(
        drills=tuple(answers),
        labels=labels,
        blind=blind_scores(answers, labels),
        disagreements=tuple(disagreements(answers, labels)),
        calibration=result,
        verdicts_before=before,
        verdicts_after=after,
    )


_CACHED: dict[str, Report] = {}
_CACHE_LOCK = threading.Lock()


def cached_build() -> Report:
    """``build()`` on the committed fixtures, computed once per process.

    Scoring the pack replays every drill, which is a second or two of work. The screen
    quotes the blind score on every incident, so the first fault would otherwise pay for
    it while an operator waits; the lock lets a pre-warm thread do it before the click.
    """
    with _CACHE_LOCK:
        if "report" not in _CACHED:
            _CACHED["report"] = build()
        return _CACHED["report"]


def save_calibration(report: Report, path: Path = TUNED_PATH) -> Path:
    """Record the fitted weights and bars so the Control Room decides with them."""
    payload = {
        "note": (
            "Soft-principle weights and certainty bars fitted on a SIMULATED operator "
            "override log; not calibrated on real Base operators. Reproduce with "
            "python -m gridsignal.judgment_report."
        ),
        "episodes": report.calibration.episodes,
        "fitted_on": report.calibration.train,
        "held_out": report.calibration.holdout,
        "agreement_held_out_before": round(report.calibration.before, 4),
        "agreement_held_out_after": round(report.calibration.after, 4),
        "calibration": report.calibration.calibration.as_dict(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def markdown(report: Report) -> str:
    """The tables the README and the Control Room show."""
    lines: list[str] = []
    source = "recorded Jev answers" if report.jev_live else "rules fallback (no key, no fixture)"
    lines.append(
        f"**Blind score on held-out drills** — pack and answer key committed before the "
        f"first run; answers from {source}."
    )
    lines.append("")
    lines.append("| Decision layer | Safety questions right (blind) |")
    lines.append("| --- | --- |")
    for mode in (RULES, JEV):
        score = report.blind[mode]
        lines.append(f"| {mode} | {score.correct}/{score.total} ({score.share:.0%}) |")
    lines.append("")

    lines.append("| Drill | Question | rules fallback | Jev | Answer key | Who was right |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    if not report.disagreements:
        lines.append("| — | the two layers agreed on every question | | | | |")
    for row in report.disagreements:
        lines.append(
            f"| {row.drill} | {PACK.by_id(row.principle).title} | "
            f"{'yes' if row.rules else 'no'} | {'yes' if row.jev else 'no'} | "
            f"{'yes' if row.truth else 'no'} | {row.winner} |"
        )
    lines.append("")

    lines.append("| Drill | Verdict (as committed) | Verdict (after tuning) | Reason |")
    lines.append("| --- | --- | --- | --- |")
    for drill in report.drills:
        before = report.verdicts_before[drill.drill]
        after = report.verdicts_after[drill.drill]
        lines.append(
            f"| {drill.drill} | {before.action.value} | {after.action.value} | {after.reason} |"
        )
    lines.append("")

    cal = report.calibration
    lines.append(
        f"**Calibration on {cal.episodes} simulated operator episodes** "
        f"({cal.overridden} of them overridden by the simulated operator), "
        f"{cal.train} fitted / {cal.holdout} held out. Simulated overrides, not real ones."
    )
    lines.append("")
    lines.append("| Agreement with the simulated operator | Fitted half | Held-out half |")
    lines.append("| --- | --- | --- |")
    lines.append(f"| as committed | {cal.train_before:.0%} | {cal.before:.0%} |")
    lines.append(f"| after tuning | {cal.train_after:.0%} | {cal.after:.0%} |")
    lines.append("")
    weights = ", ".join(
        f"{PACK.by_id(k).title.lower()} {v:.2f}" for k, v in sorted(cal.calibration.weights.items())
    )
    lines.append(
        f"Tuned soft weights: {weights}; act bar {cal.calibration.act_threshold:.2f}, "
        f"act-and-notify bar {cal.calibration.notify_threshold:.2f} "
        f"(both rise by {cal.calibration.money_slope:.2f} as the money at stake "
        f"approaches ${cal.calibration.dollar_scale:,.0f})."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score the safety pack blind and calibrate the judgment model."
    )
    parser.add_argument("--json", type=Path, default=None, help="also write the report as JSON")
    parser.add_argument(
        "--no-save-calibration",
        action="store_true",
        help=f"do not write the tuned weights and bars to {TUNED_PATH.name}",
    )
    args = parser.parse_args(argv)

    report = build()
    print(markdown(report))
    if not args.no_save_calibration:
        save_calibration(report)
    if args.json is not None:
        payload = {
            "blind": {m: s.__dict__ for m, s in report.blind.items()},
            "disagreements": [d.__dict__ for d in report.disagreements],
            "calibration": {
                **{k: v for k, v in report.calibration.__dict__.items() if k != "calibration"},
                "tuned": report.calibration.calibration.as_dict(),
            },
            "verdicts": {
                drill: {
                    "as_committed": report.verdicts_before[drill].as_dict(),
                    "after_tuning": report.verdicts_after[drill].as_dict(),
                }
                for drill in report.verdicts_before
            },
        }
        args.json.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
