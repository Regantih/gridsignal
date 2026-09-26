"""Prove a battery can hold an award for the whole window before it is committed.

A bid is a claim about one instant. An award is a promise about a window: the battery
must sustain the awarded kW for every hour of the event, on top of whatever it already
owes another award, without touching the member's backup reserve. Those are different
questions, and a contract net that only checks the bid answers the wrong one.

Every award, energy or ancillary, passes :func:`check` before it is proposed and again
before it is executed, because state drifts while a human is deciding: a card goes
stale during the approval delay, an agent degrades, a battery wins a second call. The
verdict either clears the award, trims it to what the battery can hold, or rejects it
with the reason recorded in the trace.

The constraints, in the order they bind:

* **Provable state** — a stale or rejected card cannot prove anything, so it delivers 0.
* **Energy** — spare kWh above the member reserve, spread across the award window.
* **Power** — free inverter kW, derated on a degraded agent. A card that publishes the
  ``power_derate`` it already applied to ``kw_available`` is taken at its word, so a
  degraded battery is never derated twice; a card that declares none is derated here by
  :data:`DEGRADED_DERATE`, a documented modelling assumption, not a measured derate.
* **Other awards** — kW already committed to earlier calls come off both limits.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from gridsignal.mesh.cards import AgentCard, CardStatus, Health

#: A degraded inverter is only trusted for half its nameplate power when the mesh is
#: deciding what it can promise. Modelling assumption, labelled as one.
DEGRADED_DERATE = 0.5
#: Awards smaller than this are not worth dispatching.
MIN_AWARD_KW = 0.05


@dataclass(frozen=True)
class Verdict:
    """What one battery can actually hold for the whole window, and why."""

    agent_id: str
    requested_kw: float
    deliverable_kw: float
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.deliverable_kw >= self.requested_kw - 1e-9

    @property
    def rejected(self) -> bool:
        return self.deliverable_kw < MIN_AWARD_KW

    @property
    def trimmed_kw(self) -> float:
        return round(max(self.requested_kw - self.deliverable_kw, 0.0), 3)


def power_derate(card: AgentCard) -> float:
    """How much more of the published inverter power to discount before promising it.

    A card built from fleet state declares the derate it already applied, so nothing is
    taken off twice; a card that declares none is derated here if it is degraded.
    """
    if "power_derate" in card.capabilities:
        return 1.0
    return DEGRADED_DERATE if card.health is Health.DEGRADED else 1.0


def _floor_kw(kw: float) -> float:
    """Round down: rounding to nearest would let an award dip into the reserve."""
    return math.floor(max(kw, 0.0) * 1000) / 1000


def check(
    card: AgentCard,
    status: CardStatus,
    hours: float,
    requested_kw: float,
    committed_kw: float = 0.0,
    reserve_kwh: float = 0.0,
) -> Verdict:
    """Can this agent hold ``requested_kw`` for ``hours``, on top of its commitments?"""
    agent_id = card.agent_id
    requested = round(max(requested_kw, 0.0), 3)

    if status is not CardStatus.VERIFIED:
        return Verdict(
            agent_id, requested, 0.0, f"card is {status.value}: state of charge unproven"
        )
    if card.health is Health.OFFLINE:
        return Verdict(agent_id, requested, 0.0, "agent is offline")

    spare_kwh = max(card.capability("kwh_available") - reserve_kwh, 0.0)
    energy_kw = _floor_kw(spare_kwh / max(hours, 1e-6) - committed_kw)
    derate = power_derate(card)
    power_kw = _floor_kw(card.capability("kw_available") * derate - committed_kw)
    deliverable = min(energy_kw, power_kw)

    if deliverable >= requested - 1e-9:
        return Verdict(agent_id, requested, requested)
    if energy_kw <= power_kw:
        reason = (
            f"{spare_kwh:.2f} kWh spare above the member reserve holds only "
            f"{energy_kw:.2f} kW for {hours:.2f} h"
        )
    else:
        derated = " (degraded, derated)" if derate != 1.0 else ""
        reason = (
            f"inverter has {power_kw:.2f} kW free{derated} after {committed_kw:.2f} kW "
            "already committed"
        )
    return Verdict(agent_id, requested, max(deliverable, 0.0), reason)
