# Judging map

Every sub-criterion, and the file, test or screen that proves it. 100 points total, judged from
the demo video and the codebase.

Run everything below from a fresh clone after `pip install -e ".[dev]"`.

## Technical Execution — 30

### Completeness: the core workflow runs without crashing — 15

| Evidence | Where |
|---|---|
| Failure → incident → human approval → quarantine + reassignment → recovery, end to end | `src/gridsignal/control_room/engine.py`, screen: **Control Room** |
| Every state transition asserted, including approving out of order and replaying after reset | `tests/test_control_room.py` (`pytest -q tests/test_control_room.py`) |
| All four views render, and the demo buttons can be pressed in any order | `tests/test_dashboard.py` — drives the real app through Streamlit's `AppTest` |
| Runs offline with no key, no network, no account | bundled `data/processed/*.parquet`, `data/jev_fixtures/*.json`; whole suite passes air-gapped |
| One documented start command | `streamlit run app/dashboard.py` (README Quick Start) |

### Technical depth: a real pipeline, not a wrapper — 15

| Evidence | Where |
|---|---|
| ERCOT ingest → rolling median/MAD spike detection → causal spike probability → day-ahead-anchored dispatch → settlement backtest | `ingest.py`, `detect.py`, `forecast.py`, `dam.py`, `signals.py`, `backtest.py`, `pipeline.py` |
| Out-of-sample discipline: separate tuning split, frozen parameters, held-out days scored once, losing days published | `holdout.py`, `scripts/tune_policy.py`, `tests/test_holdout.py`, screen: **Grid Signals → Held-out days** |
| No lookahead anywhere in the policy | `tests/test_detect.py`, `tests/test_forecast.py`, `tests/test_dam.py` |
| Agent mesh: HMAC-signed capability cards, registry with verified/stale/rejected, contract-net bidding, idempotent awards, partial cover plus escalation | `src/gridsignal/mesh/`, `tests/test_mesh.py`, screen: **Agent Mesh** |
| Jev is one input, not the system: signature → rules → Jev → human | `src/gridsignal/jev/policy.py`, `tests/test_jev.py` |
| Staged firmware rollout as an orchestrated job: lab → 1% → 10% → 50% → 100% rings, three health gates per ring, automatic halt and rollback, no advance during a grid event or an islanded home, human approval past 10% | `src/gridsignal/rollout.py`, `scenarios/rollout_*.yaml`, `tests/test_rollout.py`, screen: **Agent Mesh → Rollout** |
| Install wave: signed installer commissioning, probation state, zero awards to unverified or probationary units, existing commitments untouched | `src/gridsignal/install.py`, `scenarios/install_wave.yaml`, `tests/test_install.py`, screen: **Agent Mesh → Install wave** |
| Held-out chaos drills written after the rules and prompts were frozen: baseline rules 0/4, Jev 1/4; after tuning on held-out rules 4/4, Jev 1/4; zero backup-reserve violations throughout | `scenarios/holdout/*.yaml`, `src/gridsignal/drills.py`, `python -m gridsignal.drills`, `tests/test_drills.py`, screen: **Agent Mesh → Held-out drills** |

## Fit to Track — 30

### The problem — 15

| Evidence | Where |
|---|---|
| Orchestration: the fleet stays coordinated when pieces fail — five chaos scenarios (single device, zone gateway, forged card, silent bidder, fleet-wide scarcity) replay deterministically | `scenarios/*.yaml`, `python -m gridsignal.simulate --all`, `tests/test_simulate.py` |
| It holds up on failures nobody designed for: a simulated cascade in waves, an under-frequency event where batteries self-deploy from their own cards in 12 simulated cycles and reconcile without double-counting, a neighbourhood islanding that resyncs on restore, a large-load squeeze with conflicting bids | `scenarios/holdout/*.yaml`, `tests/test_drills.py`, `docs/DEMO.md` 2:45 |
| The failure mode a heartbeat cannot see: a build that keeps sending telemetry while silently refusing charge/discharge on hot devices is caught by the canary's response gate — 100 homes touched, 3 affected, 100 rolled back out of 10,000, 420 simulated seconds to detect | `tests/test_rollout.py::test_a_silent_bad_build_is_caught_by_the_canary_response_gate`, `python -m gridsignal.rollout scenarios/rollout_bad_build.yaml`, `docs/DEMO.md` 3:05 |
| Growth does not destabilise the mesh: several hundred units join during a live event with zero kW awarded to an unverified or probationary unit and zero kW lost from an existing commitment | `tests/test_install.py`, screen: **Agent Mesh → Install wave** |
| Failures are priced, not just logged: dollars at risk before approval, dollars recovered after | `control_room/engine.py`, `prices.energy_value_usd`, screen: **Control Room → incident panel** |
| Open Grid Data: a specific, checkable claim about what the public data hides | `src/gridsignal/insight.py`, `python -m gridsignal.insight`, screen: **Grid Signals → headline card** |
| Congestion measured, not asserted: all eight load zones plus the hub average for 15 bundled days, zone-to-hub and West-to-load-center basis per 15-minute interval | `src/gridsignal/congestion.py`, `data/zones/*.parquet` with provenance sidecars, `tests/test_congestion.py`, screen: **Grid Signals → congestion panel** |

### The "why" — 15

