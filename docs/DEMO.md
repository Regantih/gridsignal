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

## 60–90 second demo script

1. **(0:00)** "This is GridSignal Control Room — a simulation of Base-style home batteries
   responding to an ERCOT peak event. 48 devices, 240 kW committed, everything green."
2. **(0:15)** Click **Trigger BAT-042 Failure**. "A device just dropped off mid-event."
3. **(0:25)** Point at the map/grid: BAT-042 is red; coverage drops below 100 % and the banner
   flags the commitment at risk.
4. **(0:35)** Read the incident panel: severity, root-cause hypothesis, impact, recommended action,
   owner, and **dollars at risk** priced on the real ERCOT price trace shown below the map.
   "The system diagnosed, priced and planned — but it did not act."
5. **(0:50)** Show the collaboration panel: Reliability Engineer is validating, Field Support is
   blocked pending approval.
6. **(1:00)** Click **Approve Recovery Plan**. "A human owns this decision."
7. **(1:10)** Coverage snaps back to 100 %, the dollars at risk turn into dollars recovered,
   BAT-042 is quarantined, tasks advance, and the audit timeline shows detection → recommendation
   → human approval → reassignment → recovery.
8. **(1:25)** Click **Reset Demo**. "Fully replayable, deterministic, and simulation-only."
