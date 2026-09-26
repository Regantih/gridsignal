"""The submission documents have hard constraints: a word budget and a clock.

Both are easy to break with one more sentence, and neither is visible in a diff, so they are
asserted here rather than trusted.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from gridsignal import demo_numbers, replay
from gridsignal.control_room import ControlRoomEngine
from gridsignal.drills import available_drills
from gridsignal.jev import evaluate
from gridsignal.mesh.scenarios import available_scenarios

#: Every checked document re-runs the command behind its numbers: the slow half.
pytestmark = pytest.mark.slow

DOCS = Path(__file__).resolve().parents[1] / "docs"
SECTIONS = ("Problem", "Who it helps", "Solution", "Impact")


def _writeup_body(text: str) -> str:
    """The prose only: headings dropped, markdown emphasis stripped."""
    lines = [line for line in text.splitlines() if not line.startswith("#")]
    return re.sub(r"[*_`]", "", "\n".join(lines))


def test_writeup_is_between_150_and_300_words() -> None:
    words = _writeup_body((DOCS / "WRITEUP.md").read_text()).split()
    assert 150 <= len(words) <= 300, f"write-up is {len(words)} words"


def test_writeup_sections_appear_in_the_required_order() -> None:
    body = _writeup_body((DOCS / "WRITEUP.md").read_text())
    positions = []
    for section in SECTIONS:
        index = body.find(f"{section}.")
        assert index >= 0, f"write-up is missing the {section!r} section"
        positions.append(index)
    assert positions == sorted(positions), "sections are out of order"


def test_submission_carries_the_same_write_up() -> None:
    """Judges read SUBMISSION.md; a drifting copy of the write-up is a wrong answer."""
    writeup = _writeup_body((DOCS / "WRITEUP.md").read_text())
    submission = _writeup_body((DOCS / "SUBMISSION.md").read_text())
    for paragraph in (p.strip() for p in writeup.split("\n\n") if p.strip()):
        assert paragraph in submission


@pytest.mark.parametrize("name", ["SUBMISSION.md", "WRITEUP.md", "JUDGING_MAP.md", "ROSTER.md"])
def test_submission_documents_exist(name: str) -> None:
    assert (DOCS / name).read_text().strip()


SCRIPT_LIMIT_S = 285  # 4:45
SPOKEN_WORD_LIMIT = 600
# Numbers a presenter says out loud: "$4,812", "47%", "10,000", "5x".
NUMBER = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")


def _script_rows(text: str) -> list[str]:
    """The rows of the timed script table."""
    return [line for line in text.splitlines() if line.startswith("| **")]


def _spoken(text: str) -> str:
    """Only the words in quotes: the stage directions are not read aloud."""
    return " ".join(re.findall(r'"([^"]+)"', "\n".join(_script_rows(text))))


def test_demo_script_is_timed_and_fits_under_four_forty_five() -> None:
    text = (DOCS / "DEMO.md").read_text()
    stamps = re.findall(r"\*\*(\d+):(\d\d)\*\*", text)
    assert stamps, "the demo script has no timestamps"
    seconds = [int(m) * 60 + int(s) for m, s in stamps]
    assert seconds == sorted(seconds), "timestamps run backwards"
    assert seconds[0] == 0
    assert seconds[-1] < SCRIPT_LIMIT_S, f"the script starts its last beat at {seconds[-1]}s"


def test_demo_script_lands_in_the_four_thirty_to_four_fifty_five_window() -> None:
    """The stated run time is a rehearsal target, not a guess: it has to be in the window."""
    text = (DOCS / "DEMO.md").read_text()
    ends = re.search(r"Ends at (\d+):(\d\d)\.", text)
    assert ends, "the script does not say when it ends"
    total = int(ends.group(1)) * 60 + int(ends.group(2))
    assert 270 <= total <= 295, f"the script ends at {total}s, outside 4:30-4:55"
    stamps = re.findall(r"\*\*(\d+):(\d\d)\*\*", text)
    last = int(stamps[-1][0]) * 60 + int(stamps[-1][1])
    assert last < total, "the last scene starts after the script ends"


RUBRIC_LINES = {
    "Technical Execution / Completeness",
    "Technical Execution / Depth",
    "Fit to Track / Problem",
    "Fit to Track / Why",
    "Value / Insight",
    "Value / Usability",
    "Innovation / Creativity",
    "Innovation / Performance",
}


def test_every_demo_scene_names_a_rubric_line_and_a_file_that_exists() -> None:
    """A judge scoring from the video should see what each scene is evidence for."""
    root = DOCS.parent
    for row in _script_rows((DOCS / "DEMO.md").read_text()):
        mapping = row.rstrip("| ").rsplit("|", 1)[-1].strip()
        line, _, path = mapping.partition("—")
        assert line.strip() in RUBRIC_LINES, row
        target = root / path.strip().strip("`")
        assert target.exists(), f"{target} does not exist"


def test_demo_script_is_under_six_hundred_spoken_words() -> None:
    words = _spoken((DOCS / "DEMO.md").read_text()).split()
    assert len(words) < SPOKEN_WORD_LIMIT, f"the script speaks {len(words)} words"


def test_demo_script_only_asks_for_scenarios_the_picker_offers() -> None:
    """Held-out drills run from the CLI; the script must not send a judge hunting."""
    offered = {p.stem for p in available_scenarios()}
    text = (DOCS / "DEMO.md").read_text()
    for named in re.findall(r"`([a-z_]+)`", "\n".join(_script_rows(text))):
        if named.startswith("holdout") or named in {p.stem for p in available_drills()}:
            raise AssertionError(f"{named} is not in the dashboard scenario picker")
        if named.endswith("_agent") or named.endswith("_outage"):
            assert named in offered, f"{named} is not a bundled chaos scenario"


def test_every_number_spoken_in_the_demo_is_reproducible_from_one_command() -> None:
    """`python -m gridsignal.demo_numbers` has to print what the presenter says."""
    printed = demo_numbers.report()
    missing = [
        n for n in NUMBER.findall(_spoken((DOCS / "DEMO.md").read_text())) if n not in printed
    ]
    assert not missing, f"not printed by gridsignal.demo_numbers: {missing}"


def test_the_script_describes_the_screen_the_presenter_will_be_looking_at() -> None:
    """A line that names a button the app does not have costs the take, not a retry."""
    rows = {
        row.split("|")[1].strip().strip("*"): row
        for row in _script_rows((DOCS / "DEMO.md").read_text())
    }
    assert "of 36,000 kW committed" in rows["0:00"] or "36,000 kW committed" in rows["0:00"]
    assert "battery is still paused" in rows["2:50"]  # member.py says paused, not resolved
    assert "Run scenario" not in rows["3:15"]  # picking the scenario runs it
    for stamp in ("2:10", "4:05"):
        spoken = rows[stamp].split('"')[1]
        assert len(spoken.split()) <= 60, f"{stamp} speaks {len(spoken.split())} words"


def test_the_dry_run_counts_the_sweep_and_the_suite_it_actually_has() -> None:
    """A rescore that quotes last week's counts is a rescore nobody reran."""
    sys.path.insert(0, str(DOCS.parent / "scripts"))
    import no_crash_sweep

    text = (DOCS / "JUDGE_DRY_RUN.md").read_text()
    cli = re.search(r"drives (\d+) CLI entry points", text)
    tests = re.search(r"(\d[\d,]*) tests pass with no key", text)
    assert cli and tests, "the dry run no longer states what it ran"
    assert int(cli.group(1)) == len(no_crash_sweep.CLI_COMMANDS)

    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--collect-only", "-p", "no:randomly"],
        cwd=DOCS.parent,
        capture_output=True,
        text=True,
        check=True,
    )
    counted = re.search(r"(\d+) tests collected", collected.stdout)
    assert counted, collected.stdout[-2000:]
    claimed = int(tests.group(1).replace(",", ""))
    assert claimed == int(counted.group(1)), (
        f"the dry run says {claimed} tests, pytest collects {counted.group(1)}"
    )


