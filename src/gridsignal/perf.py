"""Speed and scale: the same fleet, timed stage by stage at 10,000 and 100,000 agents.

Everything here is in-process compute on a simulated fleet with real cached ERCOT
prices: no network, no device round trips, no messaging latency. The stages are the
ones an operator waits on::

    build fleet        construct the simulated devices and commit the dispatch plan
    detect incident    raise an incident from the current fleet snapshot
    recover            recompute the whole allocation after a human approval
    publish cards      sign one capability card per battery and verify it on register
    heartbeat sweep    every agent checks in and re-signs its card
    negotiate          one call for capacity: collect bids, rank, award, approve

Each stage is repeated, so the report carries p50 and p95 rather than one lucky run,
plus the throughput that matters for that stage and the peak heap the scale needs.

    python -m gridsignal.perf                      # 10,000 and 100,000 agents
    python -m gridsignal.perf --sizes 10000        # one scale
    python -m gridsignal.perf --repeats 3 --json perf.json
"""

from __future__ import annotations

import argparse
import json
import time
import tracemalloc
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from gridsignal.control_room import ControlRoomEngine
from gridsignal.fleet import FOCUS_DEVICE_ID
from gridsignal.mesh.build import heartbeat_all, register_fleet
from gridsignal.mesh.cards import derived_signing_key
from gridsignal.mesh.messages import MessageBus
from gridsignal.mesh.negotiation import Coordinator
from gridsignal.mesh.registry import AgentRegistry
from gridsignal.perf_before import slow_paths
from gridsignal.prices import load_scenario

DEFAULT_SIZES: tuple[int, ...] = (10_000, 100_000)
DEFAULT_REPEATS = 5
#: kW asked for in the benchmark call for capacity. Simulated.
CALL_KW = 500.0
MIB = 1024 * 1024


def percentile(samples: list[float], pct: float) -> float:
    """Linear-interpolated percentile, so five samples still give a usable p95."""
    if not samples:
        return 0.0
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * pct
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


@dataclass(frozen=True)
class Stage:
    """One timed step, repeated."""

    name: str
    unit: str
    samples_ms: tuple[float, ...]
    items: int

    @property
    def p50_ms(self) -> float:
        return round(percentile(list(self.samples_ms), 0.50), 2)

    @property
    def p95_ms(self) -> float:
        return round(percentile(list(self.samples_ms), 0.95), 2)

    @property
    def throughput_per_s(self) -> float:
        p50 = self.p50_ms
        return round(self.items / (p50 / 1000.0), 0) if p50 > 0 else 0.0


@dataclass
class ScaleResult:
    """Every stage at one fleet size, plus what the scale costs in memory."""

    agents: int
    devices: int
    repeats: int
    bids: int
    awards: int
    coverage_pct: float
    peak_mib: float
    stages: list[Stage] = field(default_factory=list)

    @property
    def total_p50_ms(self) -> float:
        return round(sum(stage.p50_ms for stage in self.stages), 2)


def _timed(fn: Callable[[], object]) -> float:
    started = time.perf_counter()
    fn()
    return (time.perf_counter() - started) * 1000.0


@dataclass(frozen=True)
class Pass:
    """One timed run through every stage, and what that run produced."""

    timings_ms: dict[str, float]
    agents: int
    devices: int
    bids: int
    awards: int
    coverage_pct: float


def one_pass(size: int) -> Pass:
    """Build, break, recover, publish, heartbeat and negotiate once, timing each step."""
    trace = load_scenario("scarcity")
    timings: dict[str, float] = {}

    started = time.perf_counter()
    engine = ControlRoomEngine(price_trace=trace, fleet_size=size)
    timings["build fleet"] = (time.perf_counter() - started) * 1000.0

    timings["detect incident"] = _timed(lambda: engine.trigger_device_failure(FOCUS_DEVICE_ID))
    timings["recover after approval"] = _timed(engine.approve_recovery)

    hours = engine.remaining_hours()
    registry = AgentRegistry(key=derived_signing_key(42))
    bus = MessageBus()
    timings["publish signed cards"] = _timed(
        lambda: register_fleet(registry, engine.devices, hours, bus)
    )
    timings["heartbeat sweep"] = _timed(lambda: heartbeat_all(registry))

    coordinator = Coordinator(registry, bus)
    started = time.perf_counter()
    call = coordinator.call_for_capacity(CALL_KW, hours)
    bids = coordinator.collect_bids(call, log_each=False)
    award_set = coordinator.propose(call, bids)
    coordinator.approve(call.call_id, "M. Alvarez (Fleet Operator)")
    timings["negotiate one call"] = (time.perf_counter() - started) * 1000.0

    return Pass(
        timings_ms=timings,
        agents=len(registry),
        devices=len(engine.devices),
        bids=len(bids),
        awards=len(award_set.awards),
        coverage_pct=award_set.coverage_pct,
    )


