# Submission — GridSignal Control Room

**Project title:** GridSignal Control Room
**Tracks:** Orchestration (primary) + Open Grid Data
**Repository:** https://github.com/Regantih/gridsignal

## Checklist (due Sun Sep 27, 11:00 AM CT)

Submit via https://airtable.com/appWQWPtBqDUhCPPj/shrU4GuBeUnMzyrd5. One per team.

- [x] Project title — GridSignal Control Room
- [ ] 2 to 5 min Loom demo showing the core loop live — record from [`DEMO.md`](DEMO.md) (timed to 4:55)
- [ ] Repo set to PUBLIC — repository setting, do this before submitting
- [x] README: quick start, stack + architecture diagram, reproduce steps, data provenance, limitations — [`../README.md`](../README.md)
- [x] Deployed URL or short screen capture — deploy steps below, or `python scripts/capture_demo.py`
- [ ] Team roster (names, roles, contacts) — fill in [`ROSTER.md`](ROSTER.md)
- [x] 150 to 300 word write-up — below, and in [`WRITEUP.md`](WRITEUP.md)

## Write-up (150–300 words)

**Problem.** A distributed home-battery fleet earns most of its money in a handful of hours a
year, and that is exactly when a device stops answering. The operator has to notice the silence,
price it against the commitment and coordinate the fix — while the market clears at the cap.

**Who it helps.** Fleet operators and reliability engineers at a company like Base Power, the
field techs they dispatch, and the member whose battery went dark and only wants to know their
lights still have backup.

**Solution.** GridSignal Control Room is a simulation-only operator console for a fleet of
batteries run as a mesh of agents. Every battery, gateway and zone publishes an HMAC-signed
capability card; when capacity is lost, a coordinator calls for it, healthy agents bid a price
reflecting wear and the member's backup reserve, and the cheapest covering set is proposed. Jev,
a fast decision model, judges root cause, trustworthiness and backup risk with a confidence — but
signatures, rules and a named human approval decide. Only then is the device quarantined, its kW
reassigned and the sequence audited. Dispatch is home-first: each battery serves its simulated
household load before exporting the surplus, a partner utility's units are a separate tenant the
mesh may never touch, and a neighbour mutual-aid card can never spend a giver's reserve. The same
orchestration ships firmware in gated canary rings, while Grid Signals scores a
day-ahead-anchored policy on real ERCOT prices and maps congestion.

**Impact.** One approval turns $8,971 at risk into $8,683 recovered across 10,000 simulated
devices on a real ERCOT scarcity day, in about 138 ms. Only 47% of a battery's capturable value
on the bundled scarcity days was visible day-ahead, and serving the home first costs export
revenue — reported as measured, not smoothed.

## Deployed URL

Streamlit Community Cloud, no secrets required:

1. Repo public on GitHub.
2. [share.streamlit.io](https://share.streamlit.io) → **Create app** → deploy from GitHub.
3. Repository `Regantih/gridsignal`, branch `main`, main file `app/dashboard.py`, Python 3.11.
4. Dependencies come from `requirements.txt` (installs the package itself).
5. Optional: add `AI_GATEWAY_API_KEY` or `TYPESAFE_API_KEY` under **Settings → Secrets** to run
   Jev live instead of replaying the recorded answers.

Paste the resulting `*.streamlit.app` URL into the submission form.

## Screen capture (fallback if the deploy is unavailable)

```bash
pip install -e ".[capture]"
python -m playwright install chromium
python scripts/capture_demo.py
```

Writes stills and a WebM screen recording to `docs/media/`: Control Room stable → BAT-042 failure → incident with
Jev's answer → human approval → recovery, then Agent Mesh, Grid Signals and Member App.

## Demo video

Record the screen while reading [`DEMO.md`](DEMO.md); it is timed to 4:55 and the core loop is
live, not slides. Keep the "simulation only, a human approves every action" banner on screen
during the Control Room segment.

## Judging evidence

[`JUDGING_MAP.md`](JUDGING_MAP.md) maps each sub-criterion to the file, test or screen that
proves it.
