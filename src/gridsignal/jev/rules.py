"""Deterministic fallback for when Jev is unreachable and nothing was recorded.

These are the rules the mesh used before Jev existed. They answer the same questions in
the same shape, but always with **zero confidence**, so the confidence-gated policy can
never auto-approve on them: with no model, every step goes to a human.
"""

from __future__ import annotations

from collections.abc import Mapping

from gridsignal.jev.client import JevAnswer, Question, QuestionKind
from gridsignal.jev.questions import BACKUP_RISK, ROOT_CAUSE, TRUST_PREFIX


def _section(state: Mapping[str, object], key: str) -> dict[str, object]:
    section = state.get(key)
    return dict(section) if isinstance(section, dict) else {}


def _number(section: Mapping[str, object], key: str, default: float = 0.0) -> float:
    value = section.get(key, default)
    return float(value) if isinstance(value, (int, float)) else default


def root_cause(state: Mapping[str, object]) -> str:
    """Rule order matters: a group outage explains more than the agents inside it."""
    fleet = _section(state, "fleet")
    offline = int(_number(fleet, "offline_agents"))
    zones = fleet.get("offline_zones")
    zone_count = len(zones) if isinstance(zones, list) else 0
    ring_group = int(_number(fleet, "largest_offline_group_in_one_gateway_ring"))
    stale = int(_number(fleet, "stale_agents"))
    rejected = int(_number(fleet, "rejected_cards"))

    if zone_count or ring_group > 1:
        return "gateway_outage"
    if offline == 1:
        return "device_fault"
    if offline == 0 and stale:
        return "telemetry_lag"
    if offline == 0 and rejected:
        return "spoofed_agent"
    return "device_fault"


def trustworthy(state: Mapping[str, object], agent_id: str) -> bool:
    for raw in state.get("suspect_agents") or []:
        if not isinstance(raw, dict) or str(raw.get("agent_id")) != agent_id:
            continue
        if not bool(raw.get("hmac_signature_valid", True)):
            return False
        median = _number(raw, "fleet_median_kw_available", 1.0) or 1.0
        if _number(raw, "claimed_kw_available") > 3.0 * median:
            return False
        if _number(raw, "heartbeat_age_s") > 120:
            return False
        return True
    return True


def backup_risk(state: Mapping[str, object]) -> float:
    """Thin spare energy and low state of charge both push the risk up."""
    plan = _section(state, "recovery_plan")
    reserve = _number(plan, "homeowner_backup_reserve_kwh", 4.0) or 4.0
    spare = _number(plan, "min_spare_kwh_after_plan")
    soc = _number(plan, "mean_state_of_charge_of_committed", 1.0)
    thinness = max(0.0, min(1.0, 1.0 - spare / reserve))
    emptiness = max(0.0, min(1.0, 1.0 - soc))
    return round(min(1.0, 0.6 * thinness + 0.4 * emptiness), 4)


def answers(state: Mapping[str, object], questions: Mapping[str, Question]) -> dict[str, JevAnswer]:
    """Answer a Jev question set from the rules, with zero confidence throughout."""
    result: dict[str, JevAnswer] = {}
    for question_id, question in questions.items():
        if question_id == ROOT_CAUSE:
            choice = root_cause(state)
            result[question_id] = JevAnswer(
                question_id=question_id,
                kind=QuestionKind.CHOICE,
                confidence=0.0,
                probabilities={choice: 1.0},
                choice=choice,
            )
        elif question_id == BACKUP_RISK:
            result[question_id] = JevAnswer(
                question_id=question_id,
                kind=QuestionKind.SCORE,
                confidence=0.0,
                score=backup_risk(state),
            )
        elif question_id.startswith(TRUST_PREFIX):
            yes = trustworthy(state, question_id[len(TRUST_PREFIX) :])
            result[question_id] = JevAnswer(
                question_id=question_id,
                kind=QuestionKind.NOUL,
                confidence=0.0,
                probabilities={"yes": 1.0 if yes else 0.0, "no": 0.0 if yes else 1.0},
                yes=yes,
            )
        elif question.kind is QuestionKind.SCORE:
            result[question_id] = JevAnswer(
                question_id=question_id, kind=QuestionKind.SCORE, confidence=0.0, score=0.0
            )
        else:
            result[question_id] = JevAnswer(
                question_id=question_id, kind=question.kind, confidence=0.0, yes=True
            )
    return result
