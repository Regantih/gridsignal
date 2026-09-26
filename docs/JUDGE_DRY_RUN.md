# Judge dry run

A self-score against the official rubric, written to be read next to the code rather than
instead of it. [`docs/JUDGING_MAP.md`](JUDGING_MAP.md) says where each claim lives; this file
says how strong I think it is and where it is thin. Every number quoted here is printed by
`python -m gridsignal.demo_numbers`, and `tests/test_docs.py` fails the build if this file
drifts from it.

Everything in the repository is a **simulation** priced from real cached ERCOT settlement
data. Nothing here describes Base's real fleet, real hardware or real operating figures.

**Self-score: 79 / 100.**

| Rubric line | Points | Self-score |
| --- | ---: | ---: |
| Technical Execution — Completeness | 15 | 13 |
| Technical Execution — Depth | 15 | 13 |
| Fit to Track — Problem | 15 | 11 |
| Fit to Track — Why | 15 | 12 |
| Value — Insight | 10 | 8 |
| Value — Usability | 10 | 7 |
| Innovation — Creativity | 10 | 8 |
| Innovation — Performance | 10 | 7 |
| **Total** | **100** | **79** |

## Technical Execution — Completeness — 13 / 15

**For.** The core workflow runs end to end and is exercised by machines, not by hand:
`scripts/no_crash_sweep.py` drives 22 CLI entry points and 46 dashboard interactions — all five
views, both price days, three fleet scales, the failure/approve/override/reset loop, every
scenario in the picker, both map modes — with API keys stripped and an unroutable proxy, and it
runs in CI. 448 tests pass with no key and no network in 134 s.

**Against.** The app holds fleet state in a Streamlit session: two browsers are two fleets, and
a reload is a new one. There is no persistence, no auth and no multi-operator story, so
"complete" means the demo path is complete, not the product.

## Technical Execution — Depth — 13 / 15

**For.** The parts that could have been a wrapper are not one: HMAC-signed capability cards with
verified/stale/rejected states, contract-net bidding with idempotent awards, a deliverability
proof that re-runs at the approval gate, a staged rollout with per-ring health gates and
rollback, and a judgment layer whose pack and answer key were committed before the first score
(rules, Jev offline, 21 of 24; Jev 17 of 24). The optimisation work is honest too: the replaced
implementations live in `src/gridsignal/perf_before.py` and are swapped back in during the same
run, so the "before" column is code and not a memory.

**Against.** The mesh is in-process: messages are appended to a list, not sent. There is no
transport, no partial failure between agents, no clock skew, no back-pressure — the hardest
parts of a real distributed system are modelled away, and the benchmark numbers inherit that.

## Fit to Track — Problem — 11 / 15

**For.** The problem is the right one and is stated with a price on it: on the real 2023-09-06
LZ_HOUSTON scarcity day a dark gateway ring puts 166 homes and 573 kW out, $4,812 at risk, and
one approval brings back $4,751 with coverage at 100%. Failures nobody designed for — a cascade,
an under-frequency event, an island, a large-load squeeze — replay deterministically from YAML.

**Against.** Every device, every home load and every failure is synthetic. The fleet is
Base-shaped because I modelled it that way from public interviews, not because it was measured,
so the numbers argue that the *mechanism* is right, not that the magnitudes are.

## Fit to Track — Why — 12 / 15

**For.** The Why view states problem, approach, evidence and limits on one screen with every
figure recomputed from the module that produces it and the reproducing command printed beside
it; `python -m gridsignal.why` prints the same page. The limits are on that screen rather than
buried: held-out agreement moves 71% to 83% after tuning, +6 of 48 episodes of one
simulated operator and so weak evidence that the weights transfer; the
unrestricted ancillary comparison offers 51% of ERCOT's published Reg Down plan so its
price-taker assumption fails, and seven held-out days is a small sample.

**Against.** The strongest "why" — that this is worth an operator's day — is argued from a
simulation of Base rather than from anything Base has said it needs.

## Value — Insight — 8 / 10

**For.** The insights are specific, checkable and mostly unflattering: only 47% of a scarcity
day's capturable value is visible in the day-ahead curve, $18.83 per battery exists only in
real time, all 19 intervals that printed 5x their day-ahead hour fell on scarcity days, wear
gating pays on a legacy pack and never binds on a Base Core-style one, and the ancillary
headline is a rare day inside ERCOT's ADER pilot rules (+$0.14 median) rather than a rate —
the pilot rules cost 92% of the unrestricted value because Reg Down is the product an
aggregation of home batteries may not sell.

**Against.** The held-out uplift is 7 of 7 days at mean $2.96 and median $1.94 per battery per
day, but on seven days only, and two scarcity days carry most of the mean. That is enough to say
the policy is not broken and not enough to size a business.

## Value — Usability — 7 / 10

**For.** One command starts it, no key or account is needed, the operator works one incident
instead of 332 pages, every override demands a reason and lands in an append-only log, and the
Member App says the same event in plain English without leaking operator concepts.

**Against.** It is not deployed: Community Cloud needs a sign-in I do not have, so
[`docs/DEPLOY.md`](DEPLOY.md) is two clicks of instructions instead of a URL. There is no
ingest of live telemetry, no paging integration and no roles beyond labels, so "Base could use
it tomorrow" is true of the workflow and not of the deployment.

## Innovation — Creativity — 8 / 10

**For.** The combination is the novel part: a NANDA-style signed-card agent mesh over a battery
fleet, a market backtest on real ERCOT data, and a model used as a second opinion that can only
escalate, all reading one engine and one design system.

**Against.** Each ingredient exists elsewhere; what is new here is the assembly and the safety
framing, not a new technique.

## Innovation — Performance — 7 / 10

**For.** The whole pass at 100,000 agents is 1.83x faster than the first implementation after
three profiled hot-path fixes, with p50/p95, throughput and peak heap from one command and a
CI guard that fails if an optimisation is undone. The dashboard stays interactive at 10,000
devices, and the Why page is now computed once per process so it loads instantly on camera.

**Against.** Absolute times are machine-dependent — the same run measured 10.39 s → 5.83 s
(1.78x) on a slower box — and all of it is single-process compute. No concurrency, no I/O, no
network: this measures the maths, not the system.

## The three weakest lines, and what would fix them

1. **Value — Usability (7 / 10).** No live URL and no data in from a real device. *Fix:* the
   owner signs in to Streamlit Community Cloud and deploys the branch (the repo is already
   prepared and the two clicks are written down), and the fleet reads telemetry from a small
   MQTT or CSV-tail adapter behind the same engine interface, so the Control Room can run
   against something that is not a generator.
2. **Innovation — Performance (7 / 10).** Every benchmark is single-process compute. *Fix:* put
   the mesh behind a real transport (an in-memory broker first, then a socket), measure
   negotiation latency with message loss and clock skew, and publish the throughput ceiling
   where the coordinator, not the maths, becomes the bottleneck.
3. **Fit to Track — Problem (11 / 15).** The fleet is synthetic. *Fix:* replace the modelled
   home-load profiles with a public residential load dataset (NREL ResStock) and calibrate the
   device mix against published Base interviews, so the shape of the fleet has a citation and
   the sensitivity of every dollar figure to it can be reported.
