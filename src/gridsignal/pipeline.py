"""End-to-end run: ingest -> detect -> forecast -> signals -> backtest."""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import pandas as pd

from gridsignal import backtest, dam, detect, forecast
from gridsignal.backtest import BacktestSummary
from gridsignal.prices import DEFAULT_SCENARIO, SCENARIOS, PriceTrace, load_scenario


@dataclass(frozen=True)
class PipelineResult:
    """Everything one run produced, ready for the dashboard or the CLI."""

    trace: PriceTrace
    detections: pd.DataFrame
    plan: pd.DataFrame
    ledger: pd.DataFrame
    summary: BacktestSummary
    windows: pd.DataFrame


def run(
    scenario: str = DEFAULT_SCENARIO,
    z: float = 3.0,
    kwh: float = backtest.DEFAULT_KWH,
    serve_home: bool = True,
) -> PipelineResult:
    """Replay a bundled ERCOT day end to end. No network, no credentials.

    The battery is home-first by default: the simulated house is served out of
    storage before anything is exported.
    """
    trace = load_scenario(scenario)
    detections = detect.detect_spikes(trace.frame, z=z)
    spike_prob = forecast.forecast_spike_probability(forecast.build_features(detections))
    plan = dam.signals_for(detections, spike_prob, trace.dam)
    ledger = backtest.value_captured(plan, trace.frame, kwh=kwh, serve_home=serve_home)
    return PipelineResult(
        trace=trace,
        detections=detections,
        plan=plan,
        ledger=ledger,
        summary=backtest.summarize(ledger),
        windows=detect.spike_windows(detections),
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Run the GridSignal pipeline")
    p.add_argument("--scenario", default=DEFAULT_SCENARIO, choices=sorted(SCENARIOS))
    p.add_argument("--z", type=float, default=3.0, help="spike detection threshold")
    p.add_argument("--kwh", type=float, default=backtest.DEFAULT_KWH, help="usable battery kWh")
    p.add_argument("--devices", type=int, default=1, help="scale the uplift to a fleet")
    args = p.parse_args()

    result = run(scenario=args.scenario, z=args.z, kwh=args.kwh)
    s = result.summary
    print(
        f"{result.trace.location} {result.trace.date} ({args.scenario}): "
        f"{len(result.trace.frame)} intervals, peak ${result.trace.peak_mwh:,.2f}/MWh"
    )
    print(
        f"detected {int(result.detections['is_spike'].sum())} spike intervals "
        f"in {len(result.windows)} window(s)"
    )
    print(f"signals ${s.signal_usd:,.2f} vs naive ${s.naive_usd:,.2f} per battery")
    print(
        f"uplift  ${s.uplift_usd:,.2f}/battery/day -> "
        f"${s.fleet_usd(args.devices):,.2f} across {args.devices:,} device(s)"
    )


if __name__ == "__main__":
    main()
