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
# then, in the browser, once each: Grid Signals, Agent Mesh (run `lying_agent` and the
# rollout panel's bad build), Member App, and back to Control Room.
# sidebar: Scarcity day / 10,000 devices, then click Reset Demo.
```

Every number spoken below is recomputed by one command, which the docs test checks against this
script:

```bash
python -m gridsignal.demo_numbers
```

## The script — three beats, 4:40

Start on **Control Room**, sidebar set to **Scarcity day / 10,000 devices**, demo reset.

| Time | Screen | Say and do |
|---|---|---|
| **0:00** | Control Room, stable | "Base Power runs thousands of home batteries as one power plant, and most of the money is in a handful of hours a year. This is a simulation of that fleet: 10,000 batteries, 8,000 of them ours to dispatch, 36,000 kW committed to a real ERCOT scarcity evening. It never touches a real device." |
| **0:25** | Control Room → failure | Click **Trigger BAT-042 Failure**. "One gateway ring goes quiet: 166 homes, 573 kW gone mid-event. Coverage drops to 98%, and the incident prices it — $4,812 at risk on this evening's settled prices. It diagnosed, priced and planned a recovery across the remaining headroom, and then it stopped, because nothing moves without a person." |
| **0:55** | Control Room → approval | Click **Approve Recovery Plan**. "A named human approves. The lost kW are reassigned across healthy batteries that still hold every member's backup reserve, coverage returns to 100%, and $4,751 of that $4,812 is recovered. One incident, not 332 alarms — the operator workflow groups them, and reads the append-only trail back as one timeline: detect, diagnose, approve, reassign, recover, dollars." |
| **1:30** | Agent Mesh → `lying_agent` | "Same orchestration as a negotiation. Every battery, gateway and zone holds an HMAC-signed capability card." Run the scenario. "One agent edits its card after signing to claim capacity it does not have. The signature check rejects it, the honest bidders cover all 67 kW, and the award still waits for a human — 1 approval, 0 auto-approvals. Jev's confidence never cleared the gate, and the rules fallback answers with zero confidence, so nothing here can approve itself." |
| **2:10** | Agent Mesh → Rollout panel | Switch the Rollout panel to **bad build**. "A fleet is also a deployment target. Rings go lab, 1%, 10%, 50%, 100%, each gated on heartbeat, charge and discharge response, and backup reserve held — and nothing promotes during a grid event or above 10% without a human. This build fails silently on hot devices and keeps its heartbeat, so the heartbeat gate passes it. The response gate halts it in the canary: across 10,000 devices, 100 homes touched, 3 affected, all 100 rolled back, 420 simulated seconds to detect." |
| **2:55** | Grid Signals → insight card | "What the public data hides. On the 3 real ERCOT scarcity days bundled here, the day-ahead curve exposed only 47% of the value a battery could have captured. The $18.83 per battery that shows up only in real time is worth about 28 ordinary trading days. And 19 of 1,440 intervals printed 5x their day-ahead hour — every one of them on a scarcity day." |
| **3:35** | Grid Signals → held-out days | "So we scored the policy on 7 days it never saw, once. It beats the naive schedule on 6 of 7: mean $0.44, median $0.26, worst day $0.00 — it sits out rather than lose money. The first scoring let each interval see its own settled price, which no operator has; corrected to the last settled print, mean uplift is $0.44 against $0.45, and both are on screen." |
| **4:15** | Close | Click **Reset Demo**. "Deterministic, offline, no API key: one command reproduces every number you just heard. A device failed, the fleet's commitment survived, every member kept their backup — and a person owned the decision." |

## What is cut from the five-minute version

The Member App, home-first dispatch, the storm reserve policy, the held-out chaos drills
(`python -m gridsignal.drills`) and the congestion map are all in the app and in the README;
they are left out of the script to keep it to one story. The chaos-scenario picker lists the
files in `scenarios/`; the held-out drills in `scenarios/holdout/` are deliberately kept out of
it and run from the CLI, so the script never asks for a scenario the picker does not have.

## Safety boundaries

- **No real-world effect.** No device commands, no utility or market integration, no credentials
  and no network calls at runtime.
- **Human-in-the-loop gate.** `ControlRoomEngine.approve_recovery()` and
  `Coordinator.approve()` are the only paths that move capacity; both record the approver.
- **The model never gets a veto.** Jev only answers questions, only sees simulated fleet state,
  and auto-approval is logged as an auto-approval with the model, confidence and reason. With no
  key and no fixture the UI reads **Jev offline, rules fallback** and the rules report zero
  confidence, so the fallback cannot auto-approve.
- **Everything simulated is labelled.** Frequency, outages, home loads, firmware builds, install
  waves and member behaviour are all modelled; the prices are historical and read-only.
