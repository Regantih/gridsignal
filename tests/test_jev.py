"""Jev decision layer: transports, fixtures, rules fallback and the approval gate."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from gridsignal.control_room.engine import ControlRoomEngine
from gridsignal.jev import evaluate, rules
from gridsignal.jev import incident as jev_incident
from gridsignal.jev.client import (
    FIXTURE_DIR,
    FixtureStore,
    GatewayTransport,
    JevAnswer,
    JevClient,
    JevResponse,
    QuestionKind,
    Source,
    TypeSafeTransport,
    request_key,
    transport_from_env,
)
from gridsignal.jev.policy import ApprovalPolicy, decide
from gridsignal.jev.questions import (
    BACKUP_RISK,
    ROOT_CAUSE,
    IncidentSnapshot,
    Suspect,
    incident_questions,
)
from gridsignal.mesh.scenarios import available_scenarios, load_scenario
from gridsignal.simulate import run_scenario

KEYS = ("AI_GATEWAY_API_KEY", "TYPESAFE_API_KEY")


@pytest.fixture(autouse=True)
def no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test runs as a judge would: no key, and no network reachable."""
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)

    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("the default path must not touch the network")

    monkeypatch.setattr(httpx, "post", refuse)


def snapshot(**overrides: object) -> IncidentSnapshot:
    base: dict[str, object] = {
        "scenario": "unit",
        "agents": 48,
        "batteries": 48,
        "offline_agents": 1,
        "offline_zones": (),
        "offline_gateway_rings": 1,
        "largest_offline_group_in_one_ring": 1,
        "gateway_ring_size": 12,
        "rejected_cards": 0,
        "stale_agents": 0,
        "max_telemetry_age_s": 240,
        "lost_kw": 6.6,
        "price_usd_mwh": 199.7,
        "event_hours": 1.5,
        "dollars_at_risk": 55.7,
        "bidders": 10,
        "proposed_kw": 6.6,
        "uncovered_kw": 0.0,
        "plan_agents": 3,
        "plan_mean_soc": 0.8,
        "plan_min_spare_kwh": 6.0,
        "backup_reserve_kwh": 4.0,
        "suspects": (),
    }
    base.update(overrides)
    return IncidentSnapshot(**base)  # type: ignore[arg-type]


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


class FakeHttp:
    """Captures the request body so both transports can be compared."""

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.url = ""
        self.body: dict[str, object] = {}
        self.headers: dict[str, str] = {}

    def post(self, url: str, headers: dict[str, str], json: dict[str, object], timeout: float):
        self.url, self.headers, self.body = url, headers, json
        return FakeResponse(self.payload)


GATEWAY_PAYLOAD = {
    "model": "typesafe-ai/jev",
    "answers": {
        ROOT_CAUSE: {
            "type": "choice",
            "choice": "gateway_outage",
            "probabilities": {"gateway_outage": 0.93, "device_fault": 0.07},
            "confidence": 0.93,
        },
        BACKUP_RISK: {"type": "score", "score": 0.2, "confidence": 0.95},
        "trust_BAT-001": {"type": "boolean", "probability": 0.91},
    },
}
DIRECT_PAYLOAD = {
    "model": "jev-latest",
    "answers": {
        ROOT_CAUSE: {
            "type": "choice",
            "choice": "gateway_outage",
            "probabilities": {"gateway_outage": 0.93, "device_fault": 0.07},
            "confidence": 0.93,
        },
        BACKUP_RISK: {"type": "score", "score": 0.2, "confidence": 0.95},
        "trust_BAT-001": {"type": "noul", "noul": 0.91, "confidence": 0.91},
    },
}


def suspect_snapshot() -> IncidentSnapshot:
    return snapshot(
        suspects=(
            Suspect(
                agent_id="BAT-001",
                card_status="verified",
                signature_valid=True,
                heartbeat_age_s=10,
                claimed_kw=2.0,
                claimed_kwh=9.0,
                soc=0.7,
                bid_kw=2.0,
                fleet_median_kw=2.1,
            ),
        )
    )


# ----------------------------------------------------------------- transports


def test_transport_picks_whichever_key_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    assert transport_from_env({}) is None
    assert isinstance(transport_from_env({"TYPESAFE_API_KEY": "t"}), TypeSafeTransport)
    assert isinstance(transport_from_env({"AI_GATEWAY_API_KEY": "g"}), GatewayTransport)
    both = transport_from_env({"AI_GATEWAY_API_KEY": "g", "TYPESAFE_API_KEY": "t"})
    assert isinstance(both, GatewayTransport)