def test_roster_invents_no_names() -> None:
    """A template for the team to fill in, not a guess at who they are."""
    roster = (DOCS / "ROSTER.md").read_text()
    assert "your name here" in roster
    assert not re.search(r"@\w+\.(com|org|io)", roster)


#: Every claim the docs are allowed to quote a headline number for, matched wherever it is
#: phrased. A figure retired by a code change therefore cannot survive in prose in one file
#: while the others are updated.
CLAIMS: tuple[tuple[str, str], ...] = (
    (r"\$([\d,]+) at risk", "dollars_at_risk"),
    (r"\$([\d,]+) recovered", "dollars_recovered"),
    (r"\$([\d,]+)/day across 10,000 batteries", "scenario_fleet_usd"),
    (r"[Hh]ome-first[^|\n]{0,60}?(\d) of 7", "holdout_days_won"),
    (r"[Hh]ome-first[^|\n]{0,80}?mean ([+\u2212-]?)\$(\d+\.\d\d)", "holdout_mean_usd"),
    (r"[Hh]ome-first[^|\n]{0,110}?median \+?\$(\d+\.\d\d)", "holdout_median_usd"),
    # The naive schedule can flatter or damn the policy; the absolute comparison cannot.
    (r"does nothing[^|\n]{0,60}?median \$(\d+\.\d\d)", "holdout_vs_nothing_median_usd"),
    (r"do-nothing battery, median \$(\d+\.\d\d)", "holdout_vs_nothing_median_usd"),
    (r"[Gg]rid-only[^|\n]{0,60}?(\d) of 7", "grid_only_days_won"),
    (r"[Gg]rid-only[^|\n]{0,80}?mean ([+\u2212-]?)\$(\d+\.\d\d)", "grid_only_mean_usd"),
    (
        r"[Oo]nly (\d+)% of (?:a battery\'s capturable value"
        r"|a scarcity day\'s capturable|the value a battery could have captured)",
        "scarcity_visible_share",
    ),
    # The backup promise: the audited walk and the same walk with the floor removed, so
    # the zero can never be quoted without the counterfactual that makes it mean something.
    (r"([\d,]+) intervals, 0 that took a member\'s backup", "backup_intervals_audited"),
    (r"([\d,]+) intervals breach, across", "backup_unguarded_violations"),
    (r"spending ([\d,]+\.\d) kWh of\s+promised backup", "backup_unguarded_kwh"),
    # The scenario day is uplift over the naive schedule, so the gross side is checked too
    # and neither can be quoted without the other drifting.
    (r"\$([\d,]+\.\d\d) of export revenue", "scenario_battery_revenue_usd"),
    (r"\$([\d,]+\.\d\d) for the naive schedule", "scenario_battery_naive_usd"),
    (r"\$([\d,]+\.\d\d) per\s+battery of uplift", "scenario_battery_uplift_usd"),
    (r"\$([\d,]+)/day gross", "scenario_fleet_revenue_usd"),
    # The judgment model's calibration, in prose and in the README's table. The wording
    # and the figures both come from `python -m gridsignal.judgment_report`.
    (r"fitted half moves (\d+)% to \d+%", "calibration_fitted_before"),
    (r"fitted half moves \d+% to (\d+)%", "calibration_fitted_after"),
    (r"held-out agreement moves (\d+)% to \d+%", "calibration_holdout_before"),
    (r"held-out agreement moves \d+% to (\d+)%", "calibration_holdout_after"),
    (r"\+(\d+) of 48 episodes", "calibration_episodes_moved"),
    (r"\| as committed \| (\d+)% \| \d+% \|", "calibration_fitted_before"),
    (r"\| as committed \| \d+% \| (\d+)% \|", "calibration_holdout_before"),
    (r"\| after tuning \| (\d+)% \| \d+% \|", "calibration_fitted_after"),
    (r"\| after tuning \| \d+% \| (\d+)% \|", "calibration_holdout_after"),
    # The ancillary split inside the ERCOT ADER pilot rules, and the labelled comparison
    # against all five products. Both come from `python -m gridsignal.ancillary`.
    (r"pilot rules[^|\n]{0,80}?median \**\+?\$(\d+\.\d\d)", "ancillary_median_usd"),
    (r"pilot rules[^|\n]{0,110}?mean \**\+?\$(\d+\.\d\d)", "ancillary_mean_usd"),
    (r"2024-05-08 alone carrying (\d+)%", "ancillary_top_day_share"),
    (r"all five products[^|\n]{0,90}?median of \$(\d+\.\d\d)", "unrestricted_median_usd"),
    (r"all five products[^|\n]{0,110}?mean of \$(\d+\.\d\d)", "unrestricted_mean_usd"),
    # Base's two business models, from `python -m gridsignal.business`. The break-even
    # access fee has two readings and neither may be quoted without the other holding.
    (
        r"battery alone[^|]{0,120}?\*{0,2}\$([\d,]+\.\d\d) per battery-month",
        "break_even_battery_month_usd",
    ),
    (
        r"retail relationship[^|]{0,120}?\*{0,2}\$([\d,]+\.\d\d) per battery-month",
        "break_even_month_usd",
    ),
    (r"\$([\d,]+\.\d\d) per kW-month", "break_even_kw_month_usd"),
    (r"\$(\d+\.\d\d) per battery per day of wholesale value", "certainty_cost_usd"),
    (r"promised\s+backup on \*{0,2}(\d) of 7 days", "partner_unclamped_breaches"),
    # The what-if console's two published answers, from `python -m gridsignal.whatif`.
    (r"drops ([\d,]+\.\d) kW", "whatif_zone_lost_kw"),
    (r"\*{0,2}(\d+) devices in LZ_HOUSTON\*{0,2}", "whatif_zone_devices"),
    (r"\$([\d,]+\.\d\d) of exposure", "whatif_zone_at_risk_usd"),
    (r"\*{0,2}([\d,]+\.\d) kW uncommitted", "whatif_spike_kw"),
    (r"\$([\d,]+\.\d\d) of upside", "whatif_spike_usd"),
)

