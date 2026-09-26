# GridSignal Control Room — demo guide

**Simulation only.** The fleet, the failure and the recovery are deterministic local mock data.
The only real data is a cached ERCOT settlement-price trace used to put the incident in dollars.
The app never connects to a real battery, gateway, utility, ERCOT operational endpoint or any
control system, and it issues no dispatch commands.

## The failure scenario

| Step | What happens | Who acts |
|---|---|---|
| 1 | A simulated 48-device Texas home-battery fleet is committed to a 240 kW / 2 h peak-demand response window, placed on the most expensive window of a real LZ_HOUSTON trading day (2026-09-22, $138.39/MWh average). | system |
| 2 | **BAT-042** stops sending telemetry mid-event (simulated gateway uplink loss). | simulated device |
| 3 | The telemetry monitor flags the heartbeat gap (>120 s) and the orchestrator opens **INC-001** at severity `high`. | automatic |
| 4 | The incident panel states the root-cause hypothesis, the operational impact (committed capacity drops below the target, coverage falls under 100 %, **dollars at risk** priced at the real settlement price for the rest of the window) and a recommended recovery plan, and it opens tasks for Fleet Operator, Reliability Engineer and Field Support. | automatic |
| 5 | The incident parks in `awaiting_approval`. **No capacity moves.** | — |
| 6 | A human clicks **Approve Recovery Plan**. | Fleet Operator |
| 7 | Only then: BAT-042 is quarantined (`unavailable`, excluded from capacity), the 6.6 kW it carried is reassigned across healthy devices with headroom, coverage returns to 100 %, the **dollars recovered** are recorded against the dollars that were at risk, the Field Support site visit unblocks, and the incident resolves. | orchestrator, after approval |
| 8 | Every step above is appended to an immutable audit timeline with actor, timestamp and detail. | audit log |

`Reset Demo` returns the simulation to the stable baseline so judges can replay the story without
reloading the browser.

## Making the dollars matter

Two sidebar dials replay the same failure under conditions Base actually cares about:

- **Price scenario.** The normal peak day (2026-09-22) or a real ERCOT scarcity day
  (2023-09-06, settlement peaking at $5,147.65/MWh — the offer cap of the day plus reserve
  adders). Both are cached Parquet samples, so the switch works offline.
- **Fleet scale.** 48, 1,000 or 10,000 devices. The outage is a gateway firmware ring covering
  one device in 48, so the same root cause takes out 208 devices in a 10,000-device fleet.

| Fleet | Normal day | Scarcity day |
|---|---|---|
| 48 | $1.82 at risk | $55.39 at risk |
| 1,000 | $27.77 at risk | $847.44 at risk |
| 10,000 | $294.04 at risk | **$8,971.58 at risk, $8,682.72 recovered** |

One operator approval is worth roughly nine thousand dollars on a scarcity evening — per outage,
per fleet. Detection plus reallocation across 10,000 devices runs in well under a second
(`tests/test_scale.py` prints the measured build / detect / reallocate split on every test run).

## Home-first dispatch

The battery is an always-on infrastructure asset: it serves the grid while the grid is up and
backs the member up when it is not — but the house is always paid first. Every simulated home
draws its own load curve out of storage before a single kW is exported, so
`export kW = discharge kW − home load kW`. The Control Room's **Home-first dispatch** panel shows
that split live, broken down by unit type (legacy vs. simulated Base Core-style 40 kWh / 20 kW
units, *per public interview, not official specs*) and by tenant, because the `LZ_WEST` units are
controlled by a partner utility and the mesh never bids, awards or reassigns them.

The **Storm reserve policy** panel prices the storm policy: raising the reserve from 20% to 50%
takes kW off the export commitment and gives the member hours of backup back, and the panel
states both sides of that trade before the operator applies it (and writes it to the audit log).

## The same event, seen by the member