def test_both_transports_normalise_to_one_shape() -> None:
    snap = suspect_snapshot()
    questions = incident_questions(snap)

    gateway_http = FakeHttp(GATEWAY_PAYLOAD)
    gateway = JevClient(transport=GatewayTransport("key", client=gateway_http))
    direct_http = FakeHttp(DIRECT_PAYLOAD)
    direct = JevClient(transport=TypeSafeTransport("key", client=direct_http))

    answered = [c.ask(snap.as_state(), questions) for c in (gateway, direct)]

    # The wire formats differ: boolean on the gateway, noul direct.
    assert gateway_http.body["questions"]["trust_BAT-001"]["type"] == "boolean"  # type: ignore[index]
    assert direct_http.body["questions"]["trust_BAT-001"]["type"] == "noul"  # type: ignore[index]
    assert gateway_http.body["model"] == "typesafe-ai/jev"
    assert direct_http.body["model"] == "jev-latest"
    assert gateway_http.headers["Authorization"] == "Bearer key"

    for response in answered:
        assert response.source is Source.LIVE
        assert response.answer(ROOT_CAUSE).choice == "gateway_outage"  # type: ignore[union-attr]
        assert response.answer(ROOT_CAUSE).confidence == 0.93  # type: ignore[union-attr]
        assert response.answer(BACKUP_RISK).score == 0.2  # type: ignore[union-attr]
        trust = response.answer("trust_BAT-001")
        assert trust is not None and trust.yes is True
        assert trust.kind is QuestionKind.NOUL
        assert trust.confidence >= 0.9


def test_only_simulated_state_is_sent() -> None:
    http = FakeHttp(GATEWAY_PAYLOAD)
    client = JevClient(transport=GatewayTransport("key", client=http))
    snap = suspect_snapshot()
    client.ask(snap.as_state(), incident_questions(snap))
    assert http.body["state"]["simulation"] is True  # type: ignore[index]
    assert "no real devices" in str(http.body["state"]["note"]).lower()  # type: ignore[index]


# ------------------------------------------------------------------ fixtures


def test_live_answer_is_recorded_then_replayed_without_a_key(tmp_path: Path) -> None:
    snap = suspect_snapshot()
    questions = incident_questions(snap)
    http = FakeHttp(GATEWAY_PAYLOAD)
    recorder = JevClient(
        fixtures=FixtureStore("unit", tmp_path),
        transport=GatewayTransport("key", client=http),
    )
    live = recorder.ask(snap.as_state(), questions)
    path = recorder.flush()
    assert path is not None and path.exists()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["model"] == "typesafe-ai/jev"
    assert saved["recorded_at"]
    assert "Bearer" not in path.read_text(encoding="utf-8")

    replay = JevClient(fixtures=FixtureStore("unit", tmp_path), fallback=rules.answers)
    cached = replay.ask(snap.as_state(), questions)
    assert cached.source is Source.FIXTURE
    assert cached.answer(ROOT_CAUSE).choice == live.answer(ROOT_CAUSE).choice  # type: ignore[union-attr]
    assert cached.answer(BACKUP_RISK).score == live.answer(BACKUP_RISK).score  # type: ignore[union-attr]


def test_fixture_miss_falls_back_to_rules(tmp_path: Path) -> None:
    snap = suspect_snapshot()
    client = JevClient(fixtures=FixtureStore("unit", tmp_path), fallback=rules.answers)
    response = client.ask(snap.as_state(), incident_questions(snap))
    assert response.source is Source.FALLBACK
    assert response.model == "rules-fallback"


def test_request_key_is_stable_and_state_sensitive() -> None:
    snap = suspect_snapshot()
    questions = incident_questions(snap)
    assert request_key(snap.as_state(), questions) == request_key(snap.as_state(), questions)
    other = snapshot(offline_agents=9).as_state()
    assert request_key(other, questions) != request_key(snap.as_state(), questions)


def test_every_bundled_scenario_has_recorded_answers() -> None:
    for path in available_scenarios():
        store = FixtureStore(load_scenario(path).slug, FIXTURE_DIR)
        assert len(store) > 0, f"no recorded Jev answers for {path.name}"
        assert store.model and store.recorded_at


# -------------------------------------------------------------------- rules


