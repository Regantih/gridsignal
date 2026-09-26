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

## Agent mesh (`src/gridsignal/mesh/`)

The orchestration layer as a set of agents rather than one controller. Inspired by MIT Project
NANDA / NANDA Town (https://nandatown.projectnanda.org, https://github.com/projnanda); the code
here is an original implementation of those ideas and vendors nothing from them.

1. `build.py` turns every device into a battery agent and adds one gateway agent per firmware
   ring and one agent per load zone. A card advertises only *spare* capacity, so a bid can never
   resell kW already promised to the grid event.
2. `cards.py` defines the AgentFacts-style card (id, kind, zone, capabilities, health, last
   heartbeat) and signs a canonical JSON body with HMAC-SHA256. `registry.py` generates the key at
   startup, so a card edited after signing no longer verifies.
3. `registry.py` is the in-memory directory: register, publish updated capabilities, discover by
   capability / kind / zone, and expire agents that stop sending heartbeats. Rejected, stale and
   offline agents are visible but never discovered, so they cannot win work.
4. `negotiation.py` runs the contract net. `call_for_capacity()` announces the gap, `collect_bids()`
   gathers spare kW priced by wear plus a premium that grows as the homeowner's backup reserve
   thins, `propose()` picks the cheapest covering set (or the best partial cover plus an
   escalation), and `approve(call_id, approver)` is the only path that commits anything. The award
   ledger makes repeat triggers and double approvals no-ops.
5. `scenarios.py` + `simulate.py` replay YAML chaos scenarios deterministically and write a JSONL
   trace of every message, ending in a metrics record (time to cover, percent covered, messages,
   dollars at risk and recovered).
6. `llm.py` is an optional LLM bid ranker behind a flag; disabled by default, and it falls back to
   the deterministic ranking when no provider is configured.
