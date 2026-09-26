"""The confidence gate: when may Jev approve a recovery step on its own?

`Code acts, Jev decides, humans approve when Jev is unsure.`

A step is auto-approved only when **all** of these hold:

* Jev's confidence on every decision in the step is at least ``confidence_threshold``
  (default 0.9),
* the plan is low risk to homeowner backup (``backup_risk <= max_backup_risk``),
* the money at stake is under ``dollar_cap``,
* the plan covers the whole gap and no agent was flagged untrustworthy.

Anything else routes to the human approval gate with Jev's probabilities attached, which
is also what happens whenever Jev is offline: the rules fallback answers with zero
confidence, so it can never clear the gate.
"""

from __future__ import annotations

from collections.abc import Mapping
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
    auto_approved: bool
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
            "auto_approved": self.auto_approved,
            "reason": self.reason,
            "approver": self.approver,
            "confidence": round(self.confidence, 4),
            "backup_risk": round(self.backup_risk, 4),
            "dollars": round(self.dollars, 2),
            "root_cause": self.root_cause,
            "distrusted": list(self.distrusted),
            "jev_source": self.source.value,
        }


def _min_confidence(response: JevResponse) -> float:
    if not response.answers:
        return 0.0
    return min(a.confidence for a in response.answers.values())


def decide(
    response: JevResponse,
    dollars: float,
    covered_fully: bool,
    human_approver: str,
    policy: ApprovalPolicy | None = None,
    overrides: Mapping[str, float] | None = None,
) -> ApprovalDecision:
    """Route one recovery step either to Jev or to the human gate."""
    gate = policy or ApprovalPolicy(**{k: float(v) for k, v in dict(overrides or {}).items()})
    root = response.answer(ROOT_CAUSE)
    risk_answer = response.answer(BACKUP_RISK)
    risk = risk_answer.score if risk_answer and risk_answer.score is not None else 1.0
    confidence = _min_confidence(response)
    distrusted = tuple(
        sorted(
            qid[len(TRUST_PREFIX) :]
            for qid, answer in response.answers.items()
            if qid.startswith(TRUST_PREFIX) and answer.yes is False
        )
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
    if distrusted:
        reasons.append(f"{len(distrusted)} agent(s) flagged untrustworthy")

    auto = not reasons
    return ApprovalDecision(
        auto_approved=auto,
        reason=(
            f"Jev confident ({confidence:.2f}), low risk ({risk:.2f}), ${dollars:,.2f} under cap"
            if auto
            else "; ".join(reasons)
        ),
        approver=(
            f"jev-auto ({response.model}, confidence {confidence:.2f})" if auto else human_approver
        ),
        confidence=confidence,
        backup_risk=float(risk),
        dollars=round(dollars, 2),
        root_cause=(root.choice if root and root.choice else "unknown"),
        distrusted=distrusted,
        source=response.source,
    )
