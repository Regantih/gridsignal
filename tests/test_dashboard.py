"""The Grid Signals headline must never show the scenario day on its own."""

from __future__ import annotations

import re
import sys
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from gridsignal import backup_ledger, degradation, holdout, judgment_report, why
from gridsignal.control_room.engine import ControlRoomEngine
from gridsignal.jev import incident as jev_incident

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import dashboard  # noqa: E402

#: Whole-app renders: CI runs the slow half as its own parallel job.
pytestmark = pytest.mark.slow

APP = str(Path(__file__).resolve().parents[1] / "app" / "dashboard.py")


#: Every view, rendered once for the whole module. Each render is a full app run, so
#: tests that only read a screen share one instead of paying for it again.
VIEWS = ("Control Room", "Member App", "Grid Signals", "Agent Mesh", "Why")


@pytest.fixture(scope="module")
def rendered_views() -> dict[str, AppTest]:
    views = {}
    for view in VIEWS:
        app = AppTest.from_file(APP, default_timeout=600)
        app.run()
        app.session_state["view"] = view
        app.run()
        assert not app.exception, (view, app.exception)
        views[view] = app
    return views


@pytest.fixture(scope="module")
def advanced_views() -> dict[str, AppTest]:
    """The same views with every extra panel on, for checks that must cover them all."""
    views = {}
    for view in VIEWS:
        app = AppTest.from_file(APP, default_timeout=600)
        app.run()
        app.session_state["view"] = view
        app.session_state["advanced"] = True
        app.run()
        assert not app.exception, (view, app.exception)
        views[view] = app
    return views


@pytest.fixture(scope="module")
def grid_signals(rendered_views: dict[str, AppTest]) -> AppTest:
    return rendered_views["Grid Signals"]


def fresh(view: str | None = None, timeout: int = 180) -> AppTest:
    """An app of its own, for a test that clicks something and changes fleet state."""
    app = AppTest.from_file(APP, default_timeout=timeout)
    app.run()
    if view is not None:
        app.session_state["view"] = view
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


def test_grid_signals_shows_the_wear_gate_per_battery_type(grid_signals: AppTest) -> None:
    text = markdown_text(grid_signals)
    assert "assumption" in text
    for model in degradation.WEAR_MODELS:
        assert model.label in text
    assert "equivalent full cycles" in text


def test_every_view_renders(rendered_views: dict[str, AppTest]) -> None:
    assert set(rendered_views) == set(VIEWS)
    for view, app in rendered_views.items():
        assert not app.exception, (view, app.exception)


def test_the_why_page_shows_the_numbers_the_code_computes_and_says_where_it_stops(
    rendered_views: dict[str, AppTest],
) -> None:
    app = rendered_views["Why"]

    text = markdown_text(app)
    headings = " ".join(h.value for h in app.subheader)
    page = why.build()
    for section in page.sections:
        assert section.title in headings
        for claim in section.claims:
            # Including the live benchmark: the page is built once per process, so
            # the screen and this call read the same measurement.
            assert claim.value in text, claim.label
    for limit in page.limits:
        assert limit in text
    # Every line on the screen names the command that reproduces it.
    commands = " ".join(block.value for block in app.code)
    for section in page.sections:
        for claim in section.claims:
            assert claim.command in commands


def test_agent_mesh_shows_the_registry_the_log_and_a_scenario_picker(
    rendered_views: dict[str, AppTest],
) -> None:
    app = rendered_views["Agent Mesh"]
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
    app = fresh("Agent Mesh")
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


def test_agent_mesh_shows_jev_answers_and_the_rules_comparison(
    rendered_views: dict[str, AppTest],
) -> None:
    app = rendered_views["Agent Mesh"]
    frames = [df.value for df in app.dataframe]
    answers = next(f for f in frames if "confidence" in f.columns)
    assert {"question", "answer", "probabilities", "latency (ms)"} <= set(answers.columns)
    assert "Root cause" in set(answers["question"])

    evaluation = next(f for f in frames if "root-cause accuracy" in f.columns)
    assert set(evaluation["decision layer"]) == {"rules-only", "jev"}

    text = " ".join(m.value for m in app.markdown)
    assert "Second opinion" in text
    assert "human approval required" in text
    assert "Jev has no approve path" in text


