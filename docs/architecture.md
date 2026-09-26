# Architecture

## Control Room (simulated fleet, real prices)

`fleet.py` builds a seeded 48-device fleet. `prices.py` loads the cached ERCOT settlement-price
trace from `data/processed/`, picks the most expensive two-hour window as the grid event, and
converts kW at stake into dollars (`kW x hours x $/MWh / 1000`).
`control_room/engine.py` holds the state machine —
baseline dispatch allocation, telemetry-failure detection, incident + task creation, the human
approval gate, quarantine and reassignment — and appends every transition to an audit log.
`app/dashboard.py` renders a single snapshot of that state. See `DEMO.md`.

## ERCOT pipeline

1. Ingest: real-time settlement point prices via `gridstatus` (public ERCOT MIS, no credentials),
   cached to `data/processed/<zone>_rtm_spp_sample.parquet` with a provenance sidecar JSON:
   `python -m gridsignal.ingest --date <YYYY-MM-DD>`. Load and fuel mix are not implemented yet.
2. Detect: rolling-baseline spike detection, scarcity windows, inter-zone spreads.
3. Forecast: spike probability for the next few hours by zone.
4. Plan: `dam.py` picks the charge and export hours from the day-ahead curve cached alongside
   each real-time trace (`*_dam.parquet`, fetched by `scripts/fetch_dam.py`). DAM clears the
   afternoon before the trade day, so the plan uses only information the operator has in advance.
5. Signals: charge / hold / export per interval — the day-ahead plan, overridden only where real
   time diverges from it. `signals.py` is the real-time-only fallback for a day with no curve.
6. Backtest: dollars captured vs. a naive schedule, scored once on `data/holdout/` with the
   parameters fitted on `data/tuning/` plus the two scenario days.
7. Dashboard: member view plus an operator view for Base.
