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
| Operator judgment model: six principles committed in priority order before scoring (three hard vetoes, three weighted soft), Jev answers each with a probability and the rules layer (Jev offline) with confidence 0.0, a transparent policy returns act / act-and-notify / ask-a-human with a plain-language reason, and the certainty bar rises with the dollars at stake | `src/gridsignal/jev/principles.yaml`, `src/gridsignal/jev/judgment.py`, `tests/test_judgment.py`, screen: **Control Room → Operator principles** |
| Model on/off ablation: every bundled scenario replayed twice on the same seeds, with the recorded Jev answers and with them withheld — identical covered kW in 5 of 5, a different reported root cause in 2, so the model moves the explanation and never the dispatch | `python -m gridsignal.jev.evaluate`, `src/gridsignal/jev/evaluate.py`, screen: **Why** |
| Blind-first safety scoring: pack and answer key committed before the first run, scored once on the four held-out drills (rules with Jev offline 21/24, Jev 17/24), every rules-vs-Jev disagreement settled by the key with the winner named | `data/holdout_safety_labels.yaml`, `python -m gridsignal.judgment_report`, `tests/test_judgment.py` |
| Calibration on a **simulated** override log (96 episodes, 80 overridden), moving only soft weights and bars, split by whole operating condition: the fitted half moves 90% to 94% and held-out agreement moves 71% to 83% (+6 of 48 episodes) — one simulated operator, so weak evidence that the weights transfer and no claim about real Base operators | `src/gridsignal/jev/overrides.py`, `data/judgment_calibration.json`, `python -m gridsignal.judgment_report` |
| Staged firmware rollout as an orchestrated job: lab → 1% → 10% → 50% → 100% rings, three health gates per ring, automatic halt and rollback, no advance during a grid event or an islanded home, human approval past 10% | `src/gridsignal/rollout.py`, `scenarios/rollout_*.yaml`, `tests/test_rollout.py`, screen: **Agent Mesh → Rollout** |
| Install wave: signed installer commissioning, probation state, zero awards to unverified or probationary units, existing commitments untouched | `src/gridsignal/install.py`, `scenarios/install_wave.yaml`, `tests/test_install.py`, screen: **Agent Mesh → Install wave** |
| Awards are proved, not trusted: before it is committed, every award is checked against the whole delivery window (spare energy above the member reserve, free inverter power, a 50% derate on degraded units, minus kW already owed elsewhere), trimmed or refused with a logged reason, and re-checked at the approval gate; the counterfactual run reports 3.21 kW across 2 of 3,343 awards that would have been overcommitted | `src/gridsignal/mesh/deliverability.py`, `python -m gridsignal.deliverability_report`, `tests/test_deliverability.py`, screen: **Agent Mesh → Deliverability check** |
| Held-out chaos drills written after the rules and prompts were frozen: baseline rules 0/4, Jev 1/4; after tuning on held-out and re-scoring on the unified reserve, rules 4/4, Jev 2/4; zero backup-reserve violations throughout | `scenarios/holdout/*.yaml`, `src/gridsignal/drills.py`, `python -m gridsignal.drills`, `tests/test_drills.py`, screen: **Agent Mesh → Held-out drills** |

## Fit to Track — 30

### The problem — 15

| Evidence | Where |
|---|---|
| Orchestration: the fleet stays coordinated when pieces fail — five chaos scenarios (single device, zone gateway, forged card, silent bidder, fleet-wide scarcity) replay deterministically | `scenarios/*.yaml`, `python -m gridsignal.simulate --all`, `tests/test_simulate.py` |
| It holds up on failures nobody designed for: a simulated cascade in waves, an under-frequency event where batteries self-deploy from their own cards in 12 simulated cycles and reconcile without double-counting, a neighbourhood islanding that resyncs on restore, a large-load squeeze with conflicting bids | `scenarios/holdout/*.yaml`, `tests/test_drills.py`, `python -m gridsignal.drills` |
| The failure mode a heartbeat cannot see: a build that keeps sending telemetry while silently refusing charge/discharge on hot devices is caught by the canary's response gate — 100 homes touched, 3 affected, 100 rolled back out of 10,000, 420 simulated seconds to detect | `tests/test_rollout.py::test_a_silent_bad_build_is_caught_by_the_canary_response_gate`, `python -m gridsignal.rollout scenarios/rollout_bad_build.yaml`, `docs/DEMO.md` 2:10 |
| Home-first dispatch: the house is served from storage before anything is exported, so `export kW = discharge kW − home load kW`, and a mixed fleet of legacy and simulated Base Core-style (40 kWh / 20 kW) units carries capacity and power on its cards | `src/gridsignal/load.py`, `src/gridsignal/home.py`, `fleet.py`, `tests/test_home.py`, screen: **Control Room → Home-first dispatch** |
| Multi-tenant control authority: the mesh never bids, awards or reassigns a battery a partner utility controls, including during chaos drills | `control_room/models.py::Controller`, `control_room/engine.py`, `tests/test_home.py::test_the_operator_never_dispatches_another_tenants_battery` |
| Growth does not destabilise the mesh: several hundred units join during a live event with zero kW awarded to an unverified or probationary unit and zero kW lost from an existing commitment | `tests/test_install.py`, screen: **Agent Mesh → Install wave** |
| Failures are priced, not just logged: dollars at risk before approval, dollars recovered after | `control_room/engine.py`, `prices.energy_value_usd`, screen: **Control Room → incident panel** |
| Open Grid Data: a specific, checkable claim about what the public data hides | `src/gridsignal/insight.py`, `python -m gridsignal.insight`, screen: **Grid Signals → headline card** |
| Congestion measured, not asserted: all eight load zones plus the hub average for 15 bundled days, zone-to-hub and West-to-load-center basis per 15-minute interval | `src/gridsignal/congestion.py`, `data/zones/*.parquet` with provenance sidecars, `tests/test_congestion.py`, screen: **Grid Signals → congestion panel** |

