# Judge dry run

A self-score against the official rubric, written to be read next to the code rather than
instead of it. [`docs/JUDGING_MAP.md`](JUDGING_MAP.md) says where each claim lives; this file
says how strong I think it is and where it is thin. Every number quoted here is printed by
`python -m gridsignal.demo_numbers`, and `tests/test_docs.py` fails the build if this file
drifts from it.

Everything in the repository is a **simulation** priced from real cached ERCOT settlement
data. Nothing here describes Base's real fleet, real hardware or real operating figures.

**Self-score: 85 / 100** (79 at the first dry run, 86 before the bug-fix pass). The pass moved
one line down and nothing up: giving the naive clock schedule the same evening-peak hold the
policy gets turns the held-out mean negative, so Insight comes down to 8. The screen fixes
(exact allocation, truthful pending verdict, labels that fit) repair contradictions rather
than add strength, so Completeness stays where it was.

| Rubric line | Points | Self-score | First dry run |
| --- | ---: | ---: | ---: |
| Technical Execution — Completeness | 15 | 14 | 13 |
| Technical Execution — Depth | 15 | 14 | 13 |
| Fit to Track — Problem | 15 | 11 | 11 |
| Fit to Track — Why | 15 | 12 | 12 |
| Value — Insight | 10 | 8 | 8 |
| Value — Usability | 10 | 9 | 7 |
| Innovation — Creativity | 10 | 8 | 8 |
| Innovation — Performance | 10 | 9 | 7 |
| **Total** | **100** | **85** | **79** |

## Technical Execution — Completeness — 14 / 15

**For.** The core workflow runs end to end and is exercised by machines, not by hand:
`scripts/no_crash_sweep.py` drives 29 CLI entry points — including the telemetry importer and
the transport benchmark — and 52 dashboard interactions: all five views, both price days, three
fleet scales, the failure/approve/override/reset loop, every scenario in the picker, both map
modes, the Advanced toggle. It runs with API keys stripped and an unroutable proxy, in CI,
inside four minutes. 707 tests pass with no key and no network. The README quick start was then
run from an empty directory on a 2 vCPU box before submission (clone to a serving app in under
a minute), which is how the one remaining deviation
was found and fixed: the relative benchmark guard read low when the suite was sharded across
two cores. The first frame is now exact rather than nearly right: the allocator distributes the
rounding remainder by largest remainder, so 10,000 devices commit 36,000.0 of 36,000 kW on the
scarcity day and the opening banner is green instead of reporting 1 kW at risk beside tiles
reading 100%.

**Against.** The app holds fleet state in a Streamlit session: two browsers are two fleets, and
a reload is a new one. There is no persistence, no auth and no multi-operator story, so
"complete" means the demo path is complete, not the product.

## Technical Execution — Depth — 14 / 15

**For.** The parts that could have been a wrapper are not one: HMAC-signed capability cards with
verified/stale/rejected states, contract-net bidding with idempotent awards, a deliverability
proof that re-runs at the approval gate, a staged rollout with per-ring health gates and
rollback, and a judgment layer whose pack and answer key were committed before the first score
(rules, Jev offline, 21 of 24; Jev 17 of 24). The optimisation work is honest too: the replaced
implementations live in `src/gridsignal/perf_before.py` and are swapped back in during the same
run, so the "before" column is code and not a memory.

The mesh is no longer only in-process either: `python -m gridsignal.transport` runs the
coordinator and the agents as separate OS processes over multiplexed loopback TCP, signs cards
in the agent process and verifies them in the coordinator's, refuses forged ones across the
wire, and measures detect-to-award under 5% packet loss. Published market limits are enforced
as objects that carry their source (`src/gridsignal/guardrails.py`), and the member backup
floor is proven load-bearing by re-running the same 957 simulated runs with the floor removed.