The **Member App** view is the other half of the story: while the operator reads kW, incident
severity and an approval gate, the member at 2646 Sabine St sees hours of backup still held for
their house, how much of the discharge is powering their home versus exported, what their battery
earned in the event, and a notice in plain English — "We've lost
contact with your battery … your battery is still running and still protecting your home" —
that turns into "Resolved … a technician visit is scheduled" after the operator approves. Homes
that absorbed the reallocated load see their slice of the recovered dollars under *Helped
protect*.

The separation is deliberate: no incident IDs, no kW targets and no approval controls are exposed
to the member. Backup hours assume a 1.2 kW essential household load and earnings assume a 60%
member revenue share; both are labelled assumptions, not a Base Power tariff.

Two more simulated member features sit on the same page: members with a small portable generator
see the extra kWh and hours it adds to a long outage, and a **Neighbour mutual aid** card offers
opted-in members the chance to send a little surplus to an opted-in neighbour who runs a medical
device — only ever from energy the giver holds above their own reserve, which
`tests/test_home.py` proves can never be breached.

## The same event, run as an agent mesh

The **Agent Mesh** view replays the same orchestration as a negotiation between agents rather
than a single controller. Every battery, gateway ring and load zone holds a signed capability
card; when capacity is lost a Coordinator broadcasts a call for capacity, healthy agents bid
their spare kW at a price reflecting wear and the homeowner's backup reserve, and the cheapest
covering set is *proposed* to a named human. Chaos scenarios live in `scenarios/*.yaml` and run
deterministically:

```bash
python -m gridsignal.simulate scenarios/zone_outage.yaml
```

The interesting failures are the dishonest and the absent: a card edited after signing fails
verification and is rejected, an agent that stops sending heartbeats goes stale, and a bidder
that wins an award and then goes quiet has its kW returned to the gap so a second (also
human-approved) round can cover it. When the remaining headroom genuinely cannot cover the
commitment — `scenarios/fleet_wide_scarcity.yaml` — the mesh commits the partial cover and
escalates the rest to a person instead of reporting success.

