"""Heavy results cached the way ``app/dashboard.py`` caches them.

Plans are keyed by the fleet's assumption signature, scenario, target and the feed's newest
settled day; calibrations by their source; the feed refresh by 15-minute bucket. Every
function here calls straight into ``gridsignal`` and returns its objects unchanged.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from functools import lru_cache

from api import sessions
from gridsignal.control_room.engine import ControlRoomEngine
from gridsignal.twin import feed as twin_feed
from gridsignal.twin import learn as twin_learn
from gridsignal.twin import planner as twin_planner
from gridsignal.twin import risk as twin_risk
from gridsignal.twin.data import TWIN_DIR

PLAN_YEARS = 3
SYNTHETIC_DAYS = 60
SYNTHETIC_LABEL = "SYNTHETIC 60-day history, generated with known rates"
FEED_MAX_DAYS = 30

_plan_lock = threading.Lock()
_feed_lock = threading.Lock()


@lru_cache(maxsize=1)
def learn_fleet() -> ControlRoomEngine:
    """The 1,000-device fleet the synthetic history is generated for."""
    return ControlRoomEngine(fleet_size=1_000)


def _learn_maps() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    eng = learn_fleet()
    groups = twin_risk.device_groups(eng)
    feeders = {k: v["feeder"] for k, v in groups.items()}
    rings = {k: v["ring"] for k, v in groups.items()}
    zones = {d.device_id: d.zone for d in eng.mine}
    return zones, feeders, rings


@lru_cache(maxsize=1)
def synthetic_calibration() -> twin_learn.Calibration:
    zones, feeders, rings = _learn_maps()
    lines = twin_learn.synthetic_history(
        list(learn_fleet().mine), days=SYNTHETIC_DAYS, feeders=feeders, ring_of=rings
    )
    frame, _ = twin_learn.frame_from_lines(lines)
    return twin_learn.learn(frame, zones, feeders, source=SYNTHETIC_LABEL)


@lru_cache(maxsize=8)
def _upload_calibration(digest: str, upload: bytes) -> twin_learn.Calibration:
    zones, feeders, _ = _learn_maps()
    frame, rejected = twin_learn.frame_from_lines(upload.decode("utf-8").splitlines())
    label = f"uploaded history ({rejected:,} rows rejected by the importer)"
    return twin_learn.learn(frame, zones, feeders, source=label)


def upload_calibration(upload: bytes) -> twin_learn.Calibration:
    """Learned rates from an uploaded JSONL export, keyed by content hash."""
    return _upload_calibration(hashlib.sha256(upload).hexdigest(), upload)


def calibration_for(session: sessions.Session) -> twin_learn.Calibration | None:
    """The calibration the operator switched on for this session, or None."""
    if session.learn_source == sessions.LEARN_SYNTHETIC:
        return synthetic_calibration()
    if session.learn_source == sessions.LEARN_UPLOAD and session.upload:
        return upload_calibration(session.upload)
    return None


@lru_cache(maxsize=24)
def _plan(
    signature: tuple, scenario: str, target: float, live_day: str | None
) -> twin_planner.CommitmentPlan:
    return twin_planner.plan_commitment(
        assumptions=twin_planner.FleetAssumptions(**dict(signature)),
        scenario=scenario,
        target=target,
        years=PLAN_YEARS,
    )


def plan_for(
    engine: ControlRoomEngine,
    scenario: str,
    target: float,
    calibration: twin_learn.Calibration | None,
) -> twin_planner.CommitmentPlan:
    """``plan_commitment`` for this fleet, exactly as the dashboard calls it."""
    a = twin_planner.assumptions_from_engine(engine)
    if calibration is not None:
        a = calibration.apply(a)
    signature = tuple(sorted(a.__dict__.items()))
    live_day = twin_feed.last_full_day() if scenario == twin_planner.LIVE_SCENARIO else None
    with _plan_lock:
        return _plan(signature, scenario, target, live_day)


def scenario_menu() -> dict[str, dict]:
    return twin_planner.scenarios(live=True)


@dataclass
class FeedRefresh:
    ok: bool
    bucket: int
    status: twin_feed.FeedStatus | None = None
    error: str | None = None


_last_refresh: FeedRefresh | None = None


def refresh_feed(force: bool = False) -> FeedRefresh:
    """One refresh per 15-minute bucket. Failures are reported, never raised: the stored
    days keep serving."""
    global _last_refresh
    bucket = int(time.time() // twin_feed.REFRESH_S)
    with _feed_lock:
        if not force and _last_refresh is not None and _last_refresh.bucket == bucket:
            return _last_refresh
        try:
            status = twin_feed.refresh(max_days=FEED_MAX_DAYS)
            _last_refresh = FeedRefresh(ok=True, bucket=bucket, status=status)
        except (twin_feed.FeedError, OSError, ValueError) as exc:
            _last_refresh = FeedRefresh(ok=False, bucket=bucket, error=str(exc))
        return _last_refresh


def last_refresh() -> FeedRefresh | None:
    return _last_refresh


@lru_cache(maxsize=1)
def twin_study() -> dict:
    return {
        "stress": json.loads((TWIN_DIR / "stress.json").read_text()),
        "validation": json.loads((TWIN_DIR / "validation.json").read_text()),
    }


def prewarm() -> None:
    """Fit the world models in the background so the first plan does not wait for them."""

    def _warm() -> None:
        twin_planner.world_model()
        twin_planner.world_model(live=True)

    threading.Thread(target=_warm, daemon=True).start()
