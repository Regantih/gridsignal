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
import json
import secrets
from dataclasses import dataclass, field, replace
from enum import StrEnum


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
    signature: str = ""

    def body(self) -> dict[str, object]:
        """Everything the signature covers."""
        return {
            "agent_id": self.agent_id,
            "kind": self.kind.value,
            "zone": self.zone,
            "capabilities": {k: round(float(v), 4) for k, v in sorted(self.capabilities.items())},
            "health": self.health.value,
            "last_heartbeat_s": self.last_heartbeat_s,
        }

    def canonical(self) -> str:
        return json.dumps(self.body(), sort_keys=True, separators=(",", ":"))

    def signed(self, key: bytes) -> AgentCard:
        return replace(self, signature=sign(self, key))

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
) -> AgentCard:
    """The card a home battery agent publishes."""
    return AgentCard(
        agent_id=agent_id,
        kind=AgentKind.BATTERY,
        zone=zone,
        capabilities={"kw_available": kw_available, "kwh_available": kwh_available},
        health=health,
        last_heartbeat_s=last_heartbeat_s,
    )
