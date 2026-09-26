"""Learning the judgment model's soft weights from **simulated** operator overrides.

There is no log of real Base operators here and none is claimed. What there is:

* **The episodes are real simulation states.** Each one is a Control Room run at a
  different fleet size, price day, backup-reserve floor and stale-telemetry share, with
  the same incident opened and the same recovery plan built as in the product.
* **The operator is simulated.** A documented rule plays the part of a careful operator
  and either lets the plan stand, lets it stand with a note, or overrides it with a
  written reason (the same required-reason override the Control Room logs). The rule is
  written in :func:`simulated_operator` and is deliberately *not* the judgment policy:
  it reads raw fleet numbers, not principle answers, and it carries a documented
  conservatism quirk on scarcity-priced days.
* **Only weights and thresholds move.** Calibration is a small grid search on the
  training half of the episodes over the three soft-principle weights and the two
  certainty bars. The hard vetoes, the questions and the shape of the policy are fixed.
* **Scoring is on a held-out half** the search never sees, reported before and after.

So the honest claim is narrow: the calibration recovers weights that agree better with
*this simulated operator* on episodes it was not fitted to. It says nothing about real
operators.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import product

from gridsignal.control_room.engine import ControlRoomEngine
from gridsignal.jev import incident as incident_module
from gridsignal.jev.client import JevResponse
from gridsignal.jev.judgment import (
    CLEAN_DIAGNOSIS_CONFIDENCE,
    MUDDIED_DIAGNOSIS_CONFIDENCE,
    PACK,
    Action,
    Calibration,
    Situation,
    judge,
    situation_from_snapshot,
)
from gridsignal.prices import load_scenario as load_price_scenario

#: Fleet sizes, price days and operating conditions the episodes are drawn from.
FLEET_SIZES: tuple[int, ...] = (48, 1000, 10000)
PRICE_DAYS: tuple[str, ...] = ("normal", "scarcity")
RESERVE_FRACTIONS: tuple[float, ...] = (0.2, 0.8)
STALE_SHARES: tuple[float, ...] = (0.0, 0.12)

#: What else the operator was handed that day. Each twist is a labelled simulation of a
#: condition the Control Room can really produce, applied to the plan it really built.
TWISTS: tuple[str, ...] = ("clean", "spoofed_cards", "other_tenant", "undeliverable")

#: Where the simulated operator's patience runs out. Documented, not fitted.
OPERATOR_RESERVE_MARGIN = 0.05
OPERATOR_NOTIFY_DOLLARS = 150.0
OPERATOR_ASK_DOLLARS = 1200.0
SCARCITY_USD_MWH = 200.0


@dataclass(frozen=True)
class Episode:
    """One decision an operator was asked to sign off on, and what they did."""

    episode_id: str
    condition: str
    situation: Situation
    operator: Action
    reason: str
    simulated: bool = True

    @property
    def overridden(self) -> bool:
        """True when the operator did not simply let the plan run."""
        return self.operator is not Action.ACT

    def as_dict(self) -> dict[str, object]:
        return {
            "episode_id": self.episode_id,
            "operator_action": self.operator.value,
            "reason": self.reason,
            "simulated": self.simulated,
            "dollars": round(self.situation.dollars, 2),
            "reserve_margin": self.situation.reserve_margin,
            "uncovered_kw": round(self.situation.uncovered_kw, 2),
        }


def simulated_operator(situation: Situation) -> tuple[Action, str]:
    """The stand-in operator: a documented rule over raw fleet numbers.

    Reading order matches how the principles are prioritised, but the thresholds are
    the operator's own and the judgment model never sees them.
    """
    if situation.reserve_margin < OPERATOR_RESERVE_MARGIN:
        return (
            Action.ASK_A_HUMAN,
            "the plan leaves nothing over a member's backup reserve; I want to see it first",
        )
    if situation.unverified_bidders or situation.other_tenant_kw > 0:
        return (
            Action.ASK_A_HUMAN,
            "a bidder did not verify or is not ours to dispatch",
        )
    if situation.undeliverable_kw > 0 or situation.uncovered_kw > 0:
        return (
            Action.ASK_A_HUMAN,
            "the plan leaves a gap or commits kW we cannot hold for the window",
        )
    if situation.dollars > OPERATOR_ASK_DOLLARS:
        return (
            Action.ASK_A_HUMAN,
            f"${situation.dollars:,.0f} is more money than I sign off blind",
        )
    if situation.price_usd_mwh >= SCARCITY_USD_MWH:
        # The documented quirk: on a scarcity-priced day this operator wants to be told
        # about every step, even a clean one.
        return (
            Action.ACT_AND_NOTIFY,
            "scarcity pricing: run it, but tell me what you did",
        )
    if situation.dollars > OPERATOR_NOTIFY_DOLLARS or situation.mean_soc < 0.5:
        return (
            Action.ACT_AND_NOTIFY,
            "fine to run, but it is either real money or low state of charge",
        )
    return Action.ACT, "routine: the plan holds reserve and covers the gap"


def _twist(situation: Situation, twist: str) -> Situation:
    """Apply one labelled complication to the plan the engine built."""
    if twist == "spoofed_cards":
        return replace(situation, unverified_bidders=2)
    if twist == "other_tenant":
        return replace(situation, other_tenant_kw=round(0.1 * situation.committed_kw, 2))
    if twist == "undeliverable":
        return replace(
            situation,
            undeliverable_kw=round(0.2 * situation.committed_kw, 2),
            uncovered_kw=round(0.2 * situation.committed_kw, 2),
        )
    return situation


def _episode(engine: ControlRoomEngine, episode_id: str, stale_share: float) -> Situation:
    incident = engine.trigger_device_failure()
    if stale_share > 0:
        engine.inject_stale_telemetry(stale_share)
    snapshot = incident_module.snapshot_from_engine(engine, incident)
    situation = situation_from_snapshot(
        snapshot,
        label=episode_id,
        root_cause_confidence=(
            MUDDIED_DIAGNOSIS_CONFIDENCE if stale_share > 0 else CLEAN_DIAGNOSIS_CONFIDENCE
        ),
    )
    return situation


def episodes() -> list[Episode]:
    """The simulated override log: one episode per operating condition, deterministic."""
    log: list[Episode] = []
    for size, day, fraction, stale in product(
        FLEET_SIZES, PRICE_DAYS, RESERVE_FRACTIONS, STALE_SHARES
    ):
        engine = ControlRoomEngine(price_trace=load_price_scenario(day), fleet_size=size)
        engine.set_reserve_floor(fraction)
        base_id = f"{day}-{size}-r{int(fraction * 100)}-s{int(stale * 100)}"
        base = _episode(engine, base_id, stale)
        for twist in TWISTS:
            episode_id = f"{base_id}-{twist}"
            situation = replace(_twist(base, twist), label=episode_id)
            action, reason = simulated_operator(situation)
            log.append(
                Episode(
                    episode_id=episode_id,
                    condition=base_id,
                    situation=situation,
                    operator=action,
                    reason=reason,
                )
            )
    return log


def split(log: list[Episode]) -> tuple[list[Episode], list[Episode]]:
    """Alternating split by operating condition, keeping a condition's twists together.

    Splitting episode by episode would put the twists of one fleet state on both sides
    of the line, which both leaks and starves each half of whole twist types; whole
    conditions alternate instead, so each half sees every twist and neither half sees
    the other's fleet states.
    """
    order: list[str] = []
    for episode in log:
        if episode.condition not in order:
            order.append(episode.condition)
    held_out = {name for i, name in enumerate(order) if i % 2 == 1}
    train = [e for e in log if e.condition not in held_out]
    holdout = [e for e in log if e.condition in held_out]
    return train, holdout


#: The grid the calibration is allowed to search: three soft weights and two bars.
WEIGHT_GRID: tuple[float, ...] = (0.15, 0.30, 0.45, 0.60)
ACT_GRID: tuple[float, ...] = (0.60, 0.70, 0.80, 0.90)
NOTIFY_GRID: tuple[float, ...] = (0.20, 0.30, 0.40, 0.50, 0.60)


def agreement(
    log: list[Episode],
    answers: dict[str, JevResponse],
    calibration: Calibration,
) -> float:
    """Share of episodes where the model's verdict is the operator's own decision."""
    if not log:
        return 0.0
    hits = sum(
        1
        for episode in log
        if judge(
            answers[episode.episode_id],
            episode.situation,
            calibration=calibration,
            allow_fallback_to_act=True,
        ).action
        is episode.operator
    )
    return round(hits / len(log), 4)


def calibrate(
    train: list[Episode],
    answers: dict[str, JevResponse],
    start: Calibration | None = None,
) -> Calibration:
    """Grid-search the soft weights and the two bars for agreement on the training half.

    Ties are broken towards the starting calibration, so the search only moves a number
    when the evidence actually asks it to.
    """
    base = start or PACK.calibration
    soft_ids = [p.id for p in PACK.softs]
    best = base
    best_score = agreement(train, answers, base)
    for weights in product(WEIGHT_GRID, repeat=len(soft_ids)):
        for act, notify in product(ACT_GRID, NOTIFY_GRID):
            if notify >= act:
                continue
            candidate = replace(
                base,
                weights=dict(zip(soft_ids, weights, strict=True)),
                act_threshold=act,
                notify_threshold=notify,
            )
            score = agreement(train, answers, candidate)
            if score > best_score:
                best, best_score = candidate, score
    return best
