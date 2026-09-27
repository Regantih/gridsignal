"""Client for Jev, TypeSafe AI's decision model, with record-and-replay fixtures.

`Code acts, the rules and the hard vetoes decide, a human approves every commit.`
Jev is a second opinion that escalates when it disagrees; it has no approve path.

Two transports speak to the same model and are normalised to one internal shape:

* **Vercel AI Gateway** (`AI_GATEWAY_API_KEY`) — ``POST https://ai-gateway.vercel.sh/v1/evaluate``
  with ``{"model": "typesafe-ai/jev", "state": ..., "questions": ...}``; yes/no questions
  use ``type: "boolean"``.
* **TypeSafe direct** (`TYPESAFE_API_KEY`) — ``POST https://api.typesafe.ai/v1/systemone``
  with ``{"model": "jev-latest", ...}``; yes/no questions use ``type: "noul"``.

Keys are read from the environment only and are never written to a fixture or a trace.
Only simulated fleet state is ever sent.

Judges run this repo without a key, so every answer is cached: when a key is present a
live answer is recorded to ``data/jev_fixtures/<scenario>.json`` (with a timestamp and the
model version), and when it is absent the recorded answer is replayed. With neither, the
client falls back to the deterministic rules in :mod:`gridsignal.jev.rules` and the UI is
labelled *rules (Jev offline)*.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import httpx

from gridsignal import paths

FIXTURE_DIR = paths.FIXTURE_DIR
GATEWAY_URL = "https://ai-gateway.vercel.sh/v1/evaluate"
GATEWAY_MODEL = "typesafe-ai/jev"
TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
TYPESAFE_MODEL = "jev-latest"
TIMEOUT_S = 30.0


class QuestionKind(StrEnum):
    CHOICE = "choice"
    # Jev's yes/no question: "noul" on the TypeSafe API, "boolean" on the Vercel gateway.
    NOUL = "noul"
    SCORE = "score"


class Source(StrEnum):
    LIVE = "live"
    FIXTURE = "fixture"
    FALLBACK = "fallback"


@dataclass(frozen=True)
class Question:
    """One decision put to Jev."""

    kind: QuestionKind
    instructions: str
    # Choice and yes/no questions describe each option; a score question lists the
    # considerations. The API types differ, so both shapes are kept.
    options: Mapping[str, str] = field(default_factory=dict)
    considerations: tuple[str, ...] = ()

    def as_request(self, noul_type: str) -> dict[str, object]:
        if self.kind is QuestionKind.SCORE:
            return {
                "type": "score",
                "instructions": self.instructions,
                "criteria": list(self.considerations),
            }
        kind = noul_type if self.kind is QuestionKind.NOUL else "choice"
        return {"type": kind, "instructions": self.instructions, "criteria": dict(self.options)}

    def canonical(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "instructions": self.instructions,
            "options": dict(self.options),
            "considerations": list(self.considerations),
        }


@dataclass(frozen=True)
class JevAnswer:
    """One normalised answer: the same fields whatever transport produced it."""

    question_id: str
    kind: QuestionKind
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)
    choice: str | None = None
    yes: bool | None = None
    score: float | None = None

    @property
    def value(self) -> str:
        if self.kind is QuestionKind.CHOICE:
            return self.choice or "unknown"
        if self.kind is QuestionKind.NOUL:
            return "yes" if self.yes else "no"
        return f"{self.score:.2f}" if self.score is not None else "unknown"

    def as_dict(self) -> dict[str, object]:
        return {
            "question_id": self.question_id,
            "kind": self.kind.value,
            "confidence": self.confidence,
            "probabilities": self.probabilities,
            "choice": self.choice,
            "yes": self.yes,
            "score": self.score,
        }

    @staticmethod
    def from_dict(raw: Mapping[str, object]) -> JevAnswer:
        probabilities = raw.get("probabilities") or {}
        yes = raw.get("yes")
        score = raw.get("score")
        choice = raw.get("choice")
        return JevAnswer(
            question_id=str(raw["question_id"]),
            kind=QuestionKind(str(raw["kind"])),
            confidence=float(raw["confidence"]),  # type: ignore[arg-type]
            probabilities={str(k): float(v) for k, v in dict(probabilities).items()},  # type: ignore[arg-type]
            choice=None if choice is None else str(choice),
            yes=None if yes is None else bool(yes),
            score=None if score is None else float(score),  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class JevResponse:
    answers: dict[str, JevAnswer]
    model: str
    source: Source
    latency_ms: float

    def answer(self, question_id: str) -> JevAnswer | None:
        return self.answers.get(question_id)

    @property
    def offline(self) -> bool:
        return self.source is Source.FALLBACK

    def as_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "source": self.source.value,
            "latency_ms": round(self.latency_ms, 1),
            "answers": {k: v.as_dict() for k, v in sorted(self.answers.items())},
        }


def _normalise_answer(question_id: str, question: Question, raw: Mapping[str, object]) -> JevAnswer:
    """Both transports return per-question answers; fold them into one shape.

    A yes/no answer comes back as a probability with no confidence of its own, so the
    confidence is how far off the fence it is.
    """
    probabilities = {str(k): float(v) for k, v in dict(raw.get("probabilities") or {}).items()}
    confidence = raw.get("confidence")
    if question.kind is QuestionKind.CHOICE:
        choice = str(raw.get("choice") or raw.get("answer") or "")
        return JevAnswer(
            question_id=question_id,
            kind=QuestionKind.CHOICE,
            confidence=round(float(confidence if confidence is not None else 0.0), 4),
            probabilities=probabilities,
            choice=choice or None,
        )
    if question.kind is QuestionKind.NOUL:
        probability = raw.get("probability")
        if probability is None:
            probability = raw.get("noul", raw.get("score", 0.0))
        p = float(probability)  # type: ignore[arg-type]
        return JevAnswer(
            question_id=question_id,
            kind=QuestionKind.NOUL,
            confidence=round(float(confidence) if confidence is not None else max(p, 1.0 - p), 4),
            probabilities=probabilities or {"yes": round(p, 4), "no": round(1.0 - p, 4)},
            yes=p >= 0.5,
        )
    score = float(raw.get("score", 0.0))  # type: ignore[arg-type]
    return JevAnswer(
        question_id=question_id,
        kind=QuestionKind.SCORE,
        confidence=round(float(confidence if confidence is not None else 0.0), 4),
        probabilities=probabilities,
        score=round(score, 4),
    )


class Transport:
    """A way to reach Jev. Subclasses differ only in URL, model id and yes/no type."""

    name = "transport"
    url = ""
    model = ""
    noul_type = "noul"

    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self._client = client

    def evaluate(
        self, state: Mapping[str, object], questions: Mapping[str, Question]
    ) -> dict[str, object]:
        body = {
            "model": self.model,
            "state": dict(state),
            "questions": {
                q: question.as_request(self.noul_type) for q, question in questions.items()
            },
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        client = self._client
        if client is not None:
            response = client.post(self.url, headers=headers, json=body, timeout=TIMEOUT_S)
        else:
            response = httpx.post(self.url, headers=headers, json=body, timeout=TIMEOUT_S)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Jev returned a non-object response")
        return payload


class GatewayTransport(Transport):
    name = "vercel-ai-gateway"
    url = GATEWAY_URL
    model = GATEWAY_MODEL
    noul_type = "boolean"


class TypeSafeTransport(Transport):
    name = "typesafe-direct"
    url = TYPESAFE_URL
    model = TYPESAFE_MODEL
    noul_type = "noul"


def transport_from_env(env: Mapping[str, str] | None = None) -> Transport | None:
    """Pick whichever key is set; the gateway wins because TypeSafe signups are paused."""
    environ = os.environ if env is None else env
    gateway = environ.get("AI_GATEWAY_API_KEY", "").strip()
    if gateway:
        return GatewayTransport(gateway)
    direct = environ.get("TYPESAFE_API_KEY", "").strip()
    if direct:
        return TypeSafeTransport(direct)
    return None


def request_key(state: Mapping[str, object], questions: Mapping[str, Question]) -> str:
    """Stable id for a question set plus the simulated state it was asked about."""
    canonical = {
        "state": state,
        "questions": {q: question.canonical() for q, question in sorted(questions.items())},
    }
    body = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:24]


class FixtureStore:
    """Recorded Jev answers, one JSON file per scenario, replayed when there is no key."""

    def __init__(self, name: str, directory: Path = FIXTURE_DIR) -> None:
        self.name = name
        self.path = directory / f"{name}.json"
        self.entries: dict[str, dict[str, object]] = {}
        self.model = ""
        self.transport = ""
        self.recorded_at = ""
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.model = str(raw.get("model", ""))
            self.transport = str(raw.get("transport", ""))
            self.recorded_at = str(raw.get("recorded_at", ""))
            self.entries = dict(raw.get("entries") or {})

    def __len__(self) -> int:
        return len(self.entries)

    def get(self, key: str) -> JevResponse | None:
        entry = self.entries.get(key)
        if entry is None:
            return None
        answers = {
            qid: JevAnswer.from_dict(raw)
            for qid, raw in dict(entry.get("answers") or {}).items()  # type: ignore[arg-type]
        }
        return JevResponse(
            answers=answers,
            model=str(entry.get("model", self.model)),
            source=Source.FIXTURE,
            latency_ms=float(entry.get("latency_ms", 0.0)),  # type: ignore[arg-type]
        )

    def put(self, key: str, question_ids: list[str], response: JevResponse) -> None:
        self.entries[key] = {
            "questions": sorted(question_ids),
            "model": response.model,
            "latency_ms": round(response.latency_ms, 1),
            "answers": {qid: a.as_dict() for qid, a in sorted(response.answers.items())},
        }

    def write(self, transport: str, model: str) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "scenario": self.name,
            "model": model or self.model,
            "transport": transport or self.transport,
            "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "note": (
                "Recorded Jev answers for simulated fleet state; replayed when no API key is set."
            ),
            "entries": dict(sorted(self.entries.items())),
        }
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return self.path


Fallback = Callable[[Mapping[str, object], Mapping[str, Question]], dict[str, JevAnswer]]


class JevClient:
    """One client, three ways to get an answer: live, recorded, or deterministic rules."""

    def __init__(
        self,
        fixtures: FixtureStore | None = None,
        transport: Transport | None = None,
        fallback: Fallback | None = None,
        record: bool = True,
    ) -> None:
        self.fixtures = fixtures
        self.transport = transport
        self.fallback = fallback
        self.record = record
        self.latencies_ms: list[float] = []
        self.sources: list[Source] = []
        self._dirty = False

    @classmethod
    def for_scenario(
        cls,
        name: str,
        env: Mapping[str, str] | None = None,
        directory: Path = FIXTURE_DIR,
        refresh: bool = False,
        fallback: Fallback | None = None,
    ) -> JevClient:
        """Fixtures first so the default run is offline; live only fills the gaps."""
        fixtures = FixtureStore(name, directory)
        transport = transport_from_env(env)
        if refresh:
            fixtures.entries.clear()
        return cls(fixtures=fixtures, transport=transport, fallback=fallback)

    @classmethod
    def offline(cls, fallback: Fallback | None = None) -> JevClient:
        """No transport and no fixtures: the rules fallback, as a judge with no key sees it."""
        return cls(fixtures=None, transport=None, fallback=fallback, record=False)

    @property
    def source(self) -> Source:
        if not self.sources:
            return Source.FALLBACK
        if all(s is Source.FIXTURE for s in self.sources):
            return Source.FIXTURE
        if any(s is Source.LIVE for s in self.sources):
            return Source.LIVE
        return self.sources[0]

    @property
    def median_latency_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return round(ordered[middle], 1)
        return round(0.5 * (ordered[middle - 1] + ordered[middle]), 1)

    def ask(self, state: Mapping[str, object], questions: Mapping[str, Question]) -> JevResponse:
        key = request_key(state, questions)
        if self.fixtures is not None:
            cached = self.fixtures.get(key)
            if cached is not None and set(cached.answers) >= set(questions):
                return self._record(cached)

        if self.transport is not None:
            started = time.perf_counter()
            payload = self.transport.evaluate(state, questions)
            latency_ms = (time.perf_counter() - started) * 1000.0
            raw_answers = payload.get("answers") or {}
            if not isinstance(raw_answers, dict):
                raise ValueError("Jev returned no answers")
            answers = {
                qid: _normalise_answer(qid, question, dict(raw_answers.get(qid) or {}))
                for qid, question in questions.items()
            }
            response = JevResponse(
                answers=answers,
                model=str(payload.get("model", self.transport.model)),
                source=Source.LIVE,
                latency_ms=latency_ms,
            )
            if self.fixtures is not None and self.record:
                self.fixtures.put(key, list(questions), response)
                self._dirty = True
            return self._record(response)

        if self.fallback is None:
            raise RuntimeError("no Jev transport, no fixture and no rules fallback")
        started = time.perf_counter()
        answers = dict(self.fallback(state, questions))
        latency_ms = (time.perf_counter() - started) * 1000.0
        return self._record(
            JevResponse(
                answers=answers,
                model="rules-fallback",
                source=Source.FALLBACK,
                latency_ms=latency_ms,
            )
        )

    def flush(self) -> Path | None:
        """Persist anything newly recorded from a live call."""
        if not self._dirty or self.fixtures is None or self.transport is None:
            return None
        self._dirty = False
        return self.fixtures.write(self.transport.name, self.transport.model)

    def _record(self, response: JevResponse) -> JevResponse:
        self.latencies_ms.append(response.latency_ms)
        self.sources.append(response.source)
        return response
