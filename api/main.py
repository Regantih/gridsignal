"""The GridSignal web API and static host for the built front end.

    uvicorn api.main:app --port 8000

Every route wraps an existing ``gridsignal`` call and returns its numbers untouched. The
engine never dispatches a real device: a human operator approves every recovery action,
one incident at a time or in advance through a playbook with limits.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api import compute, sessions
from api.serialize import to_json
from gridsignal import live, member
from gridsignal.control_room.engine import (
    FOCUS_DEVICE_ID,
    OWNERS,
    PLAYBOOK_MAX_DEVICE_SHARE,
    PLAYBOOK_MAX_KW_SHARE,
    ApprovalError,
    Role,
)
from gridsignal.prices import DEFAULT_SCENARIO, available_scenarios
from gridsignal.twin import feed as twin_feed
from gridsignal.twin import planner as twin_planner
from gridsignal.twin import risk as twin_risk

WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
APPROVAL_SENTENCE = (
    "a human operator approves every recovery action, one incident at a time "
    "or in advance through a playbook with limits"
)
DISCLOSURE = "Prices are real ERCOT data. The fleet is simulated."

store = sessions.SessionStore()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if os.environ.get("GRIDSIGNAL_PREWARM", "1") == "1":
        compute.prewarm()
    yield


app = FastAPI(title="GridSignal API", version="0.1.0", lifespan=lifespan)


# ------------------------------------------------------------------ sessions


def current_session(request: Request, response: Response) -> sessions.Session:
    sid = request.headers.get(sessions.HEADER) or request.cookies.get(sessions.COOKIE)
    if not sid:
        sid = store.new_id()
    response.set_cookie(
        sessions.COOKIE, sid, httponly=True, samesite="lax", max_age=sessions.IDLE_S
    )
    return store.get(sid)


SessionDep = Annotated[sessions.Session, Depends(current_session)]


@app.exception_handler(ApprovalError)
async def _approval_error(_: Request, exc: ApprovalError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def _value_error(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "sessions": len(store)}


def _session_payload(s: sessions.Session) -> dict:
    return {
        "session_id": s.session_id,
        "fleet_size": s.fleet_size,
        "fleet_sizes": list(sessions.FLEET_SIZES),
        "price_scenario": s.price_scenario,
        "price_scenarios": [
            {"key": sc.key, "label": sc.label, "blurb": sc.blurb} for sc in available_scenarios()
        ],
        "learn_source": s.learn_source,
        "upload_name": s.upload_name,
        "approval_sentence": APPROVAL_SENTENCE,
        "disclosure": DISCLOSURE,
    }


@app.get("/api/session")
def get_session(s: SessionDep) -> dict:
    return _session_payload(s)


class ResetBody(BaseModel):
    fleet_size: int | None = None
    price_scenario: str | None = None


@app.post("/api/session/reset")
def reset_session(body: ResetBody, s: SessionDep) -> dict:
    if body.fleet_size is not None and body.fleet_size not in sessions.FLEET_SIZES:
        raise HTTPException(422, f"fleet_size must be one of {list(sessions.FLEET_SIZES)}")
    keys = {sc.key for sc in available_scenarios()}
    if body.price_scenario is not None and body.price_scenario not in keys:
        raise HTTPException(422, f"price_scenario must be one of {sorted(keys)}")
    with s.lock:
        s.rebuild(body.fleet_size, body.price_scenario or DEFAULT_SCENARIO)
    return _session_payload(s)


# ------------------------------------------------------------------ fleet (Tonight)


def fleet_payload(s: sessions.Session) -> dict:
    eng = s.engine
    snap = eng.snapshot()
    playbook = eng.playbook
    return {
        "now": snap.now.isoformat(),
        "summary": {
            "total_devices": snap.total_devices,
            "online": snap.online,
            "degraded": snap.degraded,
            "offline": snap.offline,
            "unavailable": snap.unavailable,
            "available_capacity_kwh": snap.available_capacity_kwh,
            "committed_kw": snap.committed_kw,
            "target_kw": snap.grid_event.target_kw,
            "coverage_pct": snap.coverage_pct,
            "home_load_kw": snap.home_load_kw,
            "discharge_kw": snap.discharge_kw,
            "partner_kw": snap.partner_kw,
            "open_incidents": snap.open_incidents,
            "headroom_kw": eng.headroom_kw(),
            "commit_ratio": eng.current_commit_ratio(),
            "remaining_hours": eng.remaining_hours(),
            "remaining_price_mwh": eng.remaining_price_mwh(),
        },
        "grid_event": to_json(snap.grid_event),
        "devices": to_json(snap.devices),
        "incidents": to_json(snap.incidents),
        "pending_incident": to_json(eng.pending_incident),
        "playbook": to_json(playbook),
        "playbook_defaults": {
            "max_kw": round(PLAYBOOK_MAX_KW_SHARE * snap.grid_event.target_kw, 1),
            "max_devices": max(1, int(PLAYBOOK_MAX_DEVICE_SHARE * len(eng.mine))),
        },
        "audit": to_json(snap.audit),
        "human_summary": eng.human_summary(),
        "prices": to_json(eng.prices.frame.to_dict(orient="records")),
        "price_trace": {
            "location": eng.prices.location,
            "market": eng.prices.market,
            "date": eng.prices.date,
            "source": eng.prices.source,
        },
        "focus_device_id": FOCUS_DEVICE_ID,
        "operator": OWNERS[Role.FLEET_OPERATOR],
        "approval_sentence": APPROVAL_SENTENCE,
        "disclosure": DISCLOSURE,
    }


@app.get("/api/fleet")
def get_fleet(s: SessionDep) -> dict:
    with s.lock:
        return fleet_payload(s)


class TriggerBody(BaseModel):
    device_id: str = FOCUS_DEVICE_ID


@app.post("/api/incidents/trigger")
def trigger_incident(body: TriggerBody, s: SessionDep) -> dict:
    with s.lock:
        incident = s.engine.trigger_device_failure(body.device_id)
        return {"incident": to_json(incident), "fleet": fleet_payload(s)}


@app.post("/api/incidents/approve")
def approve_incident(s: SessionDep) -> dict:
    with s.lock:
        incident = s.engine.approve_recovery()
        return {"incident": to_json(incident), "fleet": fleet_payload(s)}


class StaleBody(BaseModel):
    share: float = Field(0.03, gt=0, le=1)


@app.post("/api/incidents/stale-wave")
def stale_wave(body: StaleBody, s: SessionDep) -> dict:
    with s.lock:
        dropped_kw = s.engine.inject_stale_telemetry(body.share)
        return {"dropped_kw": dropped_kw, "fleet": fleet_payload(s)}


class PlaybookBody(BaseModel):
    max_kw: float | None = None
    max_devices: int | None = None


@app.post("/api/playbook")
def approve_playbook(body: PlaybookBody, s: SessionDep) -> dict:
    with s.lock:
        playbook = s.engine.approve_playbook(max_kw=body.max_kw, max_devices=body.max_devices)
        return {"playbook": to_json(playbook), "fleet": fleet_payload(s)}


@app.delete("/api/playbook")
def revoke_playbook(s: SessionDep) -> dict:
    with s.lock:
        s.engine.revoke_playbook(reason="revoked from the GridSignal app")
        return {"fleet": fleet_payload(s)}


class CommitmentBody(BaseModel):
    ratio: float = Field(gt=0, le=1)
    basis: str = ""


@app.post("/api/commitment")
def set_commitment(body: CommitmentBody, s: SessionDep) -> dict:
    with s.lock:
        target_kw = s.engine.set_commitment(body.ratio, basis=body.basis)
        return {"target_kw": target_kw, "fleet": fleet_payload(s)}


# ------------------------------------------------------------------ planner (Tomorrow)


@app.get("/api/scenarios")
def get_scenarios() -> dict:
    menu = compute.scenario_menu()
    return {
        "scenarios": [{"name": name, **to_json(spec)} for name, spec in menu.items()],
        "default": twin_planner.DEFAULT_SCENARIO,
        "live_scenario": twin_planner.LIVE_SCENARIO,
        "live_available": twin_planner.LIVE_SCENARIO in menu,
    }


def plan_payload(plan: twin_planner.CommitmentPlan, current_ratio: float) -> dict:
    return {
        **to_json(plan),
        "recommended_ratio": plan.recommended_ratio,
        "headline": plan.headline(current_ratio),
        "current_ratio": current_ratio,
        "kept_at_current": {policy: plan.kept_at(policy, current_ratio) for policy in plan.curve},
        "kept_at_recommended": {
            policy: plan.kept_at(policy, plan.recommended_ratio) for policy in plan.curve
        },
    }


@app.get("/api/plan")
def get_plan(
    s: SessionDep,
    scenario: str = twin_planner.DEFAULT_SCENARIO,
    target: float = 0.99,
) -> dict:
    if scenario not in compute.scenario_menu():
        raise HTTPException(422, f"unknown scenario {scenario!r}")
    if not 0.5 <= target <= 0.999:
        raise HTTPException(422, "target must be between 0.5 and 0.999")
    calibration = compute.calibration_for(s)
    with s.lock:
        current = s.engine.current_commit_ratio()
        plan = compute.plan_for(s.engine, scenario, target, calibration)
    return {
        **plan_payload(plan, current),
        "learn_source": s.learn_source,
        "calibration_source": calibration.source if calibration else None,
    }


@app.get("/api/risk")
def get_risk(s: SessionDep) -> dict:
    calibration = compute.calibration_for(s)
    with s.lock:
        groups = twin_risk.groups(s.engine, calibration)
        summary = twin_risk.summary(groups)
        target_kw = s.engine.grid_event.target_kw
        playbook = to_json(s.engine.playbook)
    return {
        "groups": [
            {**to_json(g), "status": g.status, "covering_ratio": g.covering_ratio} for g in groups
        ],
        "summary": summary,
        "status_order": list(twin_risk.STATUS_ORDER),
        "target_kw": target_kw,
        "playbook": playbook,
        "learn_source": s.learn_source,
        "calibration_source": calibration.source if calibration else None,
        "feeder_note": (
            "Feeders are simulated as zone quadrants; gateway rings come from device ids. "
            "Real feeder ids are not available in this data."
        ),
    }


def calibration_payload(cal) -> dict:
    return {
        **to_json(cal),
        "rows": to_json(cal.rows()),
        "synthetic": cal.source.startswith("SYNTHETIC"),
    }


@app.get("/api/learn")
def get_learn(s: SessionDep) -> dict:
    synthetic = compute.synthetic_calibration()
    upload = compute.upload_calibration(s.upload) if s.upload else None
    return {
        "learn_source": s.learn_source,
        "sources": list(sessions.LEARN_SOURCES),
        "synthetic": calibration_payload(synthetic),
        "upload": calibration_payload(upload) if upload else None,
        "upload_name": s.upload_name,
    }


class LearnBody(BaseModel):
    source: str


@app.put("/api/learn/source")
def set_learn_source(body: LearnBody, s: SessionDep) -> dict:
    if body.source not in sessions.LEARN_SOURCES:
        raise HTTPException(422, f"source must be one of {list(sessions.LEARN_SOURCES)}")
    if body.source == sessions.LEARN_UPLOAD and not s.upload:
        raise HTTPException(409, "upload a telemetry history first")
    s.learn_source = body.source
    return {"learn_source": s.learn_source}


@app.post("/api/learn/upload")
async def upload_learn(file: Annotated[UploadFile, File()], s: SessionDep) -> dict:
    raw = await file.read()
    if not raw.strip():
        raise HTTPException(422, "the uploaded file is empty")
    try:
        cal = compute.upload_calibration(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(422, f"could not learn from this file: {exc}") from exc
    s.upload = raw
    s.upload_name = file.filename
    s.learn_source = sessions.LEARN_UPLOAD
    return {
        "learn_source": s.learn_source,
        "upload_name": s.upload_name,
        "upload": calibration_payload(cal),
    }


# ------------------------------------------------------------------ member


@app.get("/api/members")
def list_members(s: SessionDep) -> dict:
    with s.lock:
        homes = [
            {"device_id": d.device_id, "site": d.site, "zone": d.zone, "status": d.status.value}
            for d in s.engine.mine
        ]
    return {"homes": homes, "focus_device_id": FOCUS_DEVICE_ID}


@app.get("/api/members/{device_id}")
def get_member(device_id: str, s: SessionDep) -> dict:
    with s.lock:
        try:
            summary = member.member_summary(s.engine, device_id)
        except KeyError as exc:
            raise HTTPException(404, f"no home {device_id}") from exc
        neighbours = member.neighbours(s.engine, device_id)
        mine = {
            i.incident_id
            for i in s.engine.incidents
            if device_id == i.device_id or device_id in i.cohort
        }
        history = [
            to_json(e)
            for e in s.engine.audit
            if e.kind == "baseline"
            or device_id in e.summary
            or device_id in e.detail
            or any(iid in e.summary for iid in mine)
        ]
        event = to_json(s.engine.grid_event)
    return {
        "summary": to_json(summary),
        "neighbours": neighbours,
        "history": history,
        "grid_event": event,
        "disclosure": DISCLOSURE,
    }


# ------------------------------------------------------------------ live ERCOT feed


def feed_payload(result: compute.FeedRefresh | None) -> dict:
    status = twin_feed.describe(twin_feed.load_store())
    today = datetime.now(live.CENTRAL).date()
    days = twin_feed.live_days()
    hot_days = int((days.prices.max((1, 2)) > 250).sum()) if days is not None and len(days) else 0
    so_far = twin_feed.today_prices(today=today)
    check = None
    if so_far is not None:
        c = twin_feed.check_today(twin_planner.world_model(live=True), so_far, today.month)
        check = {**to_json(c), "verdict": c.verdict, "zones": list(twin_feed.ZONES)}
    return {
        "status": to_json(status),
        "intervals_per_day": twin_feed.INTERVALS_PER_DAY,
        "today": today.isoformat(),
        "settled_days": int(len(days)) if days is not None else 0,
        "hot_days": hot_days,
        "check": check,
        "refresh": to_json(result) if result is not None else None,
        "refresh_seconds": twin_feed.REFRESH_S,
        "source": live.SOURCE_PAGE,
        "disclosure": DISCLOSURE,
    }


@app.get("/api/feed")
def get_feed() -> dict:
    return feed_payload(compute.last_refresh())


@app.post("/api/feed/refresh")
def refresh_feed(force: bool = False) -> dict:
    return feed_payload(compute.refresh_feed(force=force))


# ------------------------------------------------------------------ about


@app.get("/api/about")
def about() -> dict:
    study = compute.twin_study()
    return {
        "approval_sentence": APPROVAL_SENTENCE,
        "disclosure": DISCLOSURE,
        "study": study,
        "plan_years": compute.PLAN_YEARS,
        "synthetic_days": compute.SYNTHETIC_DAYS,
    }


# ------------------------------------------------------------------ static front end

if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        candidate = WEB_DIST / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(WEB_DIST / "index.html")