### The "why" — 15

| Evidence | Where |
|---|---|
| Why this problem is the one that matters to Base | README → *Why this matters to Base* |
| Why the two tracks are one product: the value that only real time reveals lands in the same minute a device failure costs the most | README → *Open Grid Data*, `docs/DEMO.md` 0:00 and 2:55 |
| Why the congestion read changes an operator decision: the zone order it produces is what the Control Room dispatches first, without moving the target, the homeowner reserve or the approval gate | `control_room/engine.py::set_priority_zone`, `congestion.dispatch_order`, `tests/test_control_room.py::test_priority_zone_discharges_that_zone_first_without_changing_the_target`, screen: **Control Room → congestion dispatch preference** |
| Why serving the home first is the right trade even though it costs export revenue, with the before/after held-out and scarcity numbers printed rather than smoothed | README → *Held-out results*, screen: **Grid Signals → held-out days** |
| Why the reserve floor is an operator decision and not a default: the storm policy prices revenue given up against backup hours protected, and writes the change to the audit log | `control_room/engine.py::set_reserve_floor`, `tests/test_home.py::test_raising_the_reserve_trades_revenue_for_backup_hours`, screen: **Control Room → Storm reserve policy** |
| Why the held-out result is published as measured, losses and all, before any tuning | README → *Held-out chaos drills*, `python -m gridsignal.drills` |
| Why a human gate rather than autonomy, and where Jev stops | README → *Jev, the decision layer*; `jev/policy.py` thresholds; `docs/DEMO.md` 1:30 |
| Safety boundary stated in the product, not only the README | simulation banner in `app/dashboard.py`, `docs/DEMO.md` |

## Value and Impact — 20

### Insight quality: non-obvious and genuinely useful — 10