The design is inspired by MIT Project NANDA and NANDA Town
([nandatown.projectnanda.org](https://nandatown.projectnanda.org),
[github.com/projnanda](https://github.com/projnanda)); the implementation here is original and
vendors no NANDA code.

## Who decides: Jev between the code and the human

*Code acts, Jev decides, humans approve when Jev is unsure.* The mesh's arithmetic is
deterministic; the judgement calls go to Jev (TypeSafe AI's decision model) as four questions
over the incident snapshot — root cause as a choice, "is this card or bid trustworthy" alongside
the HMAC check, risk to the homeowner's backup as a score, and a confidence-gated approval
policy. A step is auto-approved only if every answer clears the confidence threshold (default
0.9), backup risk is low, the dollars at stake are under a cap and the plan covers the whole gap;
otherwise it routes to the same human gate, with Jev's probabilities on screen.

On all five bundled scenarios Jev never cleared that gate, so **every** award in the demo is
approved by a person. Root-cause accuracy is 3/5 for Jev against 5/5 for the deterministic rules
(which were written against these same injections), at a median 326 ms per decision. The numbers
are in the Agent Mesh view and the README as measured.

Judges need no API key: real Jev answers for every scenario are recorded in `data/jev_fixtures/`
with model version and timestamp and replayed offline. With neither fixture nor key the UI says
**Jev offline, rules fallback** and the rules answer instead — and the rules always report zero
confidence, so the fallback can never auto-approve anything.

## Held-out drills: events nobody designed for

Four more drills live in `scenarios/holdout/` and were written *after* the detection rules and
the Jev questions were frozen, then scored once with neither changed: a compound cascade in
waves (`cascade_spain_style`), an under-frequency event with the coordinator unreachable
(`frequency_dip_coordinator_down`), a neighbourhood islanding on its own batteries
(`neighborhood_island`) and a data-center-style load ramp with conflicting bids
(`large_load_squeeze`). **Every frequency, outage, load and islanding value in them is
simulated.**

```bash
python -m gridsignal.drills
```

The baseline, unedited: rules-only names the root cause 0/4 and Jev 1/4 — both reach for the
injection they were trained on instead of "the grid moved". What did hold is the safety
property: zero homeowner backup-reserve violations, zero auto-approvals, and 76–100% of each
gap covered with the remainder escalated. The frequency drill covers its whole 600 kW gap but
only once the coordinator returns, ~12,900 simulated cycles against the 15-cycle Fast Frequency
Response concept, because the cards carried no local self-deploy rule.

**After tuning on held-out**, with the baseline above kept as published: every card now carries
a pre-agreed `ffr_kw` pledge and a 59.85 Hz trigger, so batteries deploy 245 kW by themselves in
12 simulated cycles and the coordinator, when it returns, is told what was already deployed and
auctions only the remainder — the same 600 kW, counted once. Two rules now read the simulated
grid conditions (grid-side share of the missing kW, and homes islanded together), which takes
rules-only from 0/4 to 4/4. Jev moved from 1/4 to 2/4 once its answers were re-recorded against
the state that holds back the member reserve. Backup-reserve violations remained zero throughout, and the tuned-five results did not move.

## Congestion: the same days, read across the map

The **Grid Signals → congestion panel** adds all eight ERCOT load zones plus the hub average for
the same 15 bundled trade dates (`data/zones/`, one Parquet and one provenance JSON per date).
It shows the zone-to-hub basis as a zone-by-hour heatmap, the West-to-load-center spread by hour,
and what timing discharge to a zone's own price is worth against timing it to the hub average —
both policies settling at the *same* local prints, so the number isolates the signal, not the
location.

The finding is two-sided and both halves are on screen: **LZ_LCRA at hour 18 averaged $39.82/MWh
over the hub and 29.8% of all zone-intervals settled more than $5 away from it, yet re-timing to
the local price is worth only $0.13 per battery per day** (best `LZ_SOUTH` +$0.57, worst
`LZ_WEST` −$0.01). "Where to install next" places the next 1,000 batteries greedily and shows
each zone's marginal value decaying as it saturates — a **data-driven sketch on 15 days of
prices, not a forecast and not a siting study**. In the Control Room, the same ordering drives a
congestion dispatch preference: one zone discharges to its headroom first, with the target, the
homeowner reserve and the approval gate untouched.

```bash
python -m gridsignal.congestion
```

## Safety boundaries

- **No real-world effect.** No device commands, no utility or market integration, no credentials
  and no network calls at runtime. The fleet lives in memory (`src/gridsignal/fleet.py`); prices
  are read from a Parquet file committed to the repo.
- **Read-only market data.** ERCOT data comes from public MIS reports via `gridstatus`, fetched
  offline by `python -m gridsignal.ingest`. Nothing is ever sent to ERCOT or a market.
- **Human-in-the-loop gate.** `ControlRoomEngine.approve_recovery()` is the only path that mutates
  dispatch after a failure. Calling it with nothing pending raises `ApprovalError`, and the
  dashboard only exposes it via an explicit operator button.
- **Automation is limited to detect → explain → propose.** Recommendation text never executes.
- **Auditable.** Detection, recommendation, human approval, quarantine, reassignment and recovery
  are all recorded append-only, with the approver's name on the approval entry.
- **Clearly labelled.** The simulation banner and the sidebar safety note are always on screen.
- **The agent mesh is advisory too.** `Coordinator.propose()` only produces a plan; awarded kW is
  committed by `approve(call_id, approver)` and nowhere else, `execute()` raises
  `ApprovalRequired` without a recorded approval, and repeat triggers or double approvals return
  the award already on file instead of committing twice.
- **The model never gets a veto or a back door.** Jev only answers questions; auto-approval still
  goes through `Coordinator.approve()` and is logged as an auto-approval with the model, the
  confidence and the reason. Only simulated fleet state is sent, API keys are read from the
  environment and never committed, and no network call happens without a key — the default run
  and every test replay recorded answers.

## How this meets the Orchestration track

The track asks for a system that stays coordinated when a component fails. GridSignal Control Room
shows the whole loop for a distributed fleet:

- **Detect** — telemetry-staleness monitoring turns a silent device into a typed incident with
  severity, not just a red dot.
- **Explain** — the incident carries a root-cause hypothesis, quantified operational impact
  (kW lost against an active grid commitment, converted to dollars at risk with real ERCOT
  settlement prices: `kW x remaining hours x $/MWh`) and a concrete recommended action.
- **Coordinate** — work is fanned out to three roles with dependency-aware states: Field Support is
  `blocked` until the operator approves, so humans and automation stay in the same plan.
- **Recover** — after approval the orchestrator re-solves the dispatch allocation across remaining
  healthy headroom, so the fleet-level commitment is held even though a member device is gone.
- **Govern** — a human approves, and the audit timeline proves who decided what and when.

The interesting property is that the fleet's *commitment* survives a device failure without any
autonomous safety-critical action: degradation is contained at the device level, coverage is
restored at the fleet level, and a person owns the decision.

## Timed 5-minute demo script

Record the screen live; every number below comes from the running app, not from slides. Total
runtime 4:57 (the Spain-style cascade at 2:45, the canary halt at 3:05 and the congestion map at
3:36 are the strong beats). Start on **Control Room**, sidebar set to **Normal day / 48 devices**, demo reset.

| Time | Screen | Say and do |
|---|---|---|
| **0:00** | Control Room, stable | "Base Power runs thousands of home batteries as one plant. They make most of their money in a handful of hours a year — and that is exactly when a device goes quiet. This is GridSignal Control Room: 48 simulated batteries, 240 kW committed to a real ERCOT peak window, a human on the approval gate. Simulation only; it never touches a real device." |
| **0:30** | Control Room → failure | Click **Trigger BAT-042 Failure**. "A battery just stopped sending telemetry mid-event." Point at the red marker, coverage under 100%, and the high-severity incident: root-cause hypothesis, impact in kW, **dollars at risk** priced on the real settlement trace below the map, three role tasks with Field Support blocked. "It diagnosed, priced and planned — and then stopped." |
| **1:00** | Control Room → approval | Click **Approve Recovery Plan**. "A named person decides." Coverage snaps to 100%, BAT-042 is quarantined, dollars at risk become **dollars recovered**, and the audit timeline reads detection → recommendation → human approval → reassignment → recovery, append-only. |
| **1:30** | Agent Mesh → `zone_outage` | "Same orchestration, run as a mesh instead of a controller." Registry table: every battery, gateway and zone holds an HMAC-signed capability card — verified, stale, or **rejected**, because `lying_agent` edits its card after signing. Message log: a call for capacity, bids priced on wear plus the homeowner's backup reserve, cheapest covering award. |
| **2:10** | Agent Mesh → Jev + escalation | "Code acts, Jev decides, humans approve when Jev is unsure." Show Jev's root cause, confidence, probabilities and latency in the log. "Confidence never cleared 0.9 on any bundled scenario, so every award here is human-approved — and the rules fallback reports zero confidence, so it can never auto-approve." Switch to `fleet_wide_scarcity`: partial cover committed, remainder escalated to a person. |
| **2:45** | Agent Mesh → held-out drills | "Then we wrote four drills *after* freezing the rules and the prompts, from how real grids actually fail, and scored them once." Open `holdout/cascade_spain_style`: a simulated generation trip drops frequency, then gateways fail, then a stale-telemetry wave — in waves, so the faults interact, the shape ENTSO-E describes for the April 2025 Iberian blackout. "Both layers blamed the gateways. Rules 0 of 4, Jev 1 of 4 — published as it came out. What held is the part that matters: zero homeowner backup violations, zero auto-approvals, 76–100% of every gap covered. Then we fixed it and said so: the cards now carry a local 59.85 Hz rule, so the fleet self-deploys 245 kW in 12 simulated cycles without the coordinator and reconciles without double-counting. Rules go 4 of 4, Jev 2 of 4 — the rules improved, not the model. All of it simulated." |
| **3:05** | Agent Mesh → Rollout panel | "Orchestration is not only kW. The same fleet is a deployment target." Switch the Rollout panel to **bad build**: rings lab → 1% canary → 10% → 50% → 100%, each gated on heartbeat, charge/discharge response and backup reserve. "This build fails silently, only on the hot devices, and the heartbeat never stops — so the heartbeat gate passes it. The response gate halts it in the canary: 100 homes touched, 3 affected, all 100 rolled back, 420 simulated seconds to detect, across 10,000 devices. Nothing advances during a grid event, and every promotion past 10% needs a human." Scroll to **Install wave**: "400 new batteries joining mid-event — 10 rejected at the door because no installer check signed their card, the rest on probation until they pass the same gates, first eligible award at 300 s, zero kW to an unverified unit and zero taken off an existing commitment. All simulated." |
| **3:20** | Grid Signals → insight card | "Open Grid Data: what the public data hides. Across the ERCOT scarcity days bundled here, only **47%** of a battery's capturable value was visible in the day-ahead curve. The $18.83 per battery that shows up only in real time is worth about 28 ordinary trading days. All 19 intervals that printed 5x their day-ahead hour fell on scarcity days; the 12 ordinary days never diverged." |
| **3:36** | Grid Signals → congestion | "Second thing the data hides, this time across the map. All eight load zones plus the hub average, same 15 days. LZ_LCRA at 6pm prices **$39.82/MWh above the hub**, and **29.8%** of all zone-intervals sit more than $5 from it — but timing discharge to your own zone's price instead of the hub is worth only **$0.13 per battery per day**, best zone +$0.57. The congestion is large; what one battery collects by re-timing alone is not. Placement sketch: the next 1,000 batteries go mostly to Austin's LCRA zone and the marginal value falls as it saturates — a sketch on 15 days of prices, not a forecast and not a siting study." |
| **3:52** | Grid Signals → held-out days | "And the honest part. The first policy was rejected — it beat the naive schedule on 2 of 7 held-out days. Anchoring to the day-ahead curve and deviating only on real-time divergence made it 6 of 7, mean +$0.62, median +$0.10, worst −$0.27. Thresholds frozen, tuned on a separate split, scored once, losing days still on screen. And since the battery now serves the house first, the same days are re-scored home-first: 6 of 7 again, mean +$0.45, plus $0.48 a day the member never spent — but on the scarcity day the export revenue collapses from $17.84 to nothing, because the house drank the energy a trading battery would have sold into a $4,981 spike. That is the trade, and it is on screen." |
| **4:06** | Member App | "The same event from the member's side: the battery is powering their house first and exporting only the surplus, hours of whole-home backup still held — more if they own a generator — a neighbour mutual-aid card that can only ever give away energy above the giver's own reserve, what their battery earned, and 'we've lost contact with your battery — it is still protecting your home', which becomes 'resolved, a technician is scheduled' after the operator approved. No incident IDs, no kW, no buttons." |
| **4:20** | Control Room at scale | Set **Discharge first under congestion → LZ_HOUSTON** ("the zone order comes from that basis data; reserve and approval gate unchanged"), then sidebar → **Scarcity day**, **10,000 devices**; trigger and approve. "Real ERCOT scarcity prices, Base-scale fleet: **$8,971 at risk, $8,683 recovered** on one approval. Detection plus reallocation across 10,000 devices is ~138 ms; a 10,000-agent negotiation over 5,913 bids is ~22 ms." Show the architecture diagram in the README: ERCOT pipeline, agent mesh, Jev, human gate. |
| **4:42** | Close | Click **Reset Demo**. "Deterministic, replayable, offline, no API key. The fleet's commitment survives a device failure — and a person still owns the decision." |

Reset between takes with **Reset Demo**; the story replays without reloading the browser.
