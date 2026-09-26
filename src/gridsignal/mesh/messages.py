"""The message bus the agents talk over, and the JSONL trace it writes.

Every negotiation step is a message, so a scenario run is fully explainable after the
fact: call -> bids -> proposed award -> human approval -> executed award -> escalation.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path


class MessageKind(StrEnum):
    REGISTER = "register"
    REJECT = "reject"
    HEARTBEAT_LOST = "heartbeat_lost"
    CALL_FOR_CAPACITY = "call_for_capacity"
    BID = "bid"
    AWARD_PROPOSED = "award_proposed"
    APPROVAL = "approval"
    AWARD_EXECUTED = "award_executed"
    AWARD_IGNORED = "award_ignored"
    ESCALATION = "escalation"
    METRICS = "metrics"


@dataclass(frozen=True)
class Message:
    t_s: int
    kind: MessageKind
    sender: str
    recipient: str
    summary: str
    payload: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        record = asdict(self)
        record["kind"] = self.kind.value
        return record


class MessageBus:
    """Append-only log of agent traffic."""

    def __init__(self) -> None:
        self.messages: list[Message] = []

    def send(
        self,
        t_s: int,
        kind: MessageKind,
        sender: str,
        recipient: str,
        summary: str,
        **payload: object,
    ) -> Message:
        message = Message(
            t_s=t_s, kind=kind, sender=sender, recipient=recipient, summary=summary, payload=payload
        )
        self.messages.append(message)
        return message

    def __len__(self) -> int:
        return len(self.messages)

    def of_kind(self, kind: MessageKind) -> list[Message]:
        return [m for m in self.messages if m.kind is kind]

    def write_jsonl(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for message in self.messages:
                handle.write(json.dumps(message.as_dict(), sort_keys=True) + "\n")
        return path


def read_jsonl(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def iter_messages(messages: Iterable[Message]) -> Iterator[dict[str, object]]:
    for message in messages:
        yield message.as_dict()
