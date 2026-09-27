"""Optional LLM coordinator, off by default and keyless.

The mesh must stay deterministic for the demo and for CI, so the shipped coordinator
is pure arithmetic. This subclass exists so an LLM can be dropped in behind an explicit
flag without touching the protocol: it is given the bids already gathered and returns a
ranking. With no provider wired up it says so and defers to the deterministic ranking —
it never calls out to a network and never needs an API key.
"""

from __future__ import annotations

from collections.abc import Callable

from gridsignal.mesh.messages import MessageBus, MessageKind
from gridsignal.mesh.negotiation import Bid, CallForCapacity, Coordinator
from gridsignal.mesh.registry import AgentRegistry

Ranker = Callable[[CallForCapacity, list[Bid]], list[Bid]]


class LLMCoordinator(Coordinator):
    """A coordinator that may consult a language model to rank bids."""

    agent_id = "coordinator-llm"

    def __init__(
        self,
        registry: AgentRegistry,
        bus: MessageBus,
        enabled: bool = False,
        ranker: Ranker | None = None,
        check_deliverability: bool = True,
    ) -> None:
        super().__init__(registry, bus, check_deliverability=check_deliverability)
        self.enabled = enabled
        self.ranker = ranker

    def rank(self, call: CallForCapacity, bids: list[Bid]) -> list[Bid]:
        if not self.enabled:
            return super().rank(call, bids)
        if self.ranker is None:
            self.bus.send(
                self.registry.now_s,
                MessageKind.ESCALATION,
                self.agent_id,
                "fleet-operator",
                "LLM coordinator enabled with no provider — using the deterministic ranking",
                call_id=call.call_id,
            )
            return super().rank(call, bids)
        return self.ranker(call, bids)
