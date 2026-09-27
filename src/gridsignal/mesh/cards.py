"""AgentFacts-style capability cards, signed so a peer can be rejected.

Inspired by MIT Project NANDA's AgentFacts idea (https://nandatown.projectnanda.org):
an agent publishes a small, verifiable card describing who it is and what it can do.
This is an independent implementation — nothing is vendored from that project — and it
is a simulation: no card here describes a real device.

The signature is an HMAC over the canonical JSON of the card body, so any field a
misbehaving agent edits after signing (a battery claiming 50 kW it does not have)
fails verification and the registry refuses to hand it out to the coordinator.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from enum import StrEnum

#: Separators used when a card is serialised for signing. Control characters, so they
#: cannot occur in an agent id, a zone, a controller or a capability name.
RECORD_SEP = "\x1e"
UNIT_SEP = "\x1f"


class AgentKind(StrEnum):
    BATTERY = "battery"
    GATEWAY = "gateway"
    ZONE = "zone"
    COORDINATOR = "coordinator"


class Health(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class CardStatus(StrEnum):
    """What the registry thinks of a card right now."""

    VERIFIED = "verified"
    STALE = "stale"
    REJECTED = "rejected"


def new_signing_key() -> bytes:
    """A fresh HMAC key for one process. Never persisted, never a real credential."""
    return secrets.token_bytes(32)


def derived_signing_key(seed: int) -> bytes:
    """A reproducible key so a seeded scenario replays byte-for-byte."""
    return hashlib.sha256(f"gridsignal-mesh-{seed}".encode()).digest()


@dataclass(frozen=True)
class AgentCard:
    """What an agent publishes about itself."""

    agent_id: str
    kind: AgentKind
    zone: str
    capabilities: dict[str, float] = field(default_factory=dict)
    health: Health = Health.HEALTHY
    last_heartbeat_s: int = 0
    #: Who is allowed to dispatch this agent. A card names its control authority so
    #: the mesh can never bid, award or reassign a battery belonging to another
    #: tenant. Simulated tenancy, signed like every other field.
    controller: str = "base"
    signature: str = ""

    def canonical(self) -> str:
        """Exactly the bytes the signature covers: every field, in a fixed order.

        Fields and capabilities are joined with unit and record separators, which
        cannot appear in an agent id, a zone or a controller name, so no value can be
        shifted into a neighbouring field without changing the signature. Capabilities
        are sorted and fixed to four decimals so the same card always signs the same.
        """
        caps = UNIT_SEP.join(
            f"{name}={float(value):.4f}" for name, value in sorted(self.capabilities.items())
        )
        return RECORD_SEP.join(
            (
                self.agent_id,
                self.kind.value,
                self.zone,
                caps,
                self.health.value,
                str(self.last_heartbeat_s),
                self.controller,
            )
        )

    def signed(self, key: bytes) -> AgentCard:
        # Field by field rather than dataclasses.replace: this runs once per card per
        # heartbeat, which is millions of calls at 100,000 agents.
        return AgentCard(
            agent_id=self.agent_id,
            kind=self.kind,
            zone=self.zone,
            capabilities=self.capabilities,
            health=self.health,
            last_heartbeat_s=self.last_heartbeat_s,
            controller=self.controller,
            signature=sign(self, key),
        )

    def verifies(self, key: bytes) -> bool:
        return hmac.compare_digest(self.signature, sign(self, key))

    def capability(self, name: str) -> float:
        return float(self.capabilities.get(name, 0.0))


def sign(card: AgentCard, key: bytes) -> str:
    return hmac.new(key, card.canonical().encode(), hashlib.sha256).hexdigest()


def battery_card(
    agent_id: str,
    zone: str,
    kw_available: float,
    kwh_available: float,
    health: Health = Health.HEALTHY,
    last_heartbeat_s: int = 0,
    controller: str = "base",
) -> AgentCard:
    """The card a home battery agent publishes."""
    return AgentCard(
        agent_id=agent_id,
        kind=AgentKind.BATTERY,
        zone=zone,
        capabilities={"kw_available": kw_available, "kwh_available": kwh_available},
        health=health,
        last_heartbeat_s=last_heartbeat_s,
        controller=controller,
    )
