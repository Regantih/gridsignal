"""The submission documents have hard constraints: a word budget and a clock.

Both are easy to break with one more sentence, and neither is visible in a diff, so they are
asserted here rather than trusted.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

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


def test_demo_script_is_timed_and_fits_under_five_minutes() -> None:
    text = (DOCS / "DEMO.md").read_text()
    stamps = re.findall(r"\*\*(\d+):(\d\d)\*\*", text)
    assert stamps, "the demo script has no timestamps"
    seconds = [int(m) * 60 + int(s) for m, s in stamps]
    assert seconds == sorted(seconds), "timestamps run backwards"
    assert seconds[0] == 0
    assert seconds[-1] < 300, f"the script starts its last beat at {seconds[-1]}s"


def test_roster_invents_no_names() -> None:
    """A template for the team to fill in, not a guess at who they are."""
    roster = (DOCS / "ROSTER.md").read_text()
    assert "your name here" in roster
    assert not re.search(r"@\w+\.(com|org|io)", roster)