def test_incident_panel_shows_one_verdict_that_cannot_contradict_the_button() -> None:
    """One card carries the verdict, the gate reading and who approves.

    Two cards could badge the same incident differently; the point of the merge is that
    the words next to the Approve button can only ever say a human approves.
    """
    app = fresh()
    trigger = next(b for b in app.button if "Trigger" in b.label)
    trigger.click().run()
    assert not app.exception, app.exception
    text = " ".join(m.value for m in app.markdown)
    assert text.count("gs-kicker'>Verdict") == 1
    assert "Second opinion" not in text
    assert "human approval required" in text
    assert "A human approves either way — Jev has no approve path" in text
    assert any(label in text for label in ("Jev live", "Jev (recorded answer)", "Jev offline"))
    assert any("Approve" in b.label for b in app.button)


def test_the_pending_verdict_never_claims_the_fleet_is_already_acting() -> None:
    """'act' beside 'human approval required' reads as a machine that already moved."""
    app = fresh()
    trigger = next(b for b in app.button if "Trigger" in b.label)
    trigger.click().run()
    assert not app.exception, app.exception

    card = next(m.value for m in app.markdown if "gs-kicker'>Verdict" in m.value)
    assert "human approval required" in card
    assert ">act<" not in card
    assert "Why:</b> Acting" not in card
    assert "once approved" in card


def test_control_room_accounts_for_spare_capacity_and_can_offer_it() -> None:
    app = fresh()
    app.session_state["advanced"] = True  # spare capacity is not on the demo screen
    app.run()
    captions = " ".join(c.value for c in app.caption)
    assert "wear floor" in captions

    offer = next(b for b in app.button if "spare capacity" in b.label)
    offer.click().run()
    assert not app.exception, app.exception

    reasons = [df.value for df in app.dataframe if "Why" in df.value.columns]
    assert reasons and "member backup reserve" in set(reasons[0]["Why"])


def test_loading_a_telemetry_file_moves_the_fleet_the_control_room_shows() -> None:
    """Replay mode: the bundled sample is imported into the same state, not a mock."""
    app = fresh()
    before = app.session_state["engine"].snapshot().offline

    next(b for b in app.button if b.label == "Import telemetry").click().run()
    assert not app.exception, app.exception

    result = app.session_state["telemetry_result"]
    assert result.rejected == () and len(result.applied) == 48
    assert app.session_state["engine"].snapshot().offline > before
    assert "Imported telemetry" in markdown_text(app)


def test_a_telemetry_file_with_bad_rows_shows_every_rejection_reason() -> None:
    app = fresh()
    app.session_state["telemetry_choice"] = "Synthetic bad rows (one per rejection reason)"
    app.run()
    next(b for b in app.button if b.label == "Import telemetry").click().run()
    assert not app.exception, app.exception

    table = next(df.value for df in app.dataframe if "rejected because" in df.value.columns)
    reasons = " ".join(table["rejected because"])
    for reason in ("stale:", "malformed:", "out of range:", "unknown device"):
        assert reason in reasons
    next(b for b in app.button if b.label == "Clear import").click().run()
    assert app.session_state["telemetry_result"] is None


# ------------------------------------------------------------------ operator UI


def test_the_banner_states_delivered_against_promised_and_what_is_at_risk() -> None:
    app = fresh()
    covered = " ".join(e.value for e in app.success)
    assert "Delivering" in covered and "kW at risk" in covered

    trigger = next(b for b in app.button if "Trigger" in b.label)
    trigger.click().run()
    assert not app.exception, app.exception
    at_risk = " ".join(e.value for e in app.error)
    assert re.search(r"Delivering [\d,]+ of [\d,]+ kW; [\d,]+ kW at risk", at_risk), at_risk