#: The documents a judge reads. docs/DEMO.md has its own, stricter check: every number the
#: presenter speaks has to appear in the same command's output.
CHECKED_DOCS = (
    "README.md",
    "docs/WRITEUP.md",
    "docs/SUBMISSION.md",
    "docs/JUDGING_MAP.md",
    "docs/JUDGE_DRY_RUN.md",
)


@pytest.fixture(scope="module")
def canonical() -> dict[str, str]:
    """The one command's answers, run once for the whole file rather than per claim."""
    return demo_numbers.canonical()


@pytest.mark.parametrize("pattern,claim", CLAIMS)
def test_headline_numbers_in_every_doc_match_the_one_command(
    pattern: str, claim: str, canonical: dict[str, str]
) -> None:
    """README, WRITEUP, SUBMISSION and JUDGING_MAP quote one value per claim."""
    expected = canonical[claim].rstrip("%")
    found = 0
    for name in CHECKED_DOCS:
        text = (DOCS.parent / name).read_text()
        for match in re.findall(pattern, text):
            sign, number = match if isinstance(match, tuple) else ("", match)
            quoted = ("-" if sign in "\u2212-" and sign else "") + number
            found += 1
            assert quoted == expected, (
                f"{name} quotes {quoted!r} for {claim}, gridsignal.demo_numbers says {expected!r}"
            )
    assert found, f"no document states the {claim} claim any more"


