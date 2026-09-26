"""The Grid Signals headline must never show the scenario day on its own."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from gridsignal import holdout

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
    assert {"call_for_capacity", "award_proposed", "approval"} <= set(log["kind"])
    assert [s.label for s in app.selectbox] == ["Replay"]
    assert "Simulation only" in " ".join(m.value for m in app.markdown)


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