def test_rounding_never_reports_more_delivered_than_promised() -> None:
    assert dashboard.coverage_banner(498.4, 500.0) == "Delivering 498 of 500 kW; 2 kW at risk"
    assert dashboard.coverage_banner(500.0, 500.0) == "Delivering 500 of 500 kW; 0 kW at risk"
    # A fleet over-assigned by rounding still reads as exactly its commitment.
    assert dashboard.coverage_banner(500.4, 500.0) == "Delivering 500 of 500 kW; 0 kW at risk"
    assert dashboard.coverage_pct_text(498.4, 500.0, 99.68) == "99%"
    assert dashboard.coverage_pct_text(500.0, 500.0, 100.0) == "100%"


def test_the_banner_colour_and_its_words_can_never_disagree() -> None:
    """A sub-kW rounding remainder used to read green while the words said 'kW at risk'."""
    for committed, target in ((35_999.6, 36_000.0), (500.0, 500.0), (500.4, 500.0)):
        assert dashboard.covered(committed, target)
        assert "0 kW at risk" in dashboard.coverage_banner(committed, target)
        assert dashboard.coverage_pct_text(committed, target, 100.004) == "100%"
    assert not dashboard.covered(35_999.0, 36_000.0)
    assert "1 kW at risk" in dashboard.coverage_banner(35_999.0, 36_000.0)


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
    app = fresh()
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


def test_control_room_shows_the_timeline_and_logs_an_override_with_its_reason() -> None:
    app = fresh()
    next(b for b in app.button if "Trigger" in b.label).click().run()
    next(b for b in app.button if "Approve Recovery" in b.label).click().run()
    assert not app.exception, app.exception

    text = markdown_text(app)
    for stage in ("DETECT", "DIAGNOSE", "REASSIGN", "RECOVER", "DOLLARS"):
        assert stage in text
    assert "raw alarms grouped into" in text

    override = next(b for b in app.button if b.label == "Override award")
    override.click().run()
    assert app.error and "reason" in app.error[0].value

    app.text_input("override_reason").set_value("crew on the street").run()
    next(b for b in app.button if b.label == "Override award").click().run()
    assert not app.exception, app.exception
    assert any("logged with your reason" in s.value for s in app.success)
    logged = [df.value for df in app.dataframe if "Reason" in df.value.columns]
    assert logged and "crew on the street" in set(logged[0]["Reason"])


# --- design pass: one number format, one set of units, no jargon without its meaning ---

#: Every shape a metric value is allowed to take. Anything with a digit in it that does
#: not match one of these is a number formatted its own way, which is the thing the
#: design pass exists to stop.
VALUE_FORMATS = (
    r"-?\$[\d,]+(?:\.\d{2})?(?:/MWh|/day)?",  # money, minus sign outside the dollar
    r"[\d,]+(?:\.\d+)? (?:kW|kWh|MWh)",  # power and energy
    r"[\d,]+(?:\.\d+)? (?:h|s)",  # durations
    r"[\d,]+%",
    r"[\d,]+ of [\d,]+",  # counts out of counts
    r"[\d,]+ / [\d,]+",  # a pair the label names in the same order
    r"[\d,]+(?:\.\d+)?",
)


def test_every_metric_value_uses_one_of_the_agreed_number_formats(
    rendered_views: dict[str, AppTest],
) -> None:
    allowed = re.compile("|".join(f"(?:{p})" for p in VALUE_FORMATS))
    for view, app in rendered_views.items():
        for m in app.metric:
            if not any(ch.isdigit() for ch in m.value):
                continue  # a word, not a number: "Active", "no fault", "whole fleet"
            match = allowed.fullmatch(m.value)
            assert match, f"{view} / {m.label}: {m.value!r} is formatted its own way"


