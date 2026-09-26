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
    for view in ("Control Room", "Member App", "Grid Signals"):
        app = AppTest.from_file(APP, default_timeout=180)
        app.run()
        app.session_state["view"] = view
        app.run()
        assert not app.exception, (view, app.exception)
