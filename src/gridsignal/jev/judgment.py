"""The operator judgment model: six principles, one yes/no question each, one verdict.

`Code acts, Jev decides, humans approve when Jev is unsure.` This module says what
"unsure" means in the language an operator would use.

:mod:`gridsignal.jev.principles` (the YAML next to this file, committed before anything
was scored against it) lists the principles a fleet operator weighs, in priority order,
each marked a **hard veto** or a **weighted soft** principle. Every principle is one
yes/no safety question, phrased so that *yes* means the step is safe on that principle:

* protect member backup first — reserve breach,
* never break ERCOT market rules — spoofed cards and control authority,
* commit only what the fleet can deliver — deliverability across the award window,
* prefer reversible steps,
* more money at stake needs more certainty,
* when in doubt ask a human — grid stress.

Jev answers each with a probability. The rules fallback answers the same questions from
documented thresholds, always with zero confidence, so a fleet running without a model
can never talk itself into acting alone.

The combination is deliberately transparent, not learned end to end:

1. a veto principle whose probability of being satisfied falls below ``veto_floor``
   sends the decision to a human, whatever the rest say;
2. the soft answers are averaged by weight into a score in [0, 1];
3. that score is compared against a certainty bar that rises with the money at stake:
   at or above ``act_threshold`` the mesh acts, above ``notify_threshold`` it acts and
   tells the operator, below that it asks first.

Only the soft weights and the two thresholds are calibrated (see
:mod:`gridsignal.jev.overrides`), and only on *simulated* operator overrides. Nothing
here is claimed to match how a real Base operator decides.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import yaml

from gridsignal import paths
from gridsignal.jev.client import JevAnswer, JevResponse, Question, QuestionKind, Source
from gridsignal.jev.questions import IncidentSnapshot

#: Simulated diagnosis confidence: one dark gateway ring is an unambiguous root cause,
#: a wave spread across rings is not. Documented assumptions, never fitted.
CLEAN_DIAGNOSIS_CONFIDENCE = 0.9
MUDDIED_DIAGNOSIS_CONFIDENCE = 0.55

PRINCIPLES_PATH = Path(__file__).with_name("principles.yaml")
#: Written by ``python -m gridsignal.judgment_report`` from the simulated override log.
TUNED_PATH = paths.DATA_DIR / "judgment_calibration.json"
PREFIX = "principle_"

#: Simulated grid-stress markers used by the rules fallback and by the state Jev sees.
NOMINAL_HZ = 60.0
SCARCITY_USD_MWH = 200.0


class PrincipleType(StrEnum):
    VETO = "veto"
    SOFT = "soft"


class Action(StrEnum):
    ACT = "act"
    ACT_AND_NOTIFY = "act-and-notify"
    ASK_A_HUMAN = "ask-a-human"


@dataclass(frozen=True)
class Principle:
    id: str
    priority: int
    type: PrincipleType
    title: str
    topic: str
    question: str
    yes_means: str
    no_means: str
    weight: float = 0.0

    @property
    def question_id(self) -> str:
        return f"{PREFIX}{self.id}"

    def as_question(self) -> Question:
        return Question(
            kind=QuestionKind.NOUL,
            instructions=(
                "A simulated home-battery fleet is about to take one step during a grid "
                f"event. {self.question.strip()}"
            ),
            options={"yes": self.yes_means.strip(), "no": self.no_means.strip()},
        )


@dataclass(frozen=True)
class Calibration:
    """The only numbers the override log is allowed to move."""

    weights: dict[str, float] = field(default_factory=dict)
    act_threshold: float = 0.80
    notify_threshold: float = 0.60
    veto_floor: float = 0.5
    money_slope: float = 0.10
    dollar_scale: float = 5000.0

    def bar(self, base: float, dollars: float) -> float:
        """The certainty a step needs: higher when there is more money on it."""
        pressure = min(max(dollars, 0.0) / self.dollar_scale, 1.0)
        return min(base + self.money_slope * pressure, 1.0)

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> Calibration:
        weights = {str(k): float(v) for k, v in dict(raw.get("weights") or {}).items()}  # type: ignore[arg-type]
        numbers = {
            field_name: float(raw[field_name])  # type: ignore[arg-type]
            for field_name in (
                "act_threshold",
                "notify_threshold",
                "veto_floor",
                "money_slope",
                "dollar_scale",
            )
            if field_name in raw
        }
        return cls(weights=weights, **numbers)

    def as_dict(self) -> dict[str, object]:
        return {
            "weights": {k: round(v, 4) for k, v in sorted(self.weights.items())},
            "act_threshold": round(self.act_threshold, 4),
            "notify_threshold": round(self.notify_threshold, 4),
            "veto_floor": round(self.veto_floor, 4),
            "money_slope": round(self.money_slope, 4),
            "dollar_scale": self.dollar_scale,
        }


@dataclass(frozen=True)
class Pack:
    """The committed question pack: the principles and the policy they start with."""

    principles: tuple[Principle, ...]
    calibration: Calibration
    version: int
    name: str

    def by_id(self, principle_id: str) -> Principle:
        for principle in self.principles:
            if principle.id == principle_id:
                return principle
        raise KeyError(principle_id)

    @property
    def softs(self) -> tuple[Principle, ...]:
        return tuple(p for p in self.principles if p.type is PrincipleType.SOFT)

    def questions(self) -> dict[str, Question]:
        return {p.question_id: p.as_question() for p in self.principles}


def load_pack(path: Path = PRINCIPLES_PATH) -> Pack:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    principles = tuple(
        sorted(
            (
                Principle(
                    id=str(item["id"]),
                    priority=int(item["priority"]),
                    type=PrincipleType(str(item["type"])),
                    title=str(item["title"]),
                    topic=str(item["topic"]),
                    question=str(item["question"]),
                    yes_means=str(item["yes_means"]),
                    no_means=str(item["no_means"]),
                    weight=float(item.get("weight", 0.0)),
                )
                for item in raw["principles"]
            ),
            key=lambda p: p.priority,
        )
    )
    policy = dict(raw.get("policy") or {})
    calibration = Calibration(
        weights={p.id: p.weight for p in principles if p.type is PrincipleType.SOFT},
        act_threshold=float(policy.get("act_threshold", 0.80)),
        notify_threshold=float(policy.get("notify_threshold", 0.60)),
        veto_floor=float(policy.get("veto_floor", 0.5)),
        money_slope=float(policy.get("money_slope", 0.10)),
        dollar_scale=float(policy.get("dollar_scale", 5000.0)),
    )
    return Pack(
        principles=principles,
        calibration=calibration,
        version=int(raw.get("version", 1)),
        name=str(raw.get("pack", "safety")),
    )


PACK = load_pack()


def tuned_calibration(path: Path = TUNED_PATH, pack: Pack = PACK) -> Calibration:
    """The weights and bars fitted on the simulated override log, if they are recorded.

    Falls back to the pack as committed, so the product behaves identically on a clone
    that has never run the calibration.
    """
    if not path.exists():
        return pack.calibration
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Calibration.from_dict(dict(raw.get("calibration") or raw))


@dataclass(frozen=True)
class Situation:
    """One step the fleet is about to take, as the judgment layer sees it.

    Every field is simulated fleet state. No member names, no addresses, no credentials.
    """

    label: str
    dollars: float
    committed_kw: float
    uncovered_kw: float
    #: Stored energy left **above** the member's backup reserve once the plan has run.
    #: Zero lands exactly on the reserve; negative eats into it.
    spare_kwh_above_reserve: float
    backup_reserve_kwh: float
    mean_soc: float
    bidders: int
    unverified_bidders: int = 0
    other_tenant_kw: float = 0.0
    undeliverable_kw: float = 0.0
    frequency_hz: float = NOMINAL_HZ
    islanded_homes: int = 0
    price_usd_mwh: float = 0.0
    reversible: bool = True
    root_cause_confidence: float = 0.0

    @property
    def reserve_margin(self) -> float:
        """Spare energy above the reserve as a share of it: 0 is exactly on the line."""
        reserve = self.backup_reserve_kwh or 1.0
        return round(self.spare_kwh_above_reserve / reserve, 4)

    @property
    def grid_stressed(self) -> bool:
        return bool(
            self.frequency_hz < NOMINAL_HZ - 0.05
            or self.islanded_homes
            or self.price_usd_mwh >= SCARCITY_USD_MWH
        )

    def as_state(self) -> dict[str, object]:
        return {
            "simulation": True,
            "note": ("Simulated home-battery fleet. No real devices, utilities or market systems."),
            "step": self.label,
            "money": {
                "dollars_at_stake": round(self.dollars, 2),
                "price_usd_per_mwh": round(self.price_usd_mwh, 2),
            },
            "plan": {
                "committed_kw": round(self.committed_kw, 2),
                "uncovered_kw": round(self.uncovered_kw, 2),
                "bidders": self.bidders,
                "bidders_whose_card_did_not_verify_or_contradicts_telemetry": (
                    self.unverified_bidders
                ),
                "kw_controlled_by_a_utility_partner_not_this_fleet": round(self.other_tenant_kw, 2),
                "kw_that_cannot_be_held_for_the_whole_window": round(self.undeliverable_kw, 2),
                "reversible_within_the_event": self.reversible,
                "root_cause_confidence": round(self.root_cause_confidence, 4),
            },
            "member_backup": {
                "spare_kwh_above_the_reserve_after_the_plan": round(
                    self.spare_kwh_above_reserve, 3
                ),
                "homeowner_backup_reserve_kwh": round(self.backup_reserve_kwh, 3),
                "mean_state_of_charge_of_committed": round(self.mean_soc, 4),
            },
            "grid_conditions": {
                "simulated": True,
                "frequency_hz": round(self.frequency_hz, 3),
                "homes_islanded_on_their_own_battery": self.islanded_homes,
            },
        }


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 4)


def rules_probabilities(situation: Situation) -> dict[str, float]:
    """The fallback's answer to each principle: a probability, from documented thresholds.

    These are the thresholds the mesh used before a model existed. They are reported with
    zero confidence, which is what keeps them out of the auto-act path.
    """
    margin = situation.reserve_margin
    money_pressure = min(situation.dollars / 5000.0, 1.0)
    return {
        # Exactly on the reserve is a coin flip; a reserve's worth of room above it is
        # as safe as this rule gets; an emptied reserve is a flat no.
        "protect_backup": _clamp(0.5 + 0.5 * min(margin, 1.0)),
        "market_rules": _clamp(
            1.0 - min(situation.unverified_bidders, 3) / 3.0
            if situation.other_tenant_kw <= 0
            else 0.0
        ),
        "deliverable": _clamp(
            1.0 - situation.undeliverable_kw / max(situation.committed_kw, 1.0)
            if situation.uncovered_kw <= 0
            else 0.5 - situation.undeliverable_kw / max(situation.committed_kw, 1.0)
        ),
        "reversible": 0.95 if situation.reversible else 0.05,
        "certainty_for_money": _clamp(situation.root_cause_confidence - 0.5 * money_pressure),
        "ask_when_in_doubt": 0.2 if situation.grid_stressed else 0.9,
    }


def rules_answers(
    state: Mapping[str, object], questions: Mapping[str, Question]
) -> dict[str, JevAnswer]:
    """Answer the principle pack from the rules, with zero confidence throughout.

    The state carries the numbers, so the fallback reads the same payload Jev is sent.
    """
    situation = situation_from_state(state)
    probabilities = rules_probabilities(situation)
    answers: dict[str, JevAnswer] = {}
    for question_id in questions:
        principle_id = question_id[len(PREFIX) :]
        p = probabilities.get(principle_id, 0.5)
        answers[question_id] = JevAnswer(
            question_id=question_id,
            kind=QuestionKind.NOUL,
            confidence=0.0,
            probabilities={"yes": round(p, 4), "no": round(1.0 - p, 4)},
            yes=p >= 0.5,
        )
    return answers


def situation_from_state(state: Mapping[str, object]) -> Situation:
    """Rebuild a situation from the payload sent to Jev, so both layers see one truth."""

    def section(key: str) -> dict[str, object]:
        value = state.get(key)
        return dict(value) if isinstance(value, dict) else {}

    def number(source: Mapping[str, object], key: str, default: float = 0.0) -> float:
        value = source.get(key, default)
        return float(value) if isinstance(value, (int, float)) else default

    plan = section("plan")
    money = section("money")
    backup = section("member_backup")
    grid = section("grid_conditions")
    return Situation(
        label=str(state.get("step", "step")),
        dollars=number(money, "dollars_at_stake"),
        price_usd_mwh=number(money, "price_usd_per_mwh"),
        committed_kw=number(plan, "committed_kw"),
        uncovered_kw=number(plan, "uncovered_kw"),
        bidders=int(number(plan, "bidders")),
        unverified_bidders=int(
            number(plan, "bidders_whose_card_did_not_verify_or_contradicts_telemetry")
        ),
        other_tenant_kw=number(plan, "kw_controlled_by_a_utility_partner_not_this_fleet"),
        undeliverable_kw=number(plan, "kw_that_cannot_be_held_for_the_whole_window"),
        reversible=bool(plan.get("reversible_within_the_event", True)),
        root_cause_confidence=number(plan, "root_cause_confidence"),
        spare_kwh_above_reserve=number(backup, "spare_kwh_above_the_reserve_after_the_plan"),
        backup_reserve_kwh=number(backup, "homeowner_backup_reserve_kwh", 1.0) or 1.0,
        mean_soc=number(backup, "mean_state_of_charge_of_committed", 1.0),
        frequency_hz=number(grid, "frequency_hz", NOMINAL_HZ),
        islanded_homes=int(number(grid, "homes_islanded_on_their_own_battery")),
    )


def situation_from_snapshot(
    snapshot: IncidentSnapshot,
    label: str,
    reversible: bool = True,
    undeliverable_kw: float = 0.0,
    unverified_bidders: int | None = None,
    other_tenant_kw: float = 0.0,
    root_cause_confidence: float = 0.0,
    spare_is_net_of_reserve: bool = False,
) -> Situation:
    """The step described by an incident snapshot, in the judgment layer's terms.

    The Control Room and the agent mesh both build :class:`IncidentSnapshot`, so both
    reach the judgment model through this one door. They differ in one place: the mesh
    already nets the backup reserve out of the spare energy on a capability card, while
    the Control Room reports the whole pack, so ``spare_is_net_of_reserve`` says which
    of the two the caller is holding.
    """
    spare = snapshot.plan_min_spare_kwh
    return Situation(
        label=label,
        dollars=snapshot.dollars_at_risk,
        committed_kw=snapshot.proposed_kw,
        uncovered_kw=snapshot.uncovered_kw,
        spare_kwh_above_reserve=round(
            spare if spare_is_net_of_reserve else spare - snapshot.backup_reserve_kwh, 3
        ),
        backup_reserve_kwh=snapshot.backup_reserve_kwh,
        mean_soc=snapshot.plan_mean_soc,
        bidders=snapshot.bidders,
        unverified_bidders=(
            snapshot.rejected_cards if unverified_bidders is None else unverified_bidders
        ),
        other_tenant_kw=other_tenant_kw,
        undeliverable_kw=undeliverable_kw,
        frequency_hz=snapshot.frequency_hz,
        islanded_homes=snapshot.islanded_agents,
        price_usd_mwh=snapshot.price_usd_mwh,
        reversible=reversible,
        root_cause_confidence=root_cause_confidence,
    )


@dataclass(frozen=True)
class PrincipleView:
    """One row of the per-principle breakdown the Control Room shows."""

    principle: Principle
    satisfied: float
    confidence: float
    weight: float

    @property
    def answer(self) -> str:
        return "yes" if self.satisfied >= 0.5 else "no"

    @property
    def contribution(self) -> float:
        return round(self.weight * self.satisfied, 4)

    def as_dict(self) -> dict[str, object]:
        return {
            "principle": self.principle.id,
            "title": self.principle.title,
            "type": self.principle.type.value,
            "answer": self.answer,
            "satisfied_probability": round(self.satisfied, 4),
            "confidence": round(self.confidence, 4),
            "weight": round(self.weight, 4),
        }


@dataclass(frozen=True)
class Verdict:
    action: Action
    reason: str
    score: float
    bar: float
    breakdown: tuple[PrincipleView, ...]
    vetoed: tuple[str, ...]
    source: Source

    def view(self, principle_id: str) -> PrincipleView:
        for row in self.breakdown:
            if row.principle.id == principle_id:
                return row
        raise KeyError(principle_id)

    def as_dict(self) -> dict[str, object]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "score": round(self.score, 4),
            "bar": round(self.bar, 4),
            "vetoed": list(self.vetoed),
            "jev_source": self.source.value,
            "principles": [row.as_dict() for row in self.breakdown],
        }


def _satisfied(answer: JevAnswer | None) -> tuple[float, float]:
    """Probability the principle is satisfied, and the confidence behind it."""
    if answer is None:
        return 0.0, 0.0
    if answer.probabilities:
        yes = answer.probabilities.get("yes")
        if yes is not None:
            return float(yes), answer.confidence
    if answer.yes is not None:
        return (1.0 if answer.yes else 0.0), answer.confidence
    if answer.score is not None:
        return 1.0 - float(answer.score), answer.confidence
    return 0.0, answer.confidence


def judge(
    response: JevResponse,
    situation: Situation,
    calibration: Calibration | None = None,
    pack: Pack = PACK,
    allow_fallback_to_act: bool = False,
) -> Verdict:
    """Combine the principle answers into act, act-and-notify or ask-a-human.

    ``allow_fallback_to_act`` exists only so the evaluation can score the rules fallback
    as a decision layer in its own right. In the product it is off: with no model the
    fallback answers with zero confidence and every step goes to a human.
    """
    cal = calibration or pack.calibration
    rows: list[PrincipleView] = []
    for principle in pack.principles:
        satisfied, confidence = _satisfied(response.answer(principle.question_id))
        weight = (
            cal.weights.get(principle.id, principle.weight)
            if principle.type is PrincipleType.SOFT
            else 0.0
        )
        rows.append(
            PrincipleView(
                principle=principle, satisfied=satisfied, confidence=confidence, weight=weight
            )
        )

    breakdown = tuple(rows)
    vetoed = tuple(
        row.principle.id
        for row in breakdown
        if row.principle.type is PrincipleType.VETO and row.satisfied < cal.veto_floor
    )
    soft = [row for row in breakdown if row.principle.type is PrincipleType.SOFT]
    total_weight = sum(row.weight for row in soft)
    score = (
        round(sum(row.contribution for row in soft) / total_weight, 4) if total_weight > 0 else 0.0
    )
    act_bar = cal.bar(cal.act_threshold, situation.dollars)
    notify_bar = cal.bar(cal.notify_threshold, situation.dollars)

    offline = response.source is Source.FALLBACK and not allow_fallback_to_act
    if vetoed:
        first = pack.by_id(vetoed[0])
        action = Action.ASK_A_HUMAN
        others = ", ".join(pack.by_id(v).title.lower() for v in vetoed[1:])
        reason = f"Held for a human on {first.title.lower()}: {first.no_means.rstrip('.')}." + (
            f" Also unsafe on {others}." if others else ""
        )
    elif offline:
        action = Action.ASK_A_HUMAN
        reason = (
            "Held for a human: no model answered, so the rules fallback replied with zero "
            "confidence and cannot clear the bar on its own."
        )
    elif score >= act_bar:
        action = Action.ACT
        reason = _soft_reason("Acting", soft, score, act_bar, situation)
    elif score >= notify_bar:
        action = Action.ACT_AND_NOTIFY
        reason = _soft_reason("Acting and telling the operator", soft, score, notify_bar, situation)
    else:
        action = Action.ASK_A_HUMAN
        weakest = min(soft, key=lambda r: r.satisfied) if soft else None
        detail = f" Weakest: {weakest.principle.title.lower()}." if weakest else ""
        reason = (
            f"Held for a human: certainty {score:.0%} is under the {notify_bar:.0%} bar for "
            f"${situation.dollars:,.0f} at stake.{detail}"
        )

    return Verdict(
        action=action,
        reason=reason,
        score=score,
        bar=act_bar,
        breakdown=breakdown,
        vetoed=vetoed,
        source=response.source,
    )


def _soft_reason(
    lead: str, soft: list[PrincipleView], score: float, bar: float, situation: Situation
) -> str:
    """Name the principles that carried the decision, in plain language."""
    ranked = sorted(soft, key=lambda r: -r.contribution)[:2]
    named = " and ".join(r.principle.title.lower() for r in ranked)
    return (
        f"{lead}: every hard principle holds, and certainty {score:.0%} clears the "
        f"{bar:.0%} bar for ${situation.dollars:,.0f} at stake, carried by {named}."
    )