def test_rules_read_the_injection_from_the_state() -> None:
    assert rules.root_cause(snapshot().as_state()) == "device_fault"
    assert rules.root_cause(snapshot(offline_zones=("LZ_HOUSTON",)).as_state()) == "gateway_outage"
    assert (
        rules.root_cause(snapshot(offline_agents=4, largest_offline_group_in_one_ring=4).as_state())
        == "gateway_outage"
    )
    assert (
        rules.root_cause(snapshot(offline_agents=0, stale_agents=3).as_state()) == "telemetry_lag"
    )
    assert (
        rules.root_cause(snapshot(offline_agents=0, rejected_cards=1).as_state()) == "spoofed_agent"
    )


def test_rules_name_the_grid_when_most_of_the_missing_kw_is_grid_side() -> None:
    """After tuning on held-out: grid conditions outrank the components that also failed."""
    grid_led = snapshot(lost_kw=1000.0, grid_side_kw=900.0, frequency_hz=59.8)
    component_led = snapshot(lost_kw=1000.0, grid_side_kw=100.0, frequency_hz=59.8)

    assert rules.root_cause(grid_led.as_state()) == "grid_event"
    assert rules.root_cause(component_led.as_state()) == "device_fault"
    assert rules.root_cause(snapshot(islanded_agents=12).as_state()) == "gateway_outage"


def test_grid_conditions_are_only_sent_when_the_drill_has_them() -> None:
    assert "grid_conditions" not in snapshot().as_state()
    assert "grid_conditions" in snapshot(frequency_hz=59.8).as_state()


def test_rules_answers_never_clear_the_confidence_gate() -> None:
    snap = suspect_snapshot()
    answers = rules.answers(snap.as_state(), incident_questions(snap))
    assert set(answers) == set(incident_questions(snap))
    assert all(a.confidence == 0.0 for a in answers.values())


def test_rules_distrust_an_agent_that_claims_too_much() -> None:
    snap = snapshot(
        suspects=(
            Suspect(
                agent_id="BAT-013",
                card_status="rejected",
                signature_valid=False,
                heartbeat_age_s=5,
                claimed_kw=99.0,
                claimed_kwh=9.0,
                soc=0.8,
                bid_kw=9.0,
                fleet_median_kw=2.0,
            ),
        )
    )
    assert rules.trustworthy(snap.as_state(), "BAT-013") is False
    liar = snapshot(
        suspects=(
            Suspect(
                agent_id="BAT-014",
                card_status="verified",
                signature_valid=True,
                heartbeat_age_s=5,
                claimed_kw=40.0,
                claimed_kwh=9.0,
                soc=0.8,
                bid_kw=9.0,
                fleet_median_kw=2.0,
            ),
        )
    )
    assert rules.trustworthy(liar.as_state(), "BAT-014") is False


# ------------------------------------------------------------ approval gate


def response_with(confidence: float, risk: float, trust: bool = True) -> JevResponse:
    return JevResponse(
        answers={
            ROOT_CAUSE: JevAnswer(
                question_id=ROOT_CAUSE,
                kind=QuestionKind.CHOICE,
                confidence=confidence,
                choice="gateway_outage",
                probabilities={"gateway_outage": confidence},
            ),
            BACKUP_RISK: JevAnswer(
                question_id=BACKUP_RISK,
                kind=QuestionKind.SCORE,
                confidence=confidence,
                score=risk,
            ),
            "trust_BAT-001": JevAnswer(
                question_id="trust_BAT-001",
                kind=QuestionKind.NOUL,
                confidence=confidence,
                yes=trust,
            ),
        },
        model="typesafe-ai/jev",
        source=Source.LIVE,
        latency_ms=420.0,
    )


def test_confident_low_risk_cheap_steps_are_auto_approved() -> None:
    decision = decide(
        response_with(0.95, 0.10), dollars=120.0, covered_fully=True, human_approver="operator"
    )
    assert decision.auto_approved
    assert decision.approver.startswith("jev-auto")
    assert "under cap" in decision.reason


def test_low_confidence_routes_to_the_human() -> None:
    decision = decide(
        response_with(0.72, 0.10), dollars=120.0, covered_fully=True, human_approver="operator"
    )
    assert not decision.auto_approved
    assert decision.approver == "operator"
    assert "below 0.90" in decision.reason


def test_high_backup_risk_routes_to_the_human() -> None:
    decision = decide(
        response_with(0.99, 0.80), dollars=10.0, covered_fully=True, human_approver="operator"
    )
    assert not decision.auto_approved
    assert "backup risk" in decision.reason