| Evidence | Where |
|---|---|
| Only 47% of a scarcity day's capturable battery value was visible in the day-ahead curve; $18.83/battery/day exists only in real time, worth ~28 ordinary days | `insight.py`, screen: **Grid Signals → headline card**, README |
| All 19 intervals that printed ≥5x their day-ahead hour fell on scarcity days; the 12 ordinary days never diverged | `insight.summarize`, `tests/test_insight.py::test_divergent_intervals_are_a_scarcity_phenomenon` |
| The market rules, not the battery, are what stand between a home fleet and the ancillary money: inside ERCOT's ADER pilot (Phase 3.3, 3 Jun 2026 — ECRS and Non-Spin only, 100 MW each system-wide, no QSE above 90%, so 9.0 kW per battery across 10,000, https://www.ercot.com/mktrules/pilots/ader) the median held-out day for a Base Core-style unit is **+$0.14**/battery/day (mean +$0.29), with 2024-05-08 alone carrying 69%. Scored against all five products — a labelled comparison, not an offer — the same days pay a median of $0.74 and a mean of $3.59, 88% of it Reg Down, the one product an ADER may not sell; that comparison also fails its own price-taker check (200 MW against 392 MW procured, 51%) | `src/gridsignal/ancillary.py` (`python -m gridsignal.ancillary`), `tests/test_ancillary.py`, screen: **Grid Signals → ancillary co-optimization** |
| Wear is worth pricing on the expensive pack and nowhere else: gating dispatch on a modelled cycle cost earns +$0.72/battery/day on held-out days for a legacy unit while skipping 5.00 of 7.00 equivalent full cycles, but the same floor never binds on a Base Core-style unit (0 cycles saved, −$0.11/day), so wear-gating belongs per unit type, not fleet-wide. The cost itself is a labelled assumption ($400/kWh over 4,000 cycles = $100/MWh; $300/kWh over 6,000 = $50/MWh) | `src/gridsignal/degradation.py` (`python -m gridsignal.degradation`), `tests/test_degradation.py`, screen: **Grid Signals → held-out days → degradation-aware dispatch** |
| Method is stated and reproducible, and the ceiling is labelled as a ceiling | README → *Open Grid Data*, expander under the insight card |
| Congestion is large but re-timing collects little: LZ_LCRA hour 18 prices $39.82/MWh over the hub and 29.8% of zone-intervals sit >$5 from it, yet a battery *in LZ_LCRA* collects +$0.15/battery/day (hindsight-timed; +$0.18 ordinary days, +$0.03 scarcity days). Every congestion and placement dollar is labelled hindsight-timed, and the gap is paired with its own zone's uplift | `congestion.summarize`, `congestion.zone_uplift`, `tests/test_congestion.py::test_zone_timed_beats_zone_blind_when_the_hub_misranks_the_peak`, screen: **Grid Signals → congestion panel** |
| "Where to install next" ranks zones by value per battery and shows marginal value falling as a zone saturates, labelled a sketch rather than a forecast or siting study | `congestion.placement_ranks`, `congestion.placement_sketch`, `tests/test_congestion.py::test_marginal_value_falls_as_a_zone_saturates`, README → *Congestion* |
| Home-first dispatch priced honestly: the canonical held-out claim is the product's own dispatch on the default Base Core-style 40 kWh / 20 kW unit, home-first on 7 of 7 days at mean +$2.96 per battery per day (median +$1.94; against a do-nothing battery, median $1.64); the legacy 13.5 kWh / 5 kW unit wins 7 of 7 at +$2.14 a day and the same frozen policy run grid-only wins 5 of 7 at mean −$0.76, both kept only as comparisons. The house is carried by the grid while storage is held for the day-ahead peak, so member savings are only $0.08/day and the member's protection is the reserve, not self-supply | `holdout.score_day(serve_home=...)`, `tests/test_holdout.py`, README → *Held-out results*, screen: **Grid Signals → held-out days** |
| The honest counterweight: the peak hold is what turns the scarcity day around — without it the same policy wins 6 of 7 at mean +$0.70 and 2024-05-08 flips from +$6.78 to −$5.37 — and two scarcity days still carry most of the mean | README → *Held-out results*, screen: **Grid Signals** |
| Same-interval lookahead removed: each interval is decided from the day-ahead curve and the last settled print, and the old grid-only comparison is still shown as first scored (+$0.09 → −$0.76 mean, 4/7 → 5/7; the scarcity day settles at −$14.98). Every headline figure in this file comes from `python -m gridsignal.demo_numbers` | `dam.deviate_from_plan(same_interval_price=...)`, `tests/test_dam.py::test_an_intervals_own_print_cannot_change_its_own_decision`, `python -m gridsignal.holdout`, screen: **Grid Signals → held-out days** |

### Usability: Base could use it tomorrow — 10

