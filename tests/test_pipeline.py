"""The ingest -> detect -> forecast -> signals -> backtest run, offline end to end."""

import subprocess
import sys

import pytest

from gridsignal import pipeline
from gridsignal.signals import Signal


@pytest.fixture(scope="module")
def scarcity() -> pipeline.PipelineResult:
    return pipeline.run("scarcity")


def test_every_stage_lines_up_on_the_same_intervals(scarcity):
    intervals = len(scarcity.trace.frame)

    assert len(scarcity.detections) == intervals
    assert len(scarcity.plan) == intervals
    assert len(scarcity.ledger) == intervals
    assert {"signal", "spike_prob", "reason"} <= set(scarcity.plan.columns)


def test_the_scarcity_day_is_worth_more_than_the_normal_day(scarcity):
    normal = pipeline.run("normal")

    assert scarcity.summary.signal_usd > 10 * normal.summary.signal_usd
    assert scarcity.trace.peak_mwh > 5_000


def test_the_run_exports_into_the_scarcity_window(scarcity):
    exports = scarcity.plan[scarcity.plan["signal"] == Signal.EXPORT.value]

    assert not exports.empty
    assert exports["spp"].max() > 3_000
    assert scarcity.summary.exported_kwh > 0


def test_unknown_scenarios_are_rejected():
    with pytest.raises(KeyError):
        pipeline.run("hurricane")


def test_the_cli_prints_the_uplift_without_touching_the_network():
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "gridsignal.pipeline",
            "--scenario",
            "scarcity",
            "--devices",
            "1000",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert "LZ_HOUSTON 2023-09-06" in out
    assert "uplift" in out
    assert "1,000 device(s)" in out