def test_replay_figures_in_the_docs_come_from_the_replay_itself() -> None:
    """README and JUDGING_MAP quote the full-fleet replay; the run has to still say so."""
    result = replay.run()
    for name in ("README.md", "docs/JUDGING_MAP.md"):
        text = (DOCS.parent / name).read_text()
        assert f"${result.dollars_recovered:,.0f}" in text
        assert f"{result.recovered_share:.0%}" in text
        assert f"{result.phantom_kw_rejected:,.0f} kW" in text


def test_the_model_ablation_in_the_docs_matches_the_ablation_itself() -> None:
    """README and JUDGING_MAP claim what the recorded Jev answers change; prove it."""
    deltas = evaluate.fixture_deltas()
    same_kw = sum(not d.changed for d in deltas)
    different_cause = sum(d.rules_root_cause != d.jev_root_cause for d in deltas)
    for name in ("README.md", "docs/JUDGING_MAP.md"):
        text = (DOCS.parent / name).read_text()
        assert f"{same_kw} of {len(deltas)}" in text
        assert f"root cause differs in {different_cause}" in text or (
            f"root cause in {different_cause}" in text
        )


def test_the_normal_day_at_risk_figure_in_the_readme_is_what_the_engine_prices() -> None:
    """README contrasts the scarcity day with the 48-device normal day; price it."""
    engine = ControlRoomEngine(fleet_size=48)
    incident = engine.trigger_device_failure()
    text = (DOCS.parent / "README.md").read_text()
    assert f"versus ${incident.dollars_at_risk:,.2f} on the 48-device normal day" in text


def test_the_jev_latency_in_the_docs_is_the_recorded_round_trip() -> None:
    """The live median comes off the recorded answers, so no document may invent one."""
    summary = evaluate.evaluate().summary("jev")
    assert summary is not None
    median = f"{summary.median_latency_ms:.0f} ms"
    for name in ("README.md", "docs/JUDGING_MAP.md"):
        text = (DOCS.parent / name).read_text()
        quoted = re.findall(r"median(?: decision latency)?[^|\n]*?(\d+) ms", text)
        assert quoted, f"{name} no longer states the Jev median latency"
        for value in quoted:
            assert f"{value} ms" == median, f"{name} quotes {value} ms, the run says {median}"


def _perf_stage_ms(stage: str) -> float:
    """One stage's after-p50 from the 10,000-battery table in docs/PERFORMANCE.md."""
    text = (DOCS / "PERFORMANCE.md").read_text()
    table = text.split("### 10,000 batteries")[1].split("### 100,000 batteries")[0]
    row = next(line for line in table.splitlines() if line.startswith(f"| {stage} "))
    return float(row.split("|")[3].strip().replace(",", ""))


def test_the_readme_timings_add_up_to_the_performance_table() -> None:
    """README rounds PERFORMANCE.md's stages; the two may not drift apart."""
    control_room = sum(
        _perf_stage_ms(s) for s in ("build fleet", "detect incident", "recover after approval")
    )
    mesh = sum(
        _perf_stage_ms(s) for s in ("publish signed cards", "heartbeat sweep", "negotiate one call")
    )
    text = (DOCS.parent / "README.md").read_text()
    assert f"takes ~{control_room:.0f} ms of" in text
    assert f"auction takes ~{mesh:.0f} ms" in text
