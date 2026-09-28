"""One :class:`ControlRoomEngine` per browser session.

The engine is deterministic and in memory. A session is a cookie (or an ``X-Session-Id``
header for scripted clients) that maps to one engine, the learned calibration the operator
switched on, and a lock so two clicks cannot mutate the same simulation at once.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import dataclass, field

from gridsignal.control_room.engine import FLEET_SIZE, ControlRoomEngine
from gridsignal.prices import DEFAULT_SCENARIO, load_scenario

COOKIE = "gs_session"
HEADER = "X-Session-Id"
_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")


def valid_id(session_id: str) -> bool:
    """Client-chosen ids are allowed (the UI keeps one per browser), but only this shape."""
    return bool(_ID.fullmatch(session_id))


FLEET_SIZES: tuple[int, ...] = (48, 1_000)
LEARN_NONE = "none"
LEARN_SYNTHETIC = "synthetic"
LEARN_UPLOAD = "upload"
LEARN_SOURCES = (LEARN_NONE, LEARN_SYNTHETIC, LEARN_UPLOAD)
IDLE_S = 6 * 60 * 60


@dataclass
class Session:
    session_id: str
    engine: ControlRoomEngine
    fleet_size: int = FLEET_SIZE
    price_scenario: str = DEFAULT_SCENARIO
    learn_source: str = LEARN_NONE
    upload: bytes | None = None
    upload_name: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    touched: float = field(default_factory=time.monotonic)

    def rebuild(self, fleet_size: int | None = None, price_scenario: str | None = None) -> None:
        self.fleet_size = fleet_size or self.fleet_size
        self.price_scenario = price_scenario or self.price_scenario
        self.engine = ControlRoomEngine(
            price_trace=load_scenario(self.price_scenario), fleet_size=self.fleet_size
        )


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def new_id(self) -> str:
        return secrets.token_urlsafe(24)

    def get(self, session_id: str) -> Session:
        with self._lock:
            self._sweep()
            session = self._sessions.get(session_id)
            if session is None:
                session = Session(session_id=session_id, engine=ControlRoomEngine())
                self._sessions[session_id] = session
            session.touched = time.monotonic()
            return session

    def _sweep(self) -> None:
        cutoff = time.monotonic() - IDLE_S
        for key in [k for k, s in self._sessions.items() if s.touched < cutoff]:
            del self._sessions[key]

    def __len__(self) -> int:
        return len(self._sessions)
