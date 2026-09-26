"""The Grid Signals headline must never show the scenario day on its own."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from gridsignal import holdout

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import dashboard  # noqa: E402

APP = str(Path(__file__).resolve().parents[1] / "app" / "dashboard.py")


@pytest.fixture(scope="module")
def grid_signals() -> AppTest:
    app = AppTest.from_file(APP, default_timeout=180)
    app.run()
    app.session_state["view"] = "Grid Signals"
    app.run()
    assert not app.exception, app.exception
    return app


def markdown_text(app: AppTest) -> str:
    return " ".join([m.value for m in app.markdown] + [c.value for c in app.caption])


def test_headline_reports_the_held_out_mean_median_and_win_rate(grid_signals: AppTest) -> None:
    summary = holdout.summarize(holdout.evaluate())
    text = markdown_text(grid_signals)
    assert f"{abs(summary.mean_uplift_usd):,.2f}" in text
    assert f"{abs(summary.median_uplift_usd):,.2f}" in text
    assert f"{summary.days_won} of {summary.days} days" in text


def test_headline_carries_the_one_line_caveat(grid_signals: AppTest) -> None:
    assert "never the claim on its own" in markdown_text(grid_signals)


def test_every_view_renders() -> None:
    for view in ("Control Room", "Member App", "Grid Signals", "Agent Mesh"):
        app = AppTest.from_file(APP, default_timeout=180)
        app.run()
        app.session_state["view"] = view
        app.run()
        assert not app.exception, (view, app.exception)


def test_agent_mesh_shows_the_registry_the_log_and_a_scenario_picker() -> None:
    app = AppTest.from_file(APP, default_timeout=180)
    app.run()
    app.session_state["view"] = "Agent Mesh"
    app.run()
    assert not app.exception, app.exception

    frames = [df.value for df in app.dataframe]
    registry = next(f for f in frames if "card" in f.columns)
    log = next(f for f in frames if "kind" in f.columns and "card" not in f.columns)
    assert {"agent", "kind", "health", "last heartbeat (s)", "card"} <= set(registry.columns)
    assert set(registry["card"]) <= {"verified", "stale", "rejected"}
    assert {"call_for_capacity", "award_proposed"} <= set(log["kind"])
    assert "approval" not in set(log["kind"])  # nothing is approved until a human clicks
    assert [s.label for s in app.selectbox] == ["Replay"]
    assert "Simulation only" in " ".join(m.value for m in app.markdown)


def test_agent_mesh_awards_execute_only_after_the_operator_clicks_approve() -> None:
    app = AppTest.from_file(APP, default_timeout=180)
    app.run()
    app.session_state["view"] = "Agent Mesh"
    app.run()
    assert not app.exception, app.exception

    approve = next(b for b in app.button if b.label == "Approve award set")
    approve.click().run()
    app.session_state["mesh_kinds"] = []  # show every message kind, including the new ones
    app.run()
    assert not app.exception, app.exception

    log = next(
        df.value
        for df in app.dataframe
        if "kind" in df.value.columns and "card" not in df.value.columns
    )
    assert {"approval", "award_executed"} <= set(log["kind"])
    approved = " ".join(e.value for e in app.success)
    assert "M. Alvarez (Fleet Operator)" in approved and "scripted" not in approved


def test_agent_mesh_shows_jev_answers_and_the_rules_comparison() -> None:
    app = AppTest.from_file(APP, default_timeout=180)
    app.run()
    app.session_state["view"] = "Agent Mesh"
    app.run()
    assert not app.exception, app.exception

    frames = [df.value for df in app.dataframe]
    answers = next(f for f in frames if "confidence" in f.columns)
    assert {"question", "answer", "probabilities", "latency (ms)"} <= set(answers.columns)
    assert "Root cause" in set(answers["question"])

    evaluation = next(f for f in frames if "root-cause accuracy" in f.columns)
    assert set(evaluation["decision layer"]) == {"rules-only", "jev"}

    text = " ".join(m.value for m in app.markdown)
    assert "Decision layer" in text
    assert "human approval required" in text or "auto-approved by Jev" in text


def test_incident_panel_shows_the_jev_read_out() -> None:
    app = AppTest.from_file(APP, default_timeout=180)
    app.run()
    trigger = next(b for b in app.button if "Trigger" in b.label)
    trigger.click().run()
    assert not app.exception, app.exception
    text = " ".join(m.value for m in app.markdown)
    assert "Code acts, Jev decides" in text
    assert any(label in text for label in ("Jev live", "Jev (recorded answer)", "Jev offline"))


def test_control_room_accounts_for_spare_capacity_and_can_offer_it() -> None:
    app = AppTest.from_file(APP, default_timeout=180)
    app.run()
    assert not app.exception, app.exception

    captions = " ".join(c.value for c in app.caption)
    assert "wear floor" in captions

    offer = next(b for b in app.button if "spare capacity" in b.label)
    offer.click().run()
    assert not app.exception, app.exception

    reasons = [df.value for df in app.dataframe if "Why" in df.value.columns]
    assert reasons and "member backup reserve" in set(reasons[0]["Why"])


# ------------------------------------------------------------------ operator UI


def test_the_banner_states_delivered_against_promised_and_what_is_at_risk() -> None:
    app = AppTest.from_file(APP, default_timeout=180)
    app.run()
    assert not app.exception, app.exception
    covered = " ".join(e.value for e in app.success)
    assert "Delivering" in covered and "kW at risk" in covered

    trigger = next(b for b in app.button if "Trigger" in b.label)
    trigger.click().run()
    assert not app.exception, app.exception
    at_risk = " ".join(e.value for e in app.error)
    assert re.search(r"Delivering [\d,]+ of [\d,]+ kW; [\d,]+ kW at risk", at_risk), at_risk


def test_rounding_never_reports_more_delivered_than_promised() -> None:
    assert dashboard.coverage_banner(499.6, 500.0) == "Delivering 499 of 500 kW; 1 kW at risk"
    assert dashboard.coverage_banner(500.0, 500.0) == "Delivering 500 of 500 kW; 0 kW at risk"
    # A fleet over-assigned by rounding still reads as exactly its commitment.
    assert dashboard.coverage_banner(500.4, 500.0) == "Delivering 500 of 500 kW; 0 kW at risk"
    assert dashboard.coverage_pct_text(499.6, 500.0, 99.92) == "99%"
    assert dashboard.coverage_pct_text(500.0, 500.0, 100.0) == "100%"


def test_the_fleet_map_renders_without_internet_tiles() -> None:
    frame = pd.DataFrame(
        [
            {
                "Device": "BAT-001",
                "Status": "Online",
                "Site": "Austin",
                "Zone": "LZ_AUSTIN",
                "lat": 30.3,
                "lon": -97.7,
                "Assigned kW": 3.0,
                "SoC %": 80,
                "size": 11,
            }
        ]
    )
    offline = dashboard.map_figure(frame, tiles=False)
    assert "map" not in offline.layout.to_plotly_json()
    assert offline.data[0].type == "scatter"  # plain x/y plot, no basemap request
    tiled = dashboard.map_figure(frame, tiles=True)
    assert tiled.data[0].type in {"scattermap", "scattermapbox"}


def test_the_member_app_does_not_claim_protection_while_contact_is_lost() -> None:
    app = AppTest.from_file(APP, default_timeout=180)
    app.run()
    trigger = next(b for b in app.button if "Trigger" in b.label)
    trigger.click().run()
    app.session_state["view"] = "Member App"
    app.run()
    assert not app.exception, app.exception

    text = " ".join(m.value for m in app.markdown)
    assert "lost contact" in text.lower()
    assert "still protecting your home" not in text
    assert "cannot confirm" in text
    labels = " ".join(m.label for m in app.metric)
    assert "last reported" in labels
