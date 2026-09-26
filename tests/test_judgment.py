"""The operator judgment model: six principles, one yes/no question each.

Every test here runs with no key and no network: answers come either from the recorded
fixtures or from the rules fallback, which replies with zero confidence.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
import yaml

from gridsignal import judgment_report
from gridsignal.control_room.engine import ControlRoomEngine
from gridsignal.jev import judgment, overrides
from gridsignal.jev.client import JevAnswer, JevClient, JevResponse, QuestionKind, Source
from gridsignal.jev.incident import judge_incident
from gridsignal.jev.judgment import (
    PACK,
    Action,
    PrincipleType,
    Situation,
    judge,
    rules_answers,
    situation_from_state,
)

#: Scores the whole committed pack twice: the slow half of the suite.
pytestmark = pytest.mark.slow

PRIORITY_ORDER = (
    "protect_backup",
    "market_rules",
    "deliverable",
    "reversible",
    "certainty_for_money",
    "ask_when_in_doubt",
)
VETOES = ("protect_backup", "market_rules", "deliverable")


def clean_situation(**overrides_: object) -> Situation:
    """A step that satisfies every principle, before the test spoils one of them."""
    base = Situation(
        label="clean step",
        dollars=120.0,
        committed_kw=400.0,
        uncovered_kw=0.0,
        spare_kwh_above_reserve=6.0,
        backup_reserve_kwh=4.0,
        mean_soc=0.72,
        bidders=30,
        price_usd_mwh=48.0,
        root_cause_confidence=0.9,
    )
    return replace(base, **overrides_)  # type: ignore[arg-type]


def answer(question_id: str, yes_probability: float, confidence: float = 0.8) -> JevAnswer:
    return JevAnswer(
        question_id=question_id,
        kind=QuestionKind.NOUL,
        confidence=confidence,
        probabilities={"yes": yes_probability, "no": 1.0 - yes_probability},
        yes=yes_probability >= 0.5,
    )


def response(source: Source = Source.FIXTURE, **probabilities: float) -> JevResponse:
    """A Jev response that answers every principle, defaulting to a confident yes."""
    return JevResponse(
        answers={
            p.question_id: answer(p.question_id, probabilities.get(p.id, 0.95))
            for p in PACK.principles
        },
        model="test",
        source=source,
        latency_ms=1.0,
    )


def fallback(situation: Situation) -> JevResponse:
    client = JevClient.offline(fallback=judgment.rules_answers)
    return client.ask(situation.as_state(), PACK.questions())


# ------------------------------------------------------------------ the committed pack


def test_pack_lists_the_six_principles_in_priority_order() -> None:
    assert tuple(p.id for p in PACK.principles) == PRIORITY_ORDER
    assert [p.priority for p in PACK.principles] == [1, 2, 3, 4, 5, 6]


def test_each_principle_is_a_hard_veto_or_a_weighted_soft_principle() -> None:
    for principle in PACK.principles:
        if principle.id in VETOES:
            assert principle.type is PrincipleType.VETO
            assert principle.weight == 0.0
        else:
            assert principle.type is PrincipleType.SOFT
            assert principle.weight > 0.0


def test_the_pack_on_disk_is_the_pack_that_was_committed() -> None:
    raw = yaml.safe_load(judgment.PRINCIPLES_PATH.read_text(encoding="utf-8"))
    assert raw["committed_before_scoring"] is True
    assert [item["id"] for item in raw["principles"]] == list(PRIORITY_ORDER)


def test_one_yes_no_question_per_principle() -> None:
    questions = PACK.questions()
    assert len(questions) == len(PACK.principles)
    for principle in PACK.principles:
        question = questions[principle.question_id]
        assert question.kind is QuestionKind.NOUL
        assert question.instructions.strip().endswith("?")


# ----------------------------------------------------------------- the rules fallback


def test_the_rules_fallback_answers_every_question_with_zero_confidence() -> None:
    situation = clean_situation()
    replies = rules_answers(situation.as_state(), PACK.questions())
    assert set(replies) == set(PACK.questions())
    assert {a.confidence for a in replies.values()} == {0.0}


def test_both_layers_read_the_same_payload() -> None:
    situation = clean_situation(dollars=910.0, undeliverable_kw=12.0)
    assert situation_from_state(situation.as_state()) == situation


def test_a_fallback_answer_never_acts_on_its_own() -> None:
    situation = clean_situation()
    verdict = judge(fallback(situation), situation)
    assert verdict.action is Action.ASK_A_HUMAN
    assert "zero" in verdict.reason


# ----------------------------------------------------------------------- the policy


def test_a_clean_step_with_a_confident_model_acts() -> None:
    situation = clean_situation()
    verdict = judge(response(), situation)
    assert verdict.action is Action.ACT
    assert verdict.vetoed == ()
    assert "prefer reversible steps" in verdict.reason or "certainty" in verdict.reason


@pytest.mark.parametrize("veto", VETOES)
def test_any_hard_veto_sends_the_step_to_a_human(veto: str) -> None:
    situation = clean_situation()
    verdict = judge(response(**{veto: 0.2}), situation)
    assert verdict.action is Action.ASK_A_HUMAN
    assert verdict.vetoed == (veto,)
    assert PACK.by_id(veto).title.lower() in verdict.reason.lower()


def test_middling_certainty_acts_and_tells_the_operator() -> None:
    situation = clean_situation()
    verdict = judge(
        response(reversible=0.7, certainty_for_money=0.7, ask_when_in_doubt=0.7), situation
    )
    assert verdict.action is Action.ACT_AND_NOTIFY


def test_low_certainty_asks_a_human_and_names_the_weakest_principle() -> None:
    situation = clean_situation()
    verdict = judge(
        response(reversible=0.2, certainty_for_money=0.1, ask_when_in_doubt=0.2), situation
    )
    assert verdict.action is Action.ASK_A_HUMAN
    assert "more money at stake" in verdict.reason.lower()


def test_more_money_raises_the_bar_for_the_same_answers() -> None:
    answers = response(reversible=0.85, certainty_for_money=0.85, ask_when_in_doubt=0.85)
    small = judge(answers, clean_situation(dollars=10.0))
    large = judge(answers, clean_situation(dollars=50_000.0))
    assert large.bar > small.bar
    assert small.action is Action.ACT
    assert large.action is Action.ACT_AND_NOTIFY


def test_every_decision_carries_a_per_principle_breakdown() -> None:
    verdict = judge(response(), clean_situation())
    assert tuple(row.principle.id for row in verdict.breakdown) == PRIORITY_ORDER
    for row in verdict.breakdown:
        assert row.answer in {"yes", "no"}
        assert (row.weight > 0.0) is (row.principle.type is PrincipleType.SOFT)
    assert {row["principle"] for row in verdict.as_dict()["principles"]} == set(PRIORITY_ORDER)


def test_the_three_actions_are_the_only_outcomes() -> None:
    assert {a.value for a in Action} == {"act", "act-and-notify", "ask-a-human"}


# ------------------------------------------------------------- reserve semantics


def test_spare_energy_is_measured_above_the_reserve() -> None:
    on_the_line = clean_situation(spare_kwh_above_reserve=0.0)
    assert on_the_line.reserve_margin == 0.0
    breach = clean_situation(spare_kwh_above_reserve=-1.0)
    assert breach.reserve_margin < 0.0
    rules = judgment.rules_probabilities(breach)
    assert rules["protect_backup"] < 0.5


def test_a_reserve_breach_is_vetoed_by_the_rules_layer() -> None:
    breach = clean_situation(spare_kwh_above_reserve=-2.0)
    verdict = judge(fallback(breach), breach, allow_fallback_to_act=True)
    assert "protect_backup" in verdict.vetoed
    assert verdict.action is Action.ASK_A_HUMAN


# ----------------------------------------------- calibration on simulated overrides


def test_the_override_log_is_labelled_simulated_and_carries_reasons() -> None:
    log = overrides.episodes()
    assert log
    assert all(episode.simulated for episode in log)
    assert all(episode.reason.strip() for episode in log)
    assert {e.operator for e in log} == set(Action)


def test_the_split_is_deterministic_and_disjoint() -> None:
    log = overrides.episodes()
    train, holdout = overrides.split(log)
    assert len(train) + len(holdout) == len(log)
    assert not {e.episode_id for e in train} & {e.episode_id for e in holdout}
    assert overrides.split(log) == (train, holdout)


def test_calibration_only_moves_soft_weights_and_the_two_bars() -> None:
    log = overrides.episodes()[:8]
    answers = {e.episode_id: fallback(e.situation) for e in log}
    tuned = overrides.calibrate(log, answers)
    assert set(tuned.weights) == {p.id for p in PACK.softs}
    assert tuned.veto_floor == PACK.calibration.veto_floor
    assert tuned.money_slope == PACK.calibration.money_slope
    assert tuned.notify_threshold < tuned.act_threshold


def test_calibration_never_loses_agreement_on_the_half_it_was_fitted_on() -> None:
    log = overrides.episodes()[:12]
    answers = {e.episode_id: fallback(e.situation) for e in log}
    before = overrides.agreement(log, answers, PACK.calibration)
    tuned = overrides.calibrate(log, answers)
    assert overrides.agreement(log, answers, tuned) >= before


# --------------------------------------------------- the report, offline and keyless


@pytest.fixture(scope="module")
def report() -> judgment_report.Report:
    """One scored run of the blind pack, shared by the tests that only read it."""
    return judgment_report.build()


def test_report_scores_both_layers_against_the_committed_answer_key(
    report: judgment_report.Report,
) -> None:
    labels = report.labels
    assert {d.drill for d in report.drills} == set(labels.answers)
    for score in report.blind.values():
        assert score.total == sum(len(labels.of(d.drill)) for d in report.drills)
        assert 0 <= score.correct <= score.total


def test_every_disagreement_is_settled_by_the_answer_key(
    report: judgment_report.Report,
) -> None:
    for row in report.disagreements:
        assert row.rules != row.jev
        assert row.truth is labels_answer(report, row)
        winner = {True: judgment_report.JEV, False: judgment_report.RULES}[row.jev == row.truth]
        assert row.winner == winner


def labels_answer(report: judgment_report.Report, row: judgment_report.Disagreement) -> bool:
    return report.labels.of(row.drill)[row.principle]


def test_the_report_is_deterministic_and_reproducible() -> None:
    assert judgment_report.markdown(judgment_report.build()) == judgment_report.markdown(
        judgment_report.build()
    )


def test_calibration_is_reported_on_a_held_out_half_and_labelled_simulated(
    report: judgment_report.Report,
) -> None:
    cal = report.calibration
    assert cal.train + cal.holdout == cal.episodes
    assert 0.0 <= cal.before <= 1.0 and 0.0 <= cal.after <= 1.0
    text = judgment_report.markdown(report)
    assert "simulated" in text.lower()
    assert "Held-out half" in text


def test_the_saved_calibration_is_the_one_the_control_room_judges_with(  # type: ignore[no-untyped-def]
    tmp_path, report: judgment_report.Report
) -> None:
    path = judgment_report.save_calibration(report, path=tmp_path / "judgment_calibration.json")
    assert judgment.tuned_calibration(path=path) == report.calibration.calibration
    assert judgment.tuned_calibration(path=tmp_path / "missing.json") == PACK.calibration


def test_the_control_room_shows_a_breakdown_for_every_principle() -> None:
    engine = ControlRoomEngine(fleet_size=48)
    incident = engine.trigger_device_failure()
    situation, response, verdict = judge_incident(engine, incident)
    assert situation.label.startswith(incident.incident_id)
    assert response.source in (Source.FIXTURE, Source.FALLBACK)
    assert [row.principle.id for row in verdict.breakdown] == list(PRIORITY_ORDER)
    assert verdict.reason