| Evidence | Where |
|---|---|
| Why this problem is the one that matters to Base | README → *Why this matters to Base* |
| Why the two tracks are one product: the value that only real time reveals lands in the same minute a device failure costs the most | README → *Open Grid Data*, `docs/DEMO.md` 0:00 and 3:15 |
| Why the congestion read changes an operator decision: the zone order it produces is what the Control Room dispatches first, without moving the target, the homeowner reserve or the approval gate | `control_room/engine.py::set_priority_zone`, `congestion.dispatch_order`, `tests/test_control_room.py::test_priority_zone_discharges_that_zone_first_without_changing_the_target`, screen: **Control Room → congestion dispatch preference** |
| Why the held-out result is published as measured, losses and all, before any tuning | README → *Held-out chaos drills*, `docs/DEMO.md` 2:45 |
| Why a human gate rather than autonomy, and where Jev stops | README → *Jev, the decision layer*; `jev/policy.py` thresholds; `docs/DEMO.md` 1:30 |
| Safety boundary stated in the product, not only the README | simulation banner in `app/dashboard.py`, `docs/DEMO.md` |

## Value and Impact — 20

### Insight quality: non-obvious and genuinely useful — 10

| Evidence | Where |
|---|---|
| Only 47% of a scarcity day's capturable battery value was visible in the day-ahead curve; $18.83/battery/day exists only in real time, worth ~28 ordinary days | `insight.py`, screen: **Grid Signals → headline card**, README |
| All 19 intervals that printed ≥5x their day-ahead hour fell on scarcity days; the 12 ordinary days never diverged | `insight.summarize`, `tests/test_insight.py::test_divergent_intervals_are_a_scarcity_phenomenon` |
| Method is stated and reproducible, and the ceiling is labelled as a ceiling | README → *Open Grid Data*, expander under the insight card |
| Congestion is large but re-timing collects little: LZ_LCRA hour 18 prices $39.82/MWh over the hub and 29.8% of zone-intervals sit >$5 from it, yet zone-timed discharge is worth +$0.13/battery/day (best +$0.57, worst −$0.01) | `congestion.summarize`, `congestion.zone_uplift`, `tests/test_congestion.py::test_zone_timed_beats_zone_blind_when_the_hub_misranks_the_peak`, screen: **Grid Signals → congestion panel** |
| "Where to install next" ranks zones by value per battery and shows marginal value falling as a zone saturates, labelled a sketch rather than a forecast or siting study | `congestion.placement_ranks`, `congestion.placement_sketch`, `tests/test_congestion.py::test_marginal_value_falls_as_a_zone_saturates`, README → *Congestion* |
| The honest counterweight: day-ahead anchoring turned 2/7 held-out days into 6/7, but the median day is +$0.10 | README → *Held-out results*, screen: **Grid Signals** |

### Usability: Base could use it tomorrow — 10

| Evidence | Where |
|---|---|
| One command to run; no keys, no accounts, no data setup | README Quick Start, `.env.example` (everything optional) |
| Operator view is single-screen and role-aware: overview, fleet map, incident, tasks for Fleet Operator / Reliability Engineer / Field Support, audit timeline | `app/dashboard.py`, screen: **Control Room** |
| Member-facing view of the same event, in plain English, with no operator concepts leaked | `src/gridsignal/member.py`, `tests/test_member.py`, screen: **Member App** |
| Append-only audit timeline of detection, recommendation, approval, reassignment, recovery | `control_room/models.py`, `tests/test_control_room.py` |
| Assumptions are labelled as assumptions wherever a dollar appears | README → *Assumptions*, captions in every view |

## Innovation and Execution — 20

### Creativity: novel combination, looks and feels good — 10

| Evidence | Where |
|---|---|
| NANDA-inspired agent mesh (signed AgentFacts-style cards, contract-net, agent-town chaos scenarios) implemented from scratch over a battery fleet | `src/gridsignal/mesh/`, README attribution |
| Jev as a fast decision layer gated by confidence, alongside cryptographic signatures and deterministic rules | `src/gridsignal/jev/`, `tests/test_jev.py` |
| Real ERCOT day-ahead + real-time data driving a simulated orchestration story, so the failure has a price | `prices.py`, `dam.py`, `control_room/engine.py` |
| Consistent dark operator UI: shared card/kicker/state-colour system, one colour language for device, card and signal states | `CSS`, `STATUS_COLOR`, `CARD_COLOR`, `SIGNAL_COLOR`, `JEV_COLOR` in `app/dashboard.py` |

### Performance: optimised for speed or scale — 10

| Evidence | Where |
|---|---|
| 10,000 devices: build ~104 ms, detect ~1 ms, approve + reallocate ~33 ms | `tests/test_scale.py` (`pytest -q -s tests/test_scale.py`) |
| 10,000 agents: register ~242 ms, heartbeat sweep ~106 ms, negotiation over 5,913 bids ~22 ms | `tests/test_simulate.py` (`pytest -q -s tests/test_simulate.py`) |
| 10,000-device staged rollout with per-ring gates in ~2 ms; 400 units commissioned into a 10,000-device mesh and re-auctioned in ~600 ms | `tests/test_rollout.py::test_ten_thousand_device_rollout_detects_the_bad_build_fast`, `tests/test_install.py::test_ten_thousand_device_install_wave_benchmark` |
| Fleet map thins healthy markers above 400 devices so a 10,000-device view stays interactive | `MAP_MARKERS` in `app/dashboard.py` |
| Analytics, held-out scoring, insight and chaos replays are cached per input in the dashboard | `@st.cache_data` on `signals_run`, `holdout_run`, `insight_run`, `chaos_run`, `jev_eval` |
| Jev never blocks the demo: recorded answers replay in microseconds, live median 326 ms | `jev/client.py`, `python -m gridsignal.jev.evaluate` |