def test_dollar_amounts_never_put_the_minus_sign_inside(
    rendered_views: dict[str, AppTest],
) -> None:
    for view, app in rendered_views.items():
        texts = [m.value for m in app.metric] + [c.value for c in app.caption]
        for text in texts:
            assert "$-" not in text, f"{view}: {text!r}"


def test_metric_context_lines_are_captions_not_deltas(
    rendered_views: dict[str, AppTest],
) -> None:
    """The second line under a number is context, so it never gets an arrow or a colour."""
    for view, app in rendered_views.items():
        for m in app.metric:
            assert not m.delta, f"{view} / {m.label}: {m.delta!r} would render as a trend"


def test_a_metric_whose_label_uses_jargon_carries_the_explanation(
    rendered_views: dict[str, AppTest],
) -> None:
    for view, app in rendered_views.items():
        for m in app.metric:
            meaning = dashboard.explain(m.label)
            if meaning is None:
                continue
            assert m.help, f"{view} / {m.label}: jargon with no tooltip"


def test_every_glossary_entry_is_one_plain_sentence() -> None:
    for word, meaning in dashboard.GLOSSARY.items():
        assert meaning.endswith("."), word
        assert len(meaning.split()) <= 30, word
        assert not meaning.lower().startswith(word), word  # no circular definitions


def test_the_formatters_agree_on_signs_separators_and_units() -> None:
    assert dashboard.money(-0.2) == "-$0.20"
    assert dashboard.money(0) == "$0.00"
    assert dashboard.money(12345.678, cents=False) == "$12,346"
    assert dashboard.power(10500) == "10,500 kW"
    assert dashboard.energy(1182.4) == "1,182 kWh"
    assert dashboard.hours(16.68) == "16.7 h"
    assert dashboard.seconds(420) == "420 s"
    assert dashboard.ratio(6, 7) == "6 of 7"


#: A tile in a seven-column row at 1440 px is about 190 px wide, which holds roughly
#: this much before Streamlit clips it with an ellipsis. The video is shot at 1440.
LABEL_BUDGET = 24
VALUE_BUDGET = 13


def test_no_tile_label_or_value_is_long_enough_to_truncate_at_1440px(
    rendered_views: dict[str, AppTest],
    advanced_views: dict[str, AppTest],
) -> None:
    for views in (rendered_views, advanced_views):
        for view, app in views.items():
            for m in app.metric:
                assert len(m.label) <= LABEL_BUDGET, f"{view}: {m.label!r} clips"
                assert len(str(m.value)) <= VALUE_BUDGET, f"{view} / {m.label}: {m.value!r} clips"


def test_fleet_energy_changes_unit_before_the_digits_overflow_the_tile() -> None:
    """244,954 kWh clipped at 1440 px; the same number in MWh does not."""
    assert dashboard.energy_tile(1182.4) == "1,182 kWh"
    assert dashboard.energy_tile(244_954.0) == "245.0 MWh"
    assert len(dashboard.energy_tile(1_000_000.0)) <= VALUE_BUDGET


def test_tables_show_formatted_numbers_and_words_not_identifiers(
    rendered_views: dict[str, AppTest],
) -> None:
    """A judge reading 'base_core' or '176039.41' is reading our variables, not a report."""
    home = next(
        df.value
        for df in rendered_views["Control Room"].dataframe
        if "Unit type" in df.value.columns
    )
    assert set(home["Unit type"]) <= set(dashboard.UNIT_TYPE_LABEL.values())
    assert all(re.fullmatch(r"-?[\d,]+\.\d", v) for v in home["Export kW"])

    tenants = next(
        df.value
        for df in rendered_views["Control Room"].dataframe
        if "Control authority" in df.value.columns
    )
    assert set(tenants["Control authority"]) <= set(dashboard.CONTROLLER_LABEL.values())


