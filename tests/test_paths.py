"""Bundled data must be found however the package was installed.

Streamlit Community Cloud installs the repository non-editably from requirements.txt, so the
module lands in site-packages while the data stays next to the working directory. Before this
was handled, the deployed app rendered "No options to select" and then raised StopIteration.
"""

from pathlib import Path

from gridsignal import congestion, paths, prices
from gridsignal.jev import client
from gridsignal.mesh import scenarios


def test_root_holds_the_bundled_inputs():
    for marker in paths.MARKERS:
        assert (paths.ROOT / marker).exists()


def test_every_module_reads_from_that_one_root():
    assert prices.PROCESSED == paths.PROCESSED_DIR
    assert congestion.DATA_DIR == paths.DATA_DIR
    assert scenarios.SCENARIO_DIR == paths.SCENARIO_DIR
    assert client.FIXTURE_DIR == paths.FIXTURE_DIR


def test_falls_back_to_the_working_directory_when_the_module_is_installed_elsewhere(monkeypatch):
    """The wheel-install case: nothing useful above the module, everything under the cwd."""
    monkeypatch.setattr(paths, "__file__", "/nonexistent/site-packages/gridsignal/paths.py")
    monkeypatch.chdir(paths.ROOT)
    assert paths.resolve_root() == Path(paths.ROOT).resolve()


def test_price_scenarios_are_discoverable_from_the_resolved_root():
    assert [s.key for s in prices.available_scenarios()]
