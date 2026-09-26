# Architecture

## Control Room (simulation only)

`fleet.py` builds a seeded 48-device fleet. `control_room/engine.py` holds the state machine —
baseline dispatch allocation, telemetry-failure detection, incident + task creation, the human
approval gate, quarantine and reassignment — and appends every transition to an audit log.
`app/dashboard.py` renders a single snapshot of that state. See `DEMO.md`.

## ERCOT pipeline (optional)

1. Ingest: ERCOT prices, load and fuel mix into `data/raw/*.parquet`.
2. Detect: rolling-baseline spike detection, scarcity windows, inter-zone spreads.
3. Forecast: spike probability for the next few hours by zone.
4. Signals: charge / hold / export per interval.
5. Backtest: dollars captured vs. a naive schedule.
6. Dashboard: member view plus an operator view for Base.