def test_inline_jargon_is_underlined_with_its_meaning_on_hover() -> None:
    markup = dashboard.term("headroom")
    assert "gs-term" in markup
    assert dashboard.GLOSSARY["headroom"] in markup


def test_one_design_system_drives_every_card() -> None:
    css = dashboard.CSS
    for token in (
        "--gs-surface",
        "--gs-line",
        "--gs-ink",
        "--gs-muted",
        "--gs-step",
        "--gs-radius",
    ):
        assert token in css
    # Spacing and card geometry come from the tokens, never from a one-off pixel value.
    assert "padding: 1rem 1.15rem" in css
    assert css.count("var(--gs-step)") >= 5


def test_small_print_keeps_its_dollar_amounts_readable(
    rendered_views: dict[str, AppTest],
) -> None:
    """Streamlit typesets a pair of unescaped dollar signs as LaTeX and eats the number."""
    for view, app in rendered_views.items():
        for c in app.caption:
            for pos, ch in enumerate(c.value):
                if ch == "$":
                    assert pos and c.value[pos - 1] == "\\", f"{view}: {c.value!r}"


def test_raw_html_never_carries_a_latex_escape_into_the_operator_view() -> None:
    """Captions need `\\$`; raw HTML does not, and a stray escape prints as `\\$4,812`."""
    app = fresh()
    trigger = next(b for b in app.button if "Trigger" in b.label)
    trigger.click().run()
    assert not app.exception, app.exception
    for block in app.markdown:
        if "<div" in block.value:
            assert "\\$" not in block.value, block.value


def test_the_map_sample_reaches_every_zone_rather_than_one_blob() -> None:
    """Devices are laid out round-robin, so a flat stride can draw one city only."""
    eng = ControlRoomEngine(fleet_size=5_000)
    sample = dashboard.map_devices(eng.devices)

    assert len(sample) <= dashboard.MAP_MARKERS + 200  # unhealthy devices are always kept
    zones = {d.zone for d in sample}
    assert zones == {d.zone for d in eng.devices}
    per_zone = Counter(d.zone for d in sample)
    assert min(per_zone.values()) >= 0.5 * max(per_zone.values()), per_zone


def test_the_operator_summary_and_the_task_list_are_each_rendered_once() -> None:
    app = fresh()
    trigger = next(b for b in app.button if "Trigger" in b.label)
    trigger.click().run()
    approve = next(b for b in app.button if "Approve" in b.label)
    approve.click().run()
    assert not app.exception, app.exception

    text = " ".join(m.value for m in app.markdown)
    assert "Operator summary" not in text  # the recovery banner already says it
    assert text.count("Who is doing what") == 1


def test_the_blind_pack_is_scored_once_per_process_and_warmed_before_the_first_click() -> None:
    """The first 10,000-device fault must not pay for replaying every held-out drill."""
    first = judgment_report.cached_build()
    assert judgment_report.cached_build() is first

    started: list[str] = []
    real = judgment_report.cached_build

    def record() -> judgment_report.Report:
        started.append("warmed")
        return real()

    state = SimpleNamespace(prewarmed=False, get=lambda key, default=None: False)
    with mock.patch.object(judgment_report, "cached_build", record):
        with mock.patch.object(dashboard.st, "session_state", state):
            dashboard.prewarm()
            state.get = lambda key, default=None: state.prewarmed
            dashboard.prewarm()  # idempotent: one warm per session, not one per rerun
    for _ in range(100):
        if started:
            break
        time.sleep(0.05)
    assert started == ["warmed"]
    assert state.prewarmed is True


def test_a_ten_thousand_device_fault_answers_quickly_once_the_pack_is_warm() -> None:
    """Camera timing: the trigger is engine work, not a held-out replay on the click."""
    judgment_report.cached_build()
    eng = ControlRoomEngine(fleet_size=10_000)
    start = time.perf_counter()
    incident = eng.trigger_device_failure()
    _, decision = jev_incident.ask(eng, incident)
    _, _, verdict = jev_incident.judge_incident(eng, incident)
    elapsed = time.perf_counter() - start

    assert incident.lost_kw > 0
    assert verdict.action is not None and not decision.gate_clear
    assert elapsed < 2.0, f"{elapsed:.2f}s"


