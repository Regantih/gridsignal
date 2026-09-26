"""The confidence gate: how sure is Jev, and does that clear the bar for this step?

`Code acts, the rules and the hard vetoes decide, a human approves every commit.`

Jev never approves anything. Every recovery step waits for a human operator, and the
gate's only job is to say whether Jev's answers were solid enough that a human should
expect to wave the step through. ``gate_clear`` is that reading, and it is recorded, not
acted on: nothing anywhere commits kW because it is true.

The gate reads clear only when **all** of these hold:

* Jev's confidence on the two answers this decision actually turns on — the root cause
  and the backup-risk score — is at least ``confidence_threshold`` (default 0.9),
* the plan is low risk to member backup (``backup_risk <= max_backup_risk``),
* the money at stake is under ``dollar_cap``,
* the plan covers the whole gap and no agent *in the plan* was flagged untrustworthy.

The gate deliberately does not take the minimum confidence over every answer: the
per-agent trust answers change *who* is awarded (distrusted bidders are dropped and the
award recomputed before approval), not whether the step is safe, so an uncertain trust
answer about an agent that is no longer in the plan must not veto the step.

Whenever Jev is offline the rules fallback answers with zero confidence, so the gate
never reads clear and the reason names the fallback.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from gridsignal.jev.client import JevResponse, Source
from gridsignal.jev.questions import BACKUP_RISK, ROOT_CAUSE, TRUST_PREFIX

DEFAULT_CONFIDENCE_THRESHOLD = 0.9
DEFAULT_MAX_BACKUP_RISK = 0.35
DEFAULT_DOLLAR_CAP = 500.0


@dataclass(frozen=True)
class ApprovalPolicy:
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    max_backup_risk: float = DEFAULT_MAX_BACKUP_RISK
    dollar_cap: float = DEFAULT_DOLLAR_CAP

    def as_dict(self) -> dict[str, float]:
        return {
            "confidence_threshold": self.confidence_threshold,
            "max_backup_risk": self.max_backup_risk,
            "dollar_cap": self.dollar_cap,
        }


@dataclass(frozen=True)
class ApprovalDecision:
    #: Whether Jev's answers cleared the confidence bar. Recorded for the operator;
    #: never a licence to commit — the approver is always a human either way.
    gate_clear: bool
    reason: str
    approver: str
    confidence: float
    backup_risk: float
    dollars: float
    root_cause: str
    distrusted: tuple[str, ...]
    source: Source

    def as_dict(self) -> dict[str, object]:
        return {
            "gate_clear": self.gate_clear,
            "reason": self.reason,
            "approver": self.approver,
            "confidence": round(self.confidence, 4),
            "backup_risk": round(self.backup_risk, 4),
            "dollars": round(self.dollars, 2),
            "root_cause": self.root_cause,
            "distrusted": list(self.distrusted),
            "jev_source": self.source.value,
        }


def deciding_confidence(response: JevResponse) -> float:
    """Confidence over the answers the gate turns on: root cause and backup risk."""
    answers = [response.answer(ROOT_CAUSE), response.answer(BACKUP_RISK)]
    present = [a.confidence for a in answers if a is not None]
    if len(present) < 2:
        return 0.0
    return min(present)


def distrusted_agents(response: JevResponse) -> tuple[str, ...]:
    """Agents whose card or bid Jev answered ``no`` on, whatever their signature says."""
    return tuple(
        sorted(
            qid[len(TRUST_PREFIX) :]
            for qid, answer in response.answers.items()
            if qid.startswith(TRUST_PREFIX) and answer.yes is False
        )
    )


def decide(
    response: JevResponse,
    dollars: float,
    covered_fully: bool,
    human_approver: str,
    policy: ApprovalPolicy | None = None,
    overrides: Mapping[str, float] | None = None,
    plan_agents: Collection[str] | None = None,
) -> ApprovalDecision:
    """Read one recovery step against the gate. It goes to ``human_approver`` regardless.

    ``plan_agents`` is the award set as it stands *after* distrusted bidders have been
    dropped; when given, only distrust of an agent still in the plan blocks the gate.
    """
    gate = policy or ApprovalPolicy(**{k: float(v) for k, v in dict(overrides or {}).items()})
    root = response.answer(ROOT_CAUSE)
    risk_answer = response.answer(BACKUP_RISK)
    risk = risk_answer.score if risk_answer and risk_answer.score is not None else 1.0
    confidence = deciding_confidence(response)
    distrusted = distrusted_agents(response)
    blocking = (
        distrusted if plan_agents is None else tuple(a for a in distrusted if a in plan_agents)
    )

    reasons: list[str] = []
    if response.source is Source.FALLBACK:
        reasons.append("Jev offline, rules fallback")
    if confidence < gate.confidence_threshold:
        reasons.append(f"confidence {confidence:.2f} below {gate.confidence_threshold:.2f}")
    if risk > gate.max_backup_risk:
        reasons.append(f"backup risk {risk:.2f} above {gate.max_backup_risk:.2f}")
    if dollars > gate.dollar_cap:
        reasons.append(f"${dollars:,.2f} above the ${gate.dollar_cap:,.0f} cap")
    if not covered_fully:
        reasons.append("plan does not cover the whole gap")
    if blocking:
        reasons.append(f"{len(blocking)} agent(s) flagged untrustworthy")

    clear = not reasons
    return ApprovalDecision(
        gate_clear=clear,
        reason=(
            f"Jev confident ({confidence:.2f}), low risk ({risk:.2f}), ${dollars:,.2f} under "
            f"cap — {human_approver} still approves"
            if clear
            else "; ".join(reasons)
        ),
        approver=human_approver,
        confidence=confidence,
        backup_risk=float(risk),
        dollars=round(dollars, 2),
        root_cause=(root.choice if root and root.choice else "unknown"),
        distrusted=distrusted,
        source=response.source,
    )