| Evidence | Where |
|---|---|
| One command to run; no keys, no accounts, no data setup | README Quick Start, `.env.example` (everything optional) |
| Operator view is single-screen and role-aware: overview, fleet map, incident, tasks for Fleet Operator / Reliability Engineer / Field Support, audit timeline | `app/dashboard.py`, screen: **Control Room** |
| Members, not customers: the member sees the home/export split, backup hours including an optional generator top-off, and a neighbour mutual-aid card that can never spend a giver's reserve | `src/gridsignal/home.py`, `src/gridsignal/member.py`, `tests/test_home.py::test_mutual_aid_helps_a_medical_member_without_spending_anyone_else_reserve`, screen: **Member App** |
| Member-facing view of the same event, in plain English, with no operator concepts leaked | `src/gridsignal/member.py`, `tests/test_member.py`, screen: **Member App** |
| Every spare kW is accounted for, not left idle: 9,425 kW offered on top of a 36,000 kW target at 10,000 devices ($2,608.63 simulated, $2,514.38 after modelled wear), and each held block names its reason — member reserve, the member's own home, another tenant, feeder cap, or a price under the wear floor | `control_room/engine.py::surplus_offer`/`offer_surplus`, `src/gridsignal/surplus.py` (`python -m gridsignal.surplus`), `tests/test_control_room.py`, screen: **Control Room → Spare capacity** |
| One scene an operator can read in ten seconds: 10,000 batteries on the real 2023-09-06 LZ_HOUSTON scarcity day, three faults at the price peak (gateway ring dark, 3% of homes stale, 12 spoofed cards refused on signature with 480 kW of phantom capacity), $13,618 of exposure, $13,436 of it protected after one approval — 99%, not 100%, because the reassigned kW is priced only over the 1.94 h left once the fix landed and the 3.4 degraded minutes are written off; 0.7 s wall clock; even that is bounded, not guaranteed: 9,617 kW of uncommitted headroom (5.9x the kW lost) was left in the healthy fleet and the replay prints it next to the recovery | `src/gridsignal/replay.py` (`python -m gridsignal.replay`), `tests/test_replay.py`, screen: **Control Room → Full-fleet scarcity replay** |
| Operator workflow on one screen: the incident as detect → diagnose → approve → reassign → recover with the dollars at the end, related alarms grouped into one incident (332 raw alarms from a dark gateway ring become 1 incident to work; a stale-telemetry wave on top makes 808 alarms 48 incidents, 16.8 per incident, never 1 page per alarm), and any award overridable by hand with a required reason written to the audit trail | `src/gridsignal/control_room/workflow.py`, `python -m gridsignal.workflow --devices 10000 --stale-wave`, `tests/test_workflow.py`, screen: **Control Room → Operator workflow** |
| Real data in, not only a simulation: a documented JSON-lines telemetry format (`device_id, ts, soc_kwh, power_kw, status, firmware, gateway`), a validating importer that writes to the same fleet state every panel reads, and a **Load telemetry file** replay mode in the app. 48 bundled synthetic rows load clean and move the fleet; the bad-row sample is refused 9 of 10 with a reason each — malformed, stale past one settlement interval, out of nameplate range, unknown device | `src/gridsignal/telemetry.py` (`python -m gridsignal.telemetry`), `data/telemetry/README.md`, `tests/test_telemetry.py`, screen: **Control Room → Load telemetry file** |
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
| Speed and scale report at 10,000 **and 100,000** agents with p50, p95, throughput and peak heap, before and after the hot-path fixes, from one command: whole pass 7.30 s -> 3.98 s at 100,000 agents (1.83x), peak heap 153 MiB, every stage linear in fleet size | [`docs/PERFORMANCE.md`](PERFORMANCE.md), `python -m gridsignal.perf --before` |
| Hot paths found by `cProfile` and fixed: canonical JSON per signature -> fixed-order separator join, `dataclasses.replace` per heartbeat -> direct construction, headroom recomputed three times per device per allocation -> measured once (recovery 3.1x faster, signing 1.7x, heartbeat 1.8x) | `src/gridsignal/mesh/cards.py`, `src/gridsignal/control_room/engine.py`, `src/gridsignal/perf_before.py` |
| Measured over a real transport, not a function call: coordinator and agents in separate OS processes, one loopback TCP socket each, HMAC-signed cards verified across the boundary. Detect to award p50 65.5 ms / p95 67.5 ms at 1,000 agents and 816.9 ms / 834.8 ms at 10,000 (~7,000 frames/s); under 5% packet loss the call still clears 100% and the tail moves to p95 2,339.3 ms, with one agent in 10,000 left unconfirmed after four re-sends. Local loopback, not a WAN | [`docs/PERFORMANCE.md`](PERFORMANCE.md), `python -m gridsignal.transport`, `src/gridsignal/transport.py`, `tests/test_transport.py` |
| Benchmark guard in CI: an absolute budget that fires on an accidental O(n^2), plus a machine-independent ratio against the implementations that were replaced | `tests/test_perf.py` |
| 10,000 devices, in-process compute on simulated state (no network, no device round trips): construct fleet ~100 ms, raise the incident from an in-memory snapshot ~1 ms, recompute the full allocation after approval ~36 ms | `tests/test_scale.py` (`pytest -q -s tests/test_scale.py`) |
| 10,000 agents, in-process compute (no messaging): build and verify signed cards ~169 ms, apply one heartbeat to each and re-evaluate staleness ~70 ms, score and award one contract-net call over 8,000 bids ~27 ms | `tests/test_simulate.py` (`pytest -q -s tests/test_simulate.py`) |
| 10,000-device staged rollout with per-ring gates in ~2 ms; 400 units commissioned into a 10,000-device mesh and re-auctioned in ~600 ms | `tests/test_rollout.py::test_ten_thousand_device_rollout_detects_the_bad_build_fast`, `tests/test_install.py::test_ten_thousand_device_install_wave_benchmark` |
| Fleet map thins healthy markers above 400 devices so a 10,000-device view stays interactive | `MAP_MARKERS` in `app/dashboard.py` |
| Analytics, held-out scoring, insight and chaos replays are cached per input in the dashboard | `@st.cache_data` on `signals_run`, `holdout_run`, `insight_run`, `chaos_run`, `jev_eval` |
| Jev never blocks the demo: recorded answers replay in microseconds, live median 360 ms; the judgment-pack answers are recorded fixtures too, so the blind score and the calibration run with no key and no network | `jev/client.py`, `data/jev_fixtures/judgment_*.json`, `python -m gridsignal.jev.evaluate` |