def test_the_backup_ledger_card_shows_the_promise_and_the_counterfactual(
    rendered_views: dict[str, AppTest],
) -> None:
    """The default Control Room carries the member's promise, audited and stress-tested."""
    app = rendered_views["Control Room"]
    text = markdown_text(app) + " ".join(c.value for c in app.caption)
    assert "Backup promise ledger" in text
    labels = {m.label: m.value for m in app.metric}
    assert labels["Intervals taking backup"] == "0"
    assert int(labels["Same walk, floor removed"].replace(",", "")) > 0
    frames = [df for df in app.dataframe if "Held h without the floor" in list(df.value.columns)]
    assert len(frames) == 1
    held = frames[0].value
    assert len(held) == backup_ledger.EVENT_STEPS
    assert set(held["Promise kept"]) == {"yes"}
    assert min(float(v) for v in held["Held h"]) >= min(
        float(v) for v in held["Held h without the floor"]
    )


def test_the_what_if_console_prices_a_scenario_without_dispatching_it(
    rendered_views: dict[str, AppTest],
) -> None:
    """The default Control Room answers a hypothetical and says it changed nothing."""
    app = rendered_views["Control Room"]
    text = markdown_text(app)
    assert "What-if console" in text
    assert "Nothing is dispatched" in text
    labels = {m.label: m.value for m in app.metric}
    assert labels["Coverable by the fleet"].endswith("kW")
    assert labels["Still exposed"].startswith("$")


def test_the_what_if_console_refuses_a_scenario_it_cannot_read() -> None:
    app = fresh()
    app.text_input("whatif_text").set_value("make me a sandwich").run()
    assert not app.exception, app.exception
    assert any("Unrecognised scenario" in w.value for w in app.warning)


#: Panels the demo script does not narrate. They exist, but not on the default screen.
ADVANCED_PANELS = (
    "Full-fleet scarcity replay",
    "Spare capacity",
    "Storm reserve policy",
    "Discharge first under congestion",
)


def test_the_default_control_room_hides_what_the_demo_does_not_narrate(
    rendered_views: dict[str, AppTest],
) -> None:
    app = rendered_views["Control Room"]
    assert app.session_state["advanced"] is False
    text = markdown_text(app) + " ".join(s.label for s in app.selectbox)
    for panel in ADVANCED_PANELS:
        assert panel not in text, f"{panel} is on the default Control Room"
    assert "Fleet map" in [h.value for h in app.subheader]


def test_the_advanced_toggle_brings_the_extra_panels_back() -> None:
    app = fresh()
    app.session_state["advanced"] = True
    app.run()
    assert not app.exception, app.exception
    text = markdown_text(app) + " ".join(s.label for s in app.selectbox)
    for panel in ADVANCED_PANELS:
        assert panel in text, f"{panel} is missing with Advanced on"


def test_the_member_app_keeps_mutual_aid_behind_advanced(
    rendered_views: dict[str, AppTest],
) -> None:
    assert "Neighbour mutual aid" not in markdown_text(rendered_views["Member App"])
    app = fresh("Member App")
    app.session_state["advanced"] = True
    app.run()
    assert "Neighbour mutual aid" in markdown_text(app)


def test_the_agent_mesh_keeps_the_install_wave_behind_advanced(
    rendered_views: dict[str, AppTest],
) -> None:
    mesh = rendered_views["Agent Mesh"]
    assert "Install wave" not in " ".join(h.value for h in mesh.subheader)
    app = fresh("Agent Mesh")
    app.session_state["advanced"] = True
    app.run()
    assert "Install wave" in " ".join(h.value for h in app.subheader)
