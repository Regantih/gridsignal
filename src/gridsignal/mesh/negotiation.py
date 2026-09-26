"""Contract-net negotiation: call for capacity, bid, award — behind a human gate.

The protocol follows the classic contract net (announce, bid, award), in the spirit of
the agent-town simulations at https://nandatown.projectnanda.org, implemented here from
scratch against this repo's simulated fleet.

Two properties matter more than the protocol itself:

* **Nothing executes without a human.** ``propose()`` only produces a plan; the awarded
  kW is applied by ``approve(call_id, approver)`` and nowhere else.
* **Awards are idempotent.** Re-triggering the same call, or a double-click on approve,
  returns the award already on file instead of committing the capacity twice.
* **A distrusted bid is dropped, not just flagged.** ``revise()`` removes the bidders a
  judgement layer distrusts and recomputes the award before anyone approves it, so the
  verdict changes who gets the kW rather than only annotating the log.
"""

from __future__ import annotations

import math
from collections.abc import Collection
from dataclasses import dataclass, field

from gridsignal.mesh.cards import AgentKind, CardStatus, Health
from gridsignal.mesh.messages import MessageBus, MessageKind
from gridsignal.mesh.registry import AgentRegistry

# A homeowner keeps this much stored energy for their own backup before the fleet may
# bid their battery into a grid event. Assumption, not a Base Power tariff.
BACKUP_RESERVE_KWH = 4.0
# Modelled battery wear, in dollars per kW of extra commitment. Assumption.
WEAR_USD_PER_KW = 0.010
# Extra cents a battery asks for when its state of charge is low: the emptier it is,
# the more the homeowner's backup is worth defending.
BACKUP_PREMIUM_USD_PER_KW = 0.040
# A degraded agent is allowed to bid, but it prices itself out of the cheap tier.
DEGRADED_PREMIUM_USD_PER_KW = 0.020
MIN_BID_KW = 0.05


class ApprovalRequired(RuntimeError):
    """Raised when an award is executed without a recorded human approval."""


@dataclass(frozen=True)
class CallForCapacity:
    call_id: str
    gap_kw: float
    hours: float
    issued_at_s: int
    zone: str | None = None
    excluded: tuple[str, ...] = ()


@dataclass(frozen=True)
class Bid:
    call_id: str
    agent_id: str
    kw: float
    soc: float
    price_usd_per_kw: float

    @property
    def cost_usd(self) -> float:
        return round(self.kw * self.price_usd_per_kw, 4)


@dataclass(frozen=True)
class Award:
    agent_id: str
    kw: float
    price_usd_per_kw: float

    @property
    def cost_usd(self) -> float:
        return round(self.kw * self.price_usd_per_kw, 4)


@dataclass
class AwardSet:
    call_id: str
    gap_kw: float
    awards: list[Award] = field(default_factory=list)
    escalated: bool = False
    approved_by: str | None = None
    executed: bool = False
    #: Bidders dropped by :meth:`Coordinator.revise` before this award was approved.
    dropped: tuple[str, ...] = ()
    #: What the award covered before those bidders were dropped, if any were.
    covered_kw_before_revision: float | None = None

    @property
    def covered_kw(self) -> float:
        return round(sum(a.kw for a in self.awards), 2)

    @property
    def uncovered_kw(self) -> float:
        return round(max(self.gap_kw - self.covered_kw, 0.0), 2)

    @property
    def cost_usd(self) -> float:
        return round(sum(a.cost_usd for a in self.awards), 4)

    @property
    def coverage_pct(self) -> float:
        if self.gap_kw <= 0:
            return 100.0
        return round(100.0 * self.covered_kw / self.gap_kw, 1)


def bid_price_usd_per_kw(soc: float, health: Health) -> float:
    """Wear, plus a premium that grows as the homeowner's backup gets thinner."""
    scarcity = min(max(1.0 - soc, 0.0), 1.0)
    premium = DEGRADED_PREMIUM_USD_PER_KW if health is Health.DEGRADED else 0.0
    return round(WEAR_USD_PER_KW + BACKUP_PREMIUM_USD_PER_KW * scarcity + premium, 6)