def test_dollars_over_the_cap_route_to_the_human() -> None:
    decision = decide(
        response_with(0.99, 0.05), dollars=9_000.0, covered_fully=True, human_approver="operator"
    )
    assert not decision.auto_approved
    assert "cap" in decision.reason


def test_distrusted_agent_or_partial_cover_routes_to_the_human() -> None:
    distrusted = decide(
        response_with(0.99, 0.05, trust=False),
        dollars=10.0,
        covered_fully=True,
        human_approver="operator",
    )
    partial = decide(
        response_with(0.99, 0.05), dollars=10.0, covered_fully=False, human_approver="operator"
    )
    assert not distrusted.auto_approved and "untrustworthy" in distrusted.reason
    assert not partial.auto_approved and "whole gap" in partial.reason


def test_threshold_and_cap_are_configurable() -> None:
    relaxed = ApprovalPolicy(confidence_threshold=0.7, max_backup_risk=0.9, dollar_cap=10_000.0)
    decision = decide(
        response_with(0.72, 0.80),
        dollars=9_000.0,
        covered_fully=True,
        human_approver="operator",
        policy=relaxed,
    )
    assert decision.auto_approved


def test_offline_fallback_can_never_auto_approve() -> None:
    snap = suspect_snapshot()
    client = JevClient.offline(fallback=rules.answers)
    response = client.ask(snap.as_state(), incident_questions(snap))
    decision = decide(response, dollars=1.0, covered_fully=True, human_approver="operator")
    assert not decision.auto_approved
    assert "Jev offline, rules fallback" in decision.reason


# --------------------------------------------------------------- integration


def test_scenario_runs_from_fixtures_with_no_key_and_no_network() -> None:
    scenario = load_scenario("single_device.yaml")
    result = run_scenario(scenario)
    assert result.metrics.jev_source == Source.FIXTURE.value
    assert result.metrics.root_cause_truth == "device_fault"
    assert result.metrics.human_approvals + result.metrics.auto_approvals == result.metrics.rounds
    assert result.jev_label == "Jev (recorded answers)"
    kinds = {m.kind.value for m in result.bus.messages}
    assert "jev_decision" in kinds
    decision_messages = [m for m in result.bus.messages if m.kind.value == "jev_decision"]
    assert all("confidence" in m.payload for m in decision_messages)
    assert any("latency_ms" in m.payload for m in decision_messages)


def test_scenario_runs_offline_labelled_as_rules_fallback() -> None:
    scenario = load_scenario("zone_outage.yaml")
    result = run_scenario(scenario, jev=JevClient.offline(fallback=rules.answers))
    assert result.jev_label == "Jev offline, rules fallback"
    assert result.metrics.root_cause == "gateway_outage"
    assert result.metrics.root_cause_correct
    assert result.metrics.auto_approvals == 0
    assert result.metrics.human_approvals >= 1


def test_ground_truth_comes_from_the_injections() -> None:
    truths = {
        load_scenario(p).slug: load_scenario(p).ground_truth_root_cause
        for p in available_scenarios()
    }
    assert truths["zone_outage"] == "gateway_outage"
    assert truths["single_device"] == "device_fault"
    assert set(truths.values()) <= {
        "device_fault",
        "gateway_outage",
        "telemetry_lag",
        "spoofed_agent",
        "grid_event",
    }


def test_control_room_incident_is_scored_by_jev() -> None:
    eng = ControlRoomEngine()
    incident = eng.trigger_device_failure()
    response, decision = jev_incident.ask(eng, incident)
    assert response.source is Source.FIXTURE
    assert response.answer(ROOT_CAUSE) is not None
    assert not decision.auto_approved  # the bundled incident is above the risk threshold
    assert eng.pending_incident is not None  # the human gate is untouched


def test_eval_scores_both_layers_against_ground_truth() -> None:
    report = evaluate.evaluate()
    rules_only = report.summary(evaluate.RULES)
    jev = report.summary(evaluate.JEV)
    assert rules_only is not None and jev is not None
    assert rules_only.scenarios == jev.scenarios == len(available_scenarios())
    assert rules_only.median_latency_ms <= jev.median_latency_ms
    assert 0.0 <= jev.accuracy <= 1.0
    table = evaluate.markdown(report)
    assert "Root-cause accuracy" in table and "rules-only" in table
