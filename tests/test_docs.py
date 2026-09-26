"""The submission documents have hard constraints: a word budget and a clock.

Both are easy to break with one more sentence, and neither is visible in a diff, so they are
asserted here rather than trusted.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gridsignal import demo_numbers
from gridsignal.drills import available_drills
from gridsignal.mesh.scenarios import available_scenarios

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


def test_roster_invents_no_names() -> None:
    """A template for the team to fill in, not a guess at who they are."""
    roster = (DOCS / "ROSTER.md").read_text()
    assert "your name here" in roster
    assert not re.search(r"@\w+\.(com|org|io)", roster)