class Coordinator:
    """The agent that notices a shortfall and runs the auction to close it."""

    agent_id = "coordinator-01"

    def __init__(self, registry: AgentRegistry, bus: MessageBus, controller: str = "base") -> None:
        self.registry = registry
        self.bus = bus
        # The tenant this coordinator speaks for; cards naming anyone else are off limits.
        self.controller = controller
        self.calls: dict[str, CallForCapacity] = {}
        self.awards: dict[str, AwardSet] = {}
        # Executed kW per agent, the ledger that makes double approval a no-op.
        self.commitments: dict[str, float] = {}
        self._call_seq = 0

    # ------------------------------------------------------------------ call

    def call_for_capacity(
        self,
        gap_kw: float,
        hours: float,
        zone: str | None = None,
        exclude: tuple[str, ...] = (),
        call_id: str | None = None,
    ) -> CallForCapacity:
        """Broadcast a shortfall. Reusing a ``call_id`` returns the existing call."""
        if call_id is not None and call_id in self.calls:
            return self.calls[call_id]
        if call_id is None:
            self._call_seq += 1
            call_id = f"CFC-{self._call_seq:03d}"
        call = CallForCapacity(
            call_id=call_id,
            gap_kw=round(gap_kw, 2),
            hours=hours,
            issued_at_s=self.registry.now_s,
            zone=zone,
            excluded=tuple(exclude),
        )
        self.calls[call_id] = call
        self.bus.send(
            self.registry.now_s,
            MessageKind.CALL_FOR_CAPACITY,
            self.agent_id,
            "broadcast",
            f"Call for {call.gap_kw:.1f} kW over {hours:.2f} h",
            call_id=call_id,
            gap_kw=call.gap_kw,
            hours=round(hours, 4),
            zone=zone,
        )
        return call

    # ------------------------------------------------------------------ bids

    def collect_bids(self, call: CallForCapacity, log_each: bool = True) -> list[Bid]:
        """Every verified, live battery agent answers with what it can spare."""
        bids: list[Bid] = []
        for card in self.registry.discover(
            "kw_available", minimum=MIN_BID_KW, kind=AgentKind.BATTERY, zone=call.zone
        ):
            if card.agent_id in call.excluded:
                continue
            if card.controller != self.controller:
                # Another tenant's battery: never bid, awarded or reassigned here,
                # even when it is healthy and sitting on spare kW.
                continue
            spare_kwh = max(card.capability("kwh_available") - BACKUP_RESERVE_KWH, 0.0)
            # Round the bid *down*: rounding to the nearest milliwatt would let a bid
            # dip a fraction of a kWh into the homeowner's reserve.
            room_kw = spare_kwh / max(call.hours, 1e-6)
            kw = math.floor(min(card.capability("kw_available"), room_kw) * 1000) / 1000
            if kw < MIN_BID_KW:
                continue
            bid = Bid(
                call_id=call.call_id,
                agent_id=card.agent_id,
                kw=kw,
                soc=round(card.capability("soc"), 4),
                price_usd_per_kw=bid_price_usd_per_kw(card.capability("soc"), card.health),
            )
            bids.append(bid)
            if log_each:
                self.bus.send(
                    self.registry.now_s,
                    MessageKind.BID,
                    card.agent_id,
                    self.agent_id,
                    f"{card.agent_id} bids {kw:.2f} kW at ${bid.price_usd_per_kw:.3f}/kW",
                    call_id=call.call_id,
                    kw=kw,
                    soc=bid.soc,
                    price_usd_per_kw=bid.price_usd_per_kw,
                )
        if not log_each:
            self.bus.send(
                self.registry.now_s,
                MessageKind.BID,
                "battery-agents",
                self.agent_id,
                f"{len(bids)} bids received for {call.call_id}",
                call_id=call.call_id,
                bids=len(bids),
            )
        return bids

    # ------------------------------------------------------------------ award

    def rank(self, call: CallForCapacity, bids: list[Bid]) -> list[Bid]:
        """Cheapest first, then biggest, then by id so ties never depend on ordering."""
        return sorted(bids, key=lambda b: (b.price_usd_per_kw, -b.kw, b.agent_id))

    def propose(self, call: CallForCapacity, bids: list[Bid]) -> AwardSet:
        """Award the cheapest set that covers the gap; cover partially and escalate."""
        if call.call_id in self.awards:
            return self.awards[call.call_id]

        award_set = AwardSet(call_id=call.call_id, gap_kw=call.gap_kw)
        remaining = call.gap_kw
        for bid in self.rank(call, bids):
            if remaining <= 1e-9:
                break
            take = round(min(bid.kw, remaining), 3)
            award_set.awards.append(
                Award(agent_id=bid.agent_id, kw=take, price_usd_per_kw=bid.price_usd_per_kw)
            )
            remaining = round(remaining - take, 6)

        award_set.escalated = award_set.uncovered_kw > 0.01
        self.awards[call.call_id] = award_set
        self.bus.send(
            self.registry.now_s,
            MessageKind.AWARD_PROPOSED,
            self.agent_id,
            "fleet-operator",
            (
                f"Proposed {award_set.covered_kw:.1f} kW from {len(award_set.awards)} agents "
                f"({award_set.coverage_pct:.0f}% of the gap) — awaiting human approval"
            ),
            call_id=call.call_id,
            covered_kw=award_set.covered_kw,
            uncovered_kw=award_set.uncovered_kw,
            agents=len(award_set.awards),
            cost_usd=award_set.cost_usd,
        )
        if award_set.escalated:
            self.bus.send(
                self.registry.now_s,
                MessageKind.ESCALATION,
                self.agent_id,
                "fleet-operator",
                (
                    f"Remaining headroom cannot cover {award_set.uncovered_kw:.1f} kW — "
                    "covering partially and escalating to a human"
                ),
                call_id=call.call_id,
                uncovered_kw=award_set.uncovered_kw,
            )
        return award_set

    def revise(
        self, call: CallForCapacity, bids: list[Bid], distrusted: Collection[str]
    ) -> AwardSet:
        """Recompute the award without the bidders a judgement layer distrusts.

        A valid signature only proves a card was not edited in flight; it says nothing
        about an agent that signs an inflated capability honestly. Those bids are removed
        here and the auction re-run, before the approval gate sees the plan.
        """
        award_set = self.awards.get(call.call_id)
        if award_set is None or award_set.executed:
            raise ApprovalRequired(f"nothing revisable for {call.call_id}")
        drop = {a for a in distrusted}
        if not drop & {a.agent_id for a in award_set.awards}:
            return award_set

        before_kw = award_set.covered_kw
        before_agents = len(award_set.awards)
        del self.awards[call.call_id]
        revised = self.propose(call, [b for b in bids if b.agent_id not in drop])
        revised.dropped = tuple(sorted(drop))
        revised.covered_kw_before_revision = before_kw
        self.bus.send(
            self.registry.now_s,
            MessageKind.AWARD_REVISED,
            self.agent_id,
            "fleet-operator",
            (
                f"Dropped {len(drop)} distrusted bidder(s) and re-ran the auction: "
                f"{before_kw:.1f} kW from {before_agents} agents -> "
                f"{revised.covered_kw:.1f} kW from {len(revised.awards)} agents"
            ),
            call_id=call.call_id,
            dropped=sorted(drop),
            covered_kw_before=before_kw,
            covered_kw_after=revised.covered_kw,
            agents_before=before_agents,
            agents_after=len(revised.awards),
        )
        return revised

    def approve(self, call_id: str, approver: str) -> AwardSet:
        """The gate: awarded capacity is committed here and only here."""
        award_set = self.awards.get(call_id)
        if award_set is None:
            raise ApprovalRequired(f"no award proposed for {call_id}")
        if award_set.executed:
            self.bus.send(
                self.registry.now_s,
                MessageKind.AWARD_IGNORED,
                approver,
                self.agent_id,
                f"{call_id} was already executed — approval ignored, nothing double-counted",
                call_id=call_id,
                covered_kw=award_set.covered_kw,
            )
            return award_set

        award_set.approved_by = approver
        self.bus.send(
            self.registry.now_s,
            MessageKind.APPROVAL,
            approver,
            self.agent_id,
            f"Human approved {call_id}: {award_set.covered_kw:.1f} kW",
            call_id=call_id,
            approver=approver,
        )
        for award in award_set.awards:
            self.commitments[award.agent_id] = round(
                self.commitments.get(award.agent_id, 0.0) + award.kw, 3
            )
        award_set.executed = True
        self.bus.send(
            self.registry.now_s,
            MessageKind.AWARD_EXECUTED,
            self.agent_id,
            "fleet",
            (
                f"{call_id} executed: {award_set.covered_kw:.1f} kW committed across "
                f"{len(award_set.awards)} agents for ${award_set.cost_usd:,.2f}"
            ),
            call_id=call_id,
            covered_kw=award_set.covered_kw,
            cost_usd=award_set.cost_usd,
        )
        return award_set

    def execute(self, call_id: str) -> AwardSet:
        """Guard rail: there is no path to commitment that skips the human."""
        award_set = self.awards.get(call_id)
        if award_set is None or award_set.approved_by is None:
            raise ApprovalRequired(f"{call_id} has no recorded human approval")
        return award_set

    # ------------------------------------------------------------------ delivery

    def defaulters(self, award_set: AwardSet) -> list[str]:
        """Awardees that stopped answering after accepting the award."""
        silent: list[str] = []
        for award in award_set.awards:
            if self.registry.status(award.agent_id) is not CardStatus.VERIFIED:
                silent.append(award.agent_id)
                self.bus.send(
                    self.registry.now_s,
                    MessageKind.HEARTBEAT_LOST,
                    award.agent_id,
                    self.agent_id,
                    f"{award.agent_id} accepted {award.kw:.2f} kW then went silent",
                    call_id=award_set.call_id,
                    kw=award.kw,
                )
        return silent

    def total_committed_kw(self) -> float:
        return round(sum(self.commitments.values()), 2)
