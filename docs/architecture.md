# Architecture

## One fleet state

There is one source of truth for fleet state: `ControlRoomEngine` in
`src/gridsignal/control_room/engine.py`. It owns every `Device` — state of charge, status,
controller, home load and committed kW — and it is the only thing that changes them.

The agent mesh is **derived** from that state and never writes back to it:

- `mesh/build.py::card_for()` projects a `Device` into a signed AgentFacts card at one instant.
  A card advertises `kw_available` / `kwh_available` computed from the same numbers the Control
  Room dispatches on, **net of the member's backup reserve** — `home.reserve_kwh()`, the same
  function the Control Room holds back in `discharge_headroom_kw()` — and net of the kW already
  committed to the event. It also declares `reserve_kwh`, so the coordinator can see the reserve
  was already held and does not hold a second, different floor of its own
  (`negotiation.reserve_floor_kwh()`).
- The coordinator bids, awards and approves against cards only. An award becomes a fleet change
  only when it is applied through the Control Room's dispatch path, so kW cannot be committed
  twice or spent out of the reserve by one engine without the other seeing it.

The consequence to remember when reading the numbers: raising the reserve floor in the Control
Room immediately shrinks what the mesh can bid, and both views agree on the same kW.

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

## Jev decision layer (`src/gridsignal/jev/`)

Code acts, Jev decides, humans approve when Jev is unsure. The layer is optional: with no API key
it replays recorded answers, and with neither key nor fixture it answers with deterministic rules
labelled "Jev offline, rules fallback".

1. `questions.py` builds the `IncidentSnapshot` — simulated fleet scale, offline agents, zones and
   gateway rings, card statuses and HMAC validity, telemetry ages, prices, lost kW, dollars at
   risk, bids, plan coverage, state of charge and backup reserve — and the four questions asked
   over it (root cause as a choice, one trust question per suspect agent, backup risk as a score).
2. `client.py` is the one client behind two transports: Vercel AI Gateway (`AI_GATEWAY_API_KEY`,
   yes/no typed `boolean`) and TypeSafe direct (`TYPESAFE_API_KEY`, typed `noul`), preferring
   whichever key is set. Both responses normalise to `JevAnswer` (choice / yes / score plus
   confidence and probabilities). Resolution order is fixture, then live, then fallback; a live
   answer is recorded into `data/jev_fixtures/<scenario>.json` keyed on a hash of the state and
   questions, so replays are deterministic and a schema change invalidates them loudly.
3. `rules.py` is the deterministic fallback. It reads the same state and always reports confidence
   0.0, which is what makes it structurally incapable of auto-approving.
4. `policy.py` is the confidence gate: auto-approve only when every answer clears the threshold
   (default 0.9), backup risk is at most 0.35, dollars are under the cap (default $500), the plan
   covers the whole gap and no agent is called untrustworthy. Everything else returns the human
   approver. Either way the commit still happens in `Coordinator.approve()` /
   `ControlRoomEngine.approve_recovery()`.
5. `incident.py` wires the same questions to the Control Room incident; `evaluate.py` scores
   rules-only against Jev on every chaos scenario (root-cause accuracy against the injected
   ground truth, approval counts, median latency); `record.py` re-records fixtures when a key is
   present.