**Against.** The transport is loopback on one box: no WAN, no clock skew, no back-pressure and
no durable queue, and the honest failure mode is visible — under packet loss an agent can be
left unconfirmed after four attempts. The dashboard still reads the in-process engine, so the
process boundary is measured beside the product rather than under it.

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
headline is a rare day rather than a rate: inside the pilot rules a median +$0.15 per battery
on a held-out day, and ERCOT's ADER pilot rules cost 91% of the unrestricted value
because Reg Down is the product an aggregation of home batteries may not sell. Two more come
from asking what a rule is worth: the same fleet under Base's two published business models
prices the utility-partner access fee at the break-even the battery cannot pay on its own, and
the member backup floor turns out to be load-bearing rather than decorative — the same walk
with the floor removed spends promised backup in 335 intervals.

**Against.** Once the naive clock schedule is allowed the same evening-peak hold, the held-out
uplift is 5 of 7 days at **mean −$0.79 and median +$0.13** per battery per day: one scarcity day
goes to the clock schedule by $14.98 and takes the mean with it. Against a battery that does
nothing the median day is worth $1.64. That is enough to say the policy is not broken and
nowhere near enough to size a business.

## Value — Usability — 9 / 10

**For.** One command starts it, no key or account is needed, the operator works one incident
instead of 332 pages, every override demands a reason and lands in an append-only log, and the
Member App says the same event in plain English without leaking operator concepts.

Real data can go in: a documented JSON-lines telemetry format
(`data/telemetry/README.md`), a validating importer that rejects malformed, stale, future-dated,
unknown and over-nameplate rows with a line number and a reason, and a **Load telemetry file** mode
that feeds the same fleet state the Control Room reads.

**Against.** It is still not deployed: Community Cloud needs a sign-in I do not have, so
[`docs/DEPLOY.md`](DEPLOY.md) is two clicks of instructions instead of a URL. The telemetry path
is a file import rather than a live stream, and there is no paging integration and no roles
beyond labels, so "Base could use it tomorrow" is true of the workflow and of an export, not of
a running deployment.

## Innovation — Creativity — 8 / 10

**For.** The combination is the novel part: a NANDA-style signed-card agent mesh over a battery
fleet, a market backtest on real ERCOT data, and a model used as a second opinion that can only
escalate, all reading one engine and one design system.

**Against.** Each ingredient exists elsewhere; what is new here is the assembly and the safety
framing, not a new technique.

## Innovation — Performance — 9 / 10

**For.** The whole pass at 100,000 agents is 1.83x faster than the first implementation after
three profiled hot-path fixes, with p50/p95, throughput and peak heap from one command and a
CI guard that fails if an optimisation is undone. The dashboard stays interactive at 10,000
devices, and the Why page is now computed once per process so it loads instantly on camera.
Speed is also measured across a process and socket boundary: detect-to-award p50/p95 of
27.9 / 28.9 ms at 1,000 agents and 222.6 / 231.9 ms at 10,000 (109,315 frames/s, 100% covered)
on a 2 vCPU Linux box with `ulimit -n 256`, the whole default command in 0.81 s, and the tail
under 5% packet loss reported rather than hidden.

**Against.** Absolute times are machine-dependent — the same compute run measured 10.39 s →
5.83 s (1.78x) on a slower box — and the transport is loopback, so it prices serialisation,
scheduling and the coordinator's fan-out, not a network. The product path itself is still
single-process compute.

## The three weakest lines, and what would fix them

1. **Fit to Track — Problem (11 / 15).** The fleet is synthetic. *Fix:* replace the modelled
   home-load profiles with a public residential load dataset (NREL ResStock) and calibrate the
   device mix against published Base interviews, so the shape of the fleet has a citation and
   the sensitivity of every dollar figure to it can be reported.
2. **Fit to Track — Why (12 / 15).** The case that this is worth an operator's day is argued
   from a simulation of Base. *Fix:* one conversation with an operator, and the six principles
   and the escalation thresholds are theirs rather than mine.
3. **Value — Usability (9 / 10).** Still no live URL, and telemetry arrives as a file rather
   than a stream. *Fix:* the owner signs in to Streamlit Community Cloud and deploys the branch
   (the repo is prepared and the two clicks are written down), and the importer grows an
   MQTT or CSV-tail adapter behind the same interface.

What the earlier dry run named as the two weakest lines has since been built: the mesh now runs
over a real transport between processes (Performance 7 → 9) and the fleet takes telemetry from
an export (Usability 7 → 9).
