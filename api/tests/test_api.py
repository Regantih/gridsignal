"""The API returns the engine's own numbers: every figure is checked against a second
engine driven the same way, never against a literal."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api import compute, main, sessions
from gridsignal.control_room.engine import ControlRoomEngine
from gridsignal.twin import planner as twin_planner
from gridsignal.twin import risk as twin_risk


@pytest.fixture()
def client() -> TestClient:
    main.store = sessions.SessionStore()
    return TestClient(main.app, headers={sessions.HEADER: "test-session"})


def _twin() -> ControlRoomEngine:
    """An engine built exactly as a fresh session builds its own."""
    return ControlRoomEngine()


def test_snapshot_matches_the_engine(client: TestClient) -> None:
    payload = client.get("/api/fleet").json()
    snap = _twin().snapshot()
    assert payload["summary"]["committed_kw"] == snap.committed_kw
    assert payload["summary"]["target_kw"] == snap.grid_event.target_kw
    assert payload["summary"]["coverage_pct"] == snap.coverage_pct
    assert payload["summary"]["total_devices"] == snap.total_devices
    assert len(payload["devices"]) == len(snap.devices)
    assert main.APPROVAL_SENTENCE in payload["approval_sentence"]
    assert "simulated" in payload["disclosure"]


def test_incident_dollars_at_risk_equal_the_engines(client: TestClient) -> None:
    incident = client.post("/api/incidents/trigger", json={}).json()["incident"]
    expected = _twin().trigger_device_failure()
    assert incident["dollars_at_risk"] == expected.dollars_at_risk
    assert incident["lost_kw"] == expected.lost_kw
    assert incident["status"] == "awaiting_approval"
    fleet = client.get("/api/fleet").json()
    assert fleet["pending_incident"]["incident_id"] == incident["incident_id"]
    assert fleet["summary"]["coverage_pct"] < 100.0


def test_recovery_needs_approval_and_recovers_no_more_than_at_risk(client: TestClient) -> None:
    assert client.post("/api/incidents/approve").status_code == 409
    at_risk = client.post("/api/incidents/trigger", json={}).json()["incident"]["dollars_at_risk"]
    body = client.post("/api/incidents/approve").json()
    twin = _twin()
    twin.trigger_device_failure()
    expected = twin.approve_recovery()
    assert body["incident"]["dollars_recovered"] == expected.dollars_recovered
    assert body["incident"]["dollars_recovered"] <= at_risk
    assert body["incident"]["status"] == "resolved"
    kinds = [e["kind"] for e in body["fleet"]["audit"]]
    assert "human_approval" in kinds


def test_playbook_executes_inside_its_limits_and_is_logged(client: TestClient) -> None:
    fleet = client.get("/api/fleet").json()
    pb = client.post("/api/playbook", json=fleet["playbook_defaults"]).json()["playbook"]
    twin = _twin()
    expected_pb = twin.approve_playbook()
    assert pb["max_kw"] == expected_pb.max_kw
    assert pb["max_devices"] == expected_pb.max_devices
    incident = client.post("/api/incidents/trigger", json={}).json()["incident"]
    expected = twin.trigger_device_failure()
    assert incident["status"] == expected.status.value == "resolved"
    assert incident["executed_under"] == expected.executed_under
    assert incident["dollars_recovered"] == expected.dollars_recovered
    assert incident["dollars_recovered"] <= incident["dollars_at_risk"]


def test_commitment_sets_the_engines_target(client: TestClient) -> None:
    body = client.post("/api/commitment", json={"ratio": 0.6, "basis": "test"}).json()
    assert body["target_kw"] == _twin().set_commitment(0.6, basis="test")
    assert body["fleet"]["grid_event"]["target_kw"] == body["target_kw"]


def test_plan_recommendation_equals_plan_commitment(client: TestClient) -> None:
    compute._plan.cache_clear()
    body = client.get("/api/plan", params={"scenario": twin_planner.DEFAULT_SCENARIO}).json()
    twin = _twin()
    plan = twin_planner.plan_commitment(
        assumptions=twin_planner.assumptions_from_engine(twin),
        scenario=twin_planner.DEFAULT_SCENARIO,
        target=0.99,
        years=compute.PLAN_YEARS,
    )
    assert body["recommended_ratio"] == plan.recommended_ratio
    assert body["safe_ratio"] == plan.safe_ratio
    curve = plan.curve["gridsignal_auto"]
    assert body["curve"]["gridsignal_auto"] == {str(k): v for k, v in curve.items()}
    assert body["current_ratio"] == twin.current_commit_ratio()


def test_scenarios_include_the_live_feed_when_it_has_enough_days(client: TestClient) -> None:
    body = client.get("/api/scenarios").json()
    names = [s["name"] for s in body["scenarios"]]
    assert names == list(twin_planner.scenarios(live=True))
    if body["live_available"]:
        assert twin_planner.LIVE_SCENARIO in names


def test_risk_summary_equals_the_twins(client: TestClient) -> None:
    body = client.get("/api/risk").json()
    groups = twin_risk.groups(_twin())
    assert body["summary"] == twin_risk.summary(groups)
    assert len(body["groups"]) == len(groups)
    assert body["groups"][0]["lost_kw"] == groups[0].lost_kw
    assert body["status_order"] == list(twin_risk.STATUS_ORDER)


def test_learning_is_labelled_synthetic_and_upload_switches_source(client: TestClient) -> None:
    body = client.get("/api/learn").json()
    assert body["learn_source"] == "none"
    assert body["synthetic"]["synthetic"] is True
    assert body["synthetic"]["source"].startswith("SYNTHETIC")
    assert client.put("/api/learn/source", json={"source": "upload"}).status_code == 409
    assert client.put("/api/learn/source", json={"source": "synthetic"}).json() == {
        "learn_source": "synthetic"
    }
    risk = client.get("/api/risk").json()
    assert risk["calibration_source"].startswith("SYNTHETIC")
    plan = client.get("/api/plan").json()
    assert plan["calibrated"] is True
    assert plan["calibration_source"].startswith("SYNTHETIC")

    from gridsignal.twin import learn as twin_learn

    eng = compute.learn_fleet()
    groups = twin_risk.device_groups(eng)
    lines = twin_learn.synthetic_history(
        list(eng.mine)[:200],
        days=10,
        feeders={k: v["feeder"] for k, v in groups.items()},
        ring_of={k: v["ring"] for k, v in groups.items()},
    )
    up = client.post(
        "/api/learn/upload",
        files={"file": ("history.jsonl", "\n".join(lines).encode(), "application/jsonl")},
    )
    assert up.status_code == 200, up.text
    assert up.json()["learn_source"] == "upload"
    assert up.json()["upload"]["synthetic"] is False
    bad = client.post("/api/learn/upload", files={"file": ("x.jsonl", b"   ", "text/plain")})
    assert bad.status_code == 422


def test_member_view_is_the_member_modules(client: TestClient) -> None:
    from gridsignal import member

    client.post("/api/incidents/trigger", json={})
    body = client.get("/api/members/BAT-042").json()
    twin = _twin()
    twin.trigger_device_failure()
    expected = member.member_summary(twin, "BAT-042")
    assert body["summary"]["backup_hours"] == expected.backup_hours
    assert body["summary"]["reserve_kwh"] == expected.reserve_kwh
    assert body["summary"]["headline"] == expected.headline
    assert "INC-" not in body["summary"]["body"]
    assert client.get("/api/members/BAT-999").status_code == 404


def test_feed_degrades_to_stored_days_when_ercot_is_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gridsignal.twin import feed as twin_feed

    def boom(**_: object) -> None:
        raise twin_feed.FeedError("offline for the test")

    monkeypatch.setattr(twin_feed, "refresh", boom)
    compute._last_refresh = None
    body = client.post("/api/feed/refresh", params={"force": "true"}).json()
    assert body["refresh"]["ok"] is False
    assert "offline for the test" in body["refresh"]["error"]
    stored = twin_feed.describe(twin_feed.load_store())
    assert body["status"]["days_in_store"] == stored.days_in_store
    assert body["status"]["intervals_today"] == stored.intervals_today
    assert body["intervals_per_day"] == 96


def test_sessions_are_isolated_by_id(client: TestClient) -> None:
    client.post("/api/incidents/trigger", json={})
    other = TestClient(main.app, headers={sessions.HEADER: "someone-else"})
    assert other.get("/api/fleet").json()["pending_incident"] is None
    assert client.get("/api/fleet").json()["pending_incident"] is not None
    cookie = TestClient(main.app).get("/api/fleet")
    assert sessions.COOKIE in cookie.cookies


def test_reset_rebuilds_the_fleet_at_the_requested_size(client: TestClient) -> None:
    assert client.post("/api/session/reset", json={"fleet_size": 7}).status_code == 422
    body = client.post("/api/session/reset", json={"fleet_size": 1000}).json()
    assert body["fleet_size"] == 1000
    assert client.get("/api/fleet").json()["summary"]["total_devices"] == 1000


def test_a_client_chosen_session_id_is_kept_and_a_malformed_one_is_refused(
    client: TestClient,
) -> None:
    ok = client.get("/api/session", headers={"X-Session-Id": "browser-abc12345"})
    assert ok.status_code == 200 and ok.json()["session_id"] == "browser-abc12345"
    bad = client.get("/api/session", headers={"X-Session-Id": "../../etc"})
    assert bad.status_code == 400


def test_the_feed_watch_refreshes_once_then_stops(monkeypatch) -> None:
    import threading

    from api import compute

    calls = threading.Event()
    monkeypatch.setattr(compute, "refresh_feed", lambda force=False: calls.set())
    stop = compute.start_feed_watch()
    assert calls.wait(5)
    stop.set()


def test_a_storm_over_houston_drops_the_homes_under_it_and_reports_who_covers(
    client: TestClient,
) -> None:
    from gridsignal.twin import risk as twin_risk

    body = client.post(
        "/api/whatif/storm", json={"lat": 29.76, "lon": -95.37, "radius_km": 60}
    ).json()
    assert body["homes"] > 0 and body["zones"] == ["LZ_HOUSTON"]
    assert 0 <= body["uncovered_kw"] <= body["lost_kw"]
    assert body["holds"] == (body["uncovered_kw"] <= 1e-6)
    assert 0 <= body["kept_share"] <= 1
    # a storm nowhere near a home changes nothing
    empty = client.post(
        "/api/whatif/storm", json={"lat": 31.0, "lon": -105.0, "radius_km": 20}
    ).json()
    assert empty["homes"] == 0 and empty["holds"] is True and empty["kept_share"] == 1.0
    assert twin_risk.STATUS_ORDER  # module still exposes the map statuses
    bad = client.post("/api/whatif/storm", json={"lat": 29.7, "lon": -95.3, "radius_km": 0})
    assert bad.status_code == 422


def test_feed_days_give_one_96_interval_row_per_settled_day(client: TestClient) -> None:
    body = client.get("/api/feed/days").json()
    assert len(body["dates"]) == len(body["prices"]) == len(body["peaks"])
    assert all(len(row) == 96 for row in body["prices"])
    assert body["peaks"] == [max(row) for row in body["prices"]]
    assert client.get("/api/feed/days", params={"zone": "LZ_MARS"}).status_code == 422


def test_markets_list_ercot_live_and_the_rest_not_modelled(client: TestClient) -> None:
    payload = client.get("/api/markets").json()
    by_id = {m["id"]: m for m in payload["markets"]}
    assert payload["live"] == ["ercot"]
    assert by_id["ercot"]["status"] == "live"
    assert by_id["comed"]["status"] == "planned"
    assert by_id["colorado"]["status"] == "equipment"
    for m in payload["markets"]:
        assert set(m) >= {"id", "name", "iso", "status", "bounds", "center"}
        lo, hi = m["bounds"]
        assert lo[0] <= m["center"][0] <= hi[0] and lo[1] <= m["center"][1] <= hi[1]
        # Nothing here carries a number the engine did not produce.
        assert not any(k in m for k in ("dollars", "kw", "price_mwh", "homes"))
