# Architecture

1. Ingest: ERCOT prices, load and fuel mix into `data/raw/*.parquet`.
2. Detect: rolling-baseline spike detection, scarcity windows, inter-zone spreads.
3. Forecast: spike probability for the next few hours by zone.
4. Signals: charge / hold / export per interval.
5. Backtest: dollars captured vs. a naive schedule.
6. Dashboard: member view plus an operator view for Base.
