"""The negotiation over a real socket between real processes, including a bad network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gridsignal import transport

AGENTS = 60


@pytest.fixture(scope="module")
def clean() -> transport.TransportResult:
    return transport.measure(AGENTS, workers=2)


def test_every_agent_connects_from_another_process_and_is_timed(
    clean: transport.TransportResult,
) -> None:
    """One socket per agent, opened by a child process, and one latency sample each."""
    assert clean.worker_processes == 2
    assert clean.connections == AGENTS
    assert clean.completed == AGENTS
    assert len(clean.latencies_ms) == AGENTS
    assert clean.frames_sent > 0 and clean.frames_received > 0
    assert clean.frames_dropped == 0
    assert clean.resends == 0


def test_the_call_clears_and_the_latency_summary_is_ordered(
    clean: transport.TransportResult,
) -> None:
    assert clean.bids > 0
    assert 0 < clean.awards <= clean.bids
    assert clean.coverage_pct == pytest.approx(100.0)
    assert clean.covered_kw == pytest.approx(clean.gap_kw)
    assert 0 < clean.p50_ms <= clean.p95_ms <= clean.max_ms
    assert clean.frames_per_s > 0


def test_the_same_seed_over_the_wire_awards_the_same_kw(
    clean: transport.TransportResult,
) -> None:
    """Ranking is by spare kW then id, so arrival order cannot change who wins."""
    again = transport.measure(AGENTS, workers=2)

    assert (again.bids, again.awards, again.covered_kw) == (
        clean.bids,
        clean.awards,
        clean.covered_kw,
    )


def test_a_card_signed_with_the_wrong_key_is_refused_across_the_process_boundary() -> None:
    """The signature is checked in the coordinator, not assumed inside one interpreter."""
    forged = 5
    result = transport.measure(AGENTS, workers=2, forged=forged)
    clean_bids = transport.measure(AGENTS, workers=2).bids

    assert result.forged_refused == forged
    assert result.bids < clean_bids
    assert result.completed == AGENTS


def test_five_percent_packet_loss_costs_the_tail_but_still_clears(
    clean: transport.TransportResult,
) -> None:
    """Frames thrown away on arrival are re-sent, so the call is covered late, not lost."""
    lossy = transport.measure(AGENTS, workers=2, drop=0.05)

    assert lossy.frames_dropped > 0
    assert lossy.resends > 0
    assert lossy.p95_ms > clean.p95_ms
    assert lossy.coverage_pct == pytest.approx(100.0)
    assert lossy.completed == AGENTS


def test_the_report_labels_the_benchmark_loopback_and_names_the_command() -> None:
    text = transport.report(sizes=(AGENTS,), workers=2)

    assert "Local loopback (127.0.0.1)" in text
    assert "Not a WAN" in text
    assert "p50 ms" in text and "p95 ms" in text
    assert "python -m gridsignal.transport" in text


def test_the_cli_writes_the_numbers_it_prints(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    out_file = tmp_path / "transport.json"
    assert transport.main(["--agents", str(AGENTS), "--workers", "2", "--json", str(out_file)]) == 0

    printed = capsys.readouterr().out
    assert "Local loopback" in printed
    payload = json.loads(out_file.read_text())
    assert len(payload) == 1
    assert payload[0]["agents"] == AGENTS
    assert payload[0]["p50_ms"] <= payload[0]["p95_ms"]
    assert "latencies_ms" not in payload[0]
