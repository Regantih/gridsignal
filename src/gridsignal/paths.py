"""Where the bundled ERCOT data, scenarios and traces live.

The package is run three ways: from a source checkout, from an editable install, and from a
plain (non-editable) install on a host that pip-installed the repository — Streamlit Community
Cloud does exactly that from ``requirements.txt``. Only the first two put the data two levels
above the module; in the third the module sits in ``site-packages`` and the data sits next to
the working directory. So the root is the first candidate that actually holds the data.
"""

from __future__ import annotations

from pathlib import Path

#: A directory is the project root only if the bundled inputs are really there.
MARKERS: tuple[str, ...] = ("data/processed", "scenarios")


def _has_markers(candidate: Path) -> bool:
    return all((candidate / marker).exists() for marker in MARKERS)


def resolve_root() -> Path:
    """The project root, searched from the module outwards and then from the cwd upwards."""
    module_root = Path(__file__).resolve().parents[2]
    cwd = Path.cwd().resolve()
    for candidate in (module_root, cwd, *cwd.parents):
        if _has_markers(candidate):
            return candidate
    return module_root


ROOT = resolve_root()
DATA_DIR = ROOT / "data"
SCENARIO_DIR = ROOT / "scenarios"
PROCESSED_DIR = DATA_DIR / "processed"
TRACE_DIR = DATA_DIR / "traces"
FIXTURE_DIR = DATA_DIR / "jev_fixtures"
