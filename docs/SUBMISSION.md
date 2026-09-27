# Submission — GridSignal Control Room

**Project title:** GridSignal Control Room
**Tracks:** Orchestration (primary) + Open Grid Data
**Repository:** https://github.com/Regantih/gridsignal

## Checklist (due Sun Sep 27, 11:00 AM CT)

Submit via https://airtable.com/appWQWPtBqDUhCPPj/shrU4GuBeUnMzyrd5. One per team.

- [x] Project title — GridSignal Control Room
- [x] 2 to 5 min Loom demo showing the core loop live — https://www.loom.com/share/0632c671ea124d37b76a0b243a36103e (4:52, follows [`DEMO.md`](DEMO.md))
- [x] Repo set to PUBLIC — https://github.com/Regantih/gridsignal
- [x] README: quick start, stack + architecture diagram, reproduce steps, data provenance, limitations — [`../README.md`](../README.md)
- [x] Deployed URL or short screen capture — deploy steps below, or `python scripts/capture_demo.py`
- [x] Team roster (names, roles, contacts) — [`ROSTER.md`](ROSTER.md): Hemanth Reganti, Lead, regantih@gmail.com
- [x] 150 to 300 word write-up — below, and in [`WRITEUP.md`](WRITEUP.md)

## Write-up (150–300 words)

**Problem.** A home-battery fleet earns most of its money in a handful of hours a year, and
that is exactly when a device stops answering. Someone must notice the silence, price it and
fix it.

**Who it helps.** Fleet operators like Base's, and members who want the lights on.

**Solution.** GridSignal Control Room is a simulation-only console for a battery fleet run as a
mesh of agents. Every battery, gateway and zone publishes an HMAC-signed capability card; when
capacity is lost, healthy agents bid and the cheapest covering set wins. Jev, a fast model, judges root cause, trust and backup risk against six pre-committed principles;
signatures, rules and a named human still decide. Only then is the device quarantined, its kW
reassigned and the sequence audited. Related alarms collapse into one timeline; any award is
overridable with a reason. Dispatch is home-first: the house runs off the grid while storage is
held for the day-ahead peak, the member's reserve is never sold, a partner utility's units are
untouchable. It ships firmware in canary rings, takes JSON-lines telemetry, runs
agents as separate processes over multiplexed loopback TCP, and prices typed what-if scenarios
on the same engine at fleet scale, dispatching nothing.

**Impact.** One approval turns $4,812 at risk into $4,751 recovered across 10,000 simulated
devices on a real ERCOT scarcity day. Only 47% of a scarcity day's capturable value was visible
day-ahead. On seven held-out days, with the naive clock schedule allowed the same evening-peak
hold, the policy wins 5 of 7 but averages -$0.79 per simulated battery per day, median +$0.13;
against a battery that does nothing, median $1.64. Scored blind, the rules beat Jev 21/24 to
17/24. The scarcity-day headline is uplift, not revenue: $127.17 of export revenue against
$63.28 for the naive schedule, $63.89 per battery of uplift.

## Deployed URL

Live on Streamlit Community Cloud: [https://dashboardpy-shrqjtlddw5ewwevwderjagridsignal-control-room.streamlit.app/](https://dashboardpy-shrqjtlddw5ewwevwderjagridsignal-control-room.streamlit.app/)

Deployed from `main`, main file `app/dashboard.py`. No secrets are set, so Jev replays
`data/jev_fixtures/`. Redeploy steps are in [`docs/DEPLOY.md`](DEPLOY.md). The first cloud build
found one more packaging gap (the Jev principle pack was not shipped as package data); it is fixed
in `pyproject.toml`.

## Screen capture (fallback if the deploy is unavailable)

```bash
pip install -e ".[capture]"
python -m playwright install chromium
python scripts/capture_demo.py
```

Writes stills and a WebM screen recording to `docs/media/`: Control Room stable → BAT-042 failure → incident with
Jev's answer → human approval → recovery, then Agent Mesh, Grid Signals and Member App.

## Demo video

Recorded: https://www.loom.com/share/0632c671ea124d37b76a0b243a36103e

Record the screen while reading [`DEMO.md`](DEMO.md); it is timed to 4:50, it carries a shot
list (what is on screen, what to click, what to keep off camera) and the core loop is live, not
slides. Keep the "simulation only, a human approves every action" banner on screen during the
Control Room segment.

## Fresh-clone check

The README quick start was run from an empty directory on a 2 vCPU Linux box before submitting:
clone 1.3 s, `python3.11 -m venv .venv` 2.3 s, `pip install -e ".[dev]"` 14.7 s,
`python -m gridsignal.demo_numbers` 14.8 s, `streamlit run app/dashboard.py` serving in under
25 s, full suite 606 tests green with `AI_GATEWAY_API_KEY` and `TYPESAFE_API_KEY` unset. The
one deviation it found — the relative benchmark guard in `tests/test_perf.py` reading low when
the suite is sharded across two cores — is fixed by judging the best of paired samples.

## Judging evidence

[`JUDGING_MAP.md`](JUDGING_MAP.md) maps each sub-criterion to the file, test or screen that
proves it.