def measure(size: int, repeats: int = DEFAULT_REPEATS) -> ScaleResult:
    """Time every stage ``repeats`` times at ``size`` simulated batteries."""
    passes = [one_pass(size) for _ in range(repeats)]
    samples = {name: [run.timings_ms[name] for run in passes] for name in passes[0].timings_ms}
    last = passes[-1]
    agents, devices, bids, awards, coverage = (
        last.agents,
        last.devices,
        last.bids,
        last.awards,
        last.coverage_pct,
    )

    return ScaleResult(
        agents=agents,
        devices=devices,
        repeats=repeats,
        bids=bids,
        awards=awards,
        coverage_pct=coverage,
        peak_mib=peak_memory_mib(size),
        stages=[
            Stage("build fleet", "devices", tuple(samples["build fleet"]), devices),
            Stage("detect incident", "devices", tuple(samples["detect incident"]), devices),
            Stage(
                "recover after approval",
                "devices",
                tuple(samples["recover after approval"]),
                devices,
            ),
            Stage("publish signed cards", "cards", tuple(samples["publish signed cards"]), agents),
            Stage("heartbeat sweep", "cards", tuple(samples["heartbeat sweep"]), agents),
            Stage("negotiate one call", "bids", tuple(samples["negotiate one call"]), bids),
        ],
    )


def peak_memory_mib(size: int) -> float:
    """Peak Python heap for one full pass: fleet, signed registry and one call."""
    trace = load_scenario("scarcity")
    tracemalloc.start()
    engine = ControlRoomEngine(price_trace=trace, fleet_size=size)
    registry = AgentRegistry(key=derived_signing_key(42))
    bus = MessageBus()
    hours = engine.remaining_hours()
    register_fleet(registry, engine.devices, hours, bus)
    coordinator = Coordinator(registry, bus)
    call = coordinator.call_for_capacity(CALL_KW, hours)
    coordinator.propose(call, coordinator.collect_bids(call, log_each=False))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return round(peak / MIB, 1)


def measure_before(size: int, repeats: int = DEFAULT_REPEATS) -> ScaleResult:
    """The same stages against the pre-Q9 implementations, on this machine."""
    with slow_paths():
        return measure(size, repeats)


def lines(results: list[ScaleResult], before: list[ScaleResult] | None = None) -> list[str]:
    out = [
        "Speed and scale (simulated fleet, real cached ERCOT prices)",
        "",
        "In-process compute only: no network, no device round trips, no messaging latency.",
        "",
    ]
    baseline = {result.devices: result for result in before or []}
    for result in results:
        old = baseline.get(result.devices)
        out += [
            f"{result.devices:,} batteries -> {result.agents:,} agents "
            f"({result.repeats} repeats, peak heap {result.peak_mib:,.1f} MiB)",
        ]
        if old is None:
            out.append(f"  {'stage':<24}{'p50 ms':>9}{'p95 ms':>9}{'throughput':>18}")
        else:
            out.append(
                f"  {'stage':<24}{'before p50':>11}{'p50 ms':>9}{'p95 ms':>9}"
                f"{'speedup':>9}{'throughput':>18}"
            )
        for index, stage in enumerate(result.stages):
            head = f"  {stage.name:<24}"
            if old is not None:
                was = old.stages[index].p50_ms
                speedup = was / stage.p50_ms if stage.p50_ms > 0 else 0.0
                head += f"{was:>11,.1f}"
            out.append(
                f"{head}{stage.p50_ms:>9,.1f}{stage.p95_ms:>9,.1f}"
                + (f"{speedup:>8,.2f}x" if old is not None else "")
                + f"{stage.throughput_per_s:>13,.0f} {stage.unit}/s"
            )
        tail = f"  {'end to end (p50 sum)':<24}"
        if old is not None:
            tail += f"{old.total_p50_ms:>11,.1f}"
        out += [
            f"{tail}{result.total_p50_ms:>9,.1f}",
            f"  {result.bids:,} bids, {result.awards:,} awards, "
            f"{result.coverage_pct:.0f}% of the call covered",
            "",
        ]
    if before:
        out.append(
            "Before = the implementations Q9 replaced (gridsignal.perf_before), "
            "run on this machine in the same process."
        )
    out.append("Reproduce with python -m gridsignal.perf --before.")
    return out


def report(sizes: tuple[int, ...] = DEFAULT_SIZES, repeats: int = DEFAULT_REPEATS) -> str:
    return "\n".join(lines([measure(size, repeats) for size in sizes]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes",
        type=int,
        nargs="+",
        default=list(DEFAULT_SIZES),
        help="fleet sizes to profile (default: 10000 100000)",
    )
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS, help="samples per stage")
    parser.add_argument("--json", type=str, default=None, help="also write the raw numbers here")
    parser.add_argument(
        "--before",
        action="store_true",
        help="also time the pre-Q9 implementations for the before column",
    )
    args = parser.parse_args(argv)

    before = [measure_before(size, args.repeats) for size in args.sizes] if args.before else None
    results = [measure(size, args.repeats) for size in args.sizes]
    print("\n".join(lines(results, before)))
    if args.json:
        payload = [
            {
                **asdict(result),
                "stages": [
                    {
                        "name": stage.name,
                        "p50_ms": stage.p50_ms,
                        "p95_ms": stage.p95_ms,
                        "throughput_per_s": stage.throughput_per_s,
                        "unit": stage.unit,
                    }
                    for stage in result.stages
                ],
            }
            for result in results
        ]
        with open(args.json, "w") as handle:
            json.dump(payload, handle, indent=2)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
