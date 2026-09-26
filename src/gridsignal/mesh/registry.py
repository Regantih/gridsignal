"""In-memory agent registry: register, publish capabilities, discover, expire.

The registry is the only place that holds the signing key, so an agent whose card
does not verify is recorded as ``rejected`` and never discovered. Heartbeats are
simulated seconds: an agent that stops sending them goes ``stale`` and drops out of
discovery without anyone deleting it, which is what the dashboard shows.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from gridsignal.mesh.cards import AgentCard, AgentKind, CardStatus, Health, new_signing_key

DEFAULT_STALE_AFTER_S = 120


@dataclass
class Registration:
    card: AgentCard
    registered_at_s: int
    last_seen_s: int
    rejected: bool = False
    reason: str = ""


class AgentRegistry:
    """Who is out there, what can they do, and do we still believe them."""

    def __init__(
        self, key: bytes | None = None, stale_after_s: int = DEFAULT_STALE_AFTER_S
    ) -> None:
        self.key = key if key is not None else new_signing_key()
        self.stale_after_s = stale_after_s
        self.now_s = 0
        self._agents: dict[str, Registration] = {}

    # ------------------------------------------------------------------ clock

    def advance(self, seconds: int) -> int:
        self.now_s += seconds
        return self.now_s

    # ------------------------------------------------------------------ writes

    def sign(self, card: AgentCard) -> AgentCard:
        """Sign a card as the agent would, using the mesh key it was issued."""
        return card.signed(self.key)

    def register(self, card: AgentCard) -> CardStatus:
        """Admit an agent, or reject it if its card does not verify."""
        if not card.verifies(self.key):
            self._agents[card.agent_id] = Registration(
                card=card,
                registered_at_s=self.now_s,
                last_seen_s=self.now_s,
                rejected=True,
                reason="card signature does not verify",
            )
            return CardStatus.REJECTED
        self._agents[card.agent_id] = Registration(
            card=card, registered_at_s=self.now_s, last_seen_s=self.now_s
        )
        return CardStatus.VERIFIED

    def publish(self, card: AgentCard) -> CardStatus:
        """Re-publish a card (new capabilities or health) and re-check the signature."""
        return self.register(card)

    def heartbeat(self, agent_id: str) -> None:
        registration = self._agents.get(agent_id)
        if registration is None:
            return
        registration.last_seen_s = self.now_s
        registration.card = replace(registration.card, last_heartbeat_s=self.now_s)
        if not registration.rejected:
            registration.card = self.sign(registration.card)

    def set_health(self, agent_id: str, health: Health) -> None:
        registration = self._agents.get(agent_id)
        if registration is None:
            return
        registration.card = self.sign(replace(registration.card, health=health))

    def tamper(self, agent_id: str, **capabilities: float) -> None:
        """Simulate a lying agent: edit the card after signing, leaving the old signature."""
        registration = self._agents.get(agent_id)
        if registration is None:
            return
        lied = replace(
            registration.card, capabilities={**registration.card.capabilities, **capabilities}
        )
        self.publish(lied)

    # ------------------------------------------------------------------ reads

    def status(self, agent_id: str) -> CardStatus:
        registration = self._agents[agent_id]
        if registration.rejected:
            return CardStatus.REJECTED
        if self.now_s - registration.last_seen_s > self.stale_after_s:
            return CardStatus.STALE
        return CardStatus.VERIFIED

    def card(self, agent_id: str) -> AgentCard:
        return self._agents[agent_id].card

    def agent_ids(self) -> list[str]:
        return list(self._agents)

    def __len__(self) -> int:
        return len(self._agents)

    def statuses(self) -> dict[str, CardStatus]:
        return {agent_id: self.status(agent_id) for agent_id in self._agents}

    def discover(
        self,
        capability: str,
        minimum: float = 0.0,
        kind: AgentKind | None = None,
        zone: str | None = None,
    ) -> list[AgentCard]:
        """Verified, live agents that can do at least ``minimum`` of ``capability``."""
        found = [
            registration.card
            for agent_id, registration in self._agents.items()
            if self.status(agent_id) is CardStatus.VERIFIED
            and registration.card.health is not Health.OFFLINE
            and registration.card.capability(capability) >= minimum
            and (kind is None or registration.card.kind is kind)
            and (zone is None or registration.card.zone == zone)
        ]
        return sorted(found, key=lambda c: c.agent_id)
