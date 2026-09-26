# GridSignal Control Room — demo guide

**Simulation only.** The fleet, the failure, the frequency and the recovery are deterministic
local mock data. The only real data is cached historical ERCOT settlement and day-ahead prices,
read from Parquet files in this repo, used to put the incident in dollars. The app never connects
to a real battery, gateway, utility, ERCOT operational endpoint or any control system, and it
issues no dispatch commands.

## Before you record: pre-warm

Streamlit builds a 10,000-device fleet and the price analytics on first use, so warm the caches
before the take — otherwise the first click spends ten seconds on a spinner:

```bash
streamlit run app/dashboard.py
# then, in the browser, once each: Why, Grid Signals, Agent Mesh (run `lying_agent` and the
# rollout panel's bad build), Member App, and back to Control Room.
# sidebar: Scarcity day / 10,000 devices, then click Reset Demo.
```

Every number spoken below is recomputed by one command, which the docs test checks against this
script:

```bash
python -m gridsignal.demo_numbers
```

## The script — one story, 4:50

One evening on a real ERCOT scarcity day: the fleet is committed, a ring of homes goes dark, the
orchestration recovers the commitment, a person signs it, and every member keeps their backup.
Start on **Control Room**, sidebar set to **Scarcity day / 10,000 devices**, demo reset. Each
scene is one click from the last.

| Time | Screen and click | Say | Rubric line and file |
|---|---|---|---|
| **0:00** | Control Room, stable | "Base Power runs thousands of home batteries as one power plant, and most of the money arrives in a handful of hours a year. This is a simulation of that fleet on a real ERCOT scarcity evening: 10,000 batteries, 8,000 of them ours to dispatch, 36,000 kW promised to the grid. It never touches a real device." | Fit to Track / Problem — `src/gridsignal/control_room/engine.py` |
| **0:30** | Click **Trigger BAT-042 Failure** | "A gateway ring goes quiet: 166 homes, 573 kW gone mid-event. The fleet prices the hole in its own commitment — $4,812 at risk on this evening's settled prices — and it plans a recovery across the headroom it still has. Then it stops, because nothing moves without a person." | Technical Execution / Depth — `src/gridsignal/control_room/engine.py` |
| **1:05** | Control Room → **Operator workflow** | "One incident, not a wall of pages: 332 raw alarms from those homes group into 1 incident on a timeline — detect, diagnose, approve, reassign, recover, dollars — and every hand-override needs a reason that lands in an append-only trail." | Value / Usability — `src/gridsignal/control_room/workflow.py` |
| **1:40** | Control Room → principles panel | "Above the button are the six principles an operator weighs: member backup, market rules and deliverability are hard vetoes, then reversibility, money and doubt. The rules and the vetoes decide. The model is a second opinion that can escalate but never approve — on a safety pack committed before it was scored, the rules layer (Jev offline) answered 21 of 24 and Jev 17 of 24." | Technical Execution / Depth — `src/gridsignal/jev/judgment.py` |
| **2:10** | Click **Approve Recovery Plan** | "A named human approves. The lost kW are reassigned across healthy batteries that each still hold their member's backup reserve, coverage returns to 100%, and $4,751 of that $4,812 is recovered." | Fit to Track / Why — `src/gridsignal/control_room/engine.py` |
| **2:35** | Sidebar **View → Member App**, home **BAT-001** | "This is one of the homes that carried it, and nothing here is about the grid. The house takes 1.7 kW before a single kW is exported, 5.5 kW goes out, the member earns $28.07 — and 9.4 hours of backup are still in the wall. The home that failed sees one sentence: your battery is reporting to us again." | Value / Insight — `src/gridsignal/member.py` |
| **3:05** | **View → Agent Mesh**, run `lying_agent` | "The recovery is a negotiation, not a broadcast. Every battery and gateway holds a signed capability card. One agent edits its card after signing to claim capacity it does not have, the signature check rejects it, honest bidders cover all 67 kW, and the award still waits for a human: 1 approval, 0 self-approvals by Jev." | Innovation / Creativity — `src/gridsignal/mesh/` |
| **3:35** | Rollout panel → **bad build** | "A fleet is a deployment target too. This build fails on hot devices and keeps its heartbeat, so it passes the heartbeat gate — the response gate halts it in the canary: across 10,000 devices, 100 homes touched, 3 affected, all rolled back, 420 simulated seconds to detect." | Technical Execution / Completeness — `src/gridsignal/rollout.py` |
| **4:00** | **View → Grid Signals** | "Why this is worth orchestrating: on the bundled scarcity days the day-ahead curve exposed only 47% of the capturable value, and the $18.83 per battery that shows up only in real time is worth about 28 ordinary trading days. Scored once on 7 days the policy never saw, it beats the naive schedule on 7 of 7 — mean $2.96, median $1.94, on a 40 kWh Base Core-style unit that keeps its charge for the evening peak instead of spending it on the afternoon house." | Value / Insight — `src/gridsignal/holdout.py` |
| **4:25** | **View → Why**, then close | "Every number on this page was recomputed from the code as it loaded, limits included. A ring of homes failed, the commitment survived, every member kept their backup, and a person owned the decision — deterministic, offline, no API key." | Fit to Track / Why — `src/gridsignal/why.py` |

Ends at 4:50. Speed and scale are deliberately not spoken: the fleet-scale timings, the speedup
ratio and the caveat that absolute times are machine-dependent live in
[`docs/PERFORMANCE.md`](PERFORMANCE.md).

## What is cut from the five-minute version

Home-first dispatch in detail, the storm reserve policy, mutual aid, the congestion map, the
ancillary co-optimization, the deliverability check and the held-out chaos drills
(`python -m gridsignal.drills`) are all in the app and in the README; they are left out of the
script to keep it to one story. The **Why** page is the one to leave on screen for questions:
it states the problem, the approach, the evidence and the limits with every figure recomputed
as the page loads (`python -m gridsignal.why`). The chaos-scenario picker lists the
files in `scenarios/`; the held-out drills in `scenarios/holdout/` are deliberately kept out of
it and run from the CLI, so the script never asks for a scenario the picker does not have.

## Safety boundaries

- **No real-world effect.** No device commands, no utility or market integration, no credentials
  and no network calls at runtime.
- **Human-in-the-loop gate.** `ControlRoomEngine.approve_recovery()` and
  `Coordinator.approve()` are the only paths that move capacity; both record the approver.
- **The model never approves and never vetoes.** Jev only answers questions, only sees simulated
  fleet state, and its confidence gate is a reading logged with the model, confidence and reason
  — no code path commits kW because the gate read clear. With no key and no fixture the UI reads
  **rules (Jev offline)** and the rules report zero confidence.
- **Everything simulated is labelled.** Frequency, outages, home loads, firmware builds, install
  waves and member behaviour are all modelled; the prices are historical and read-only.
