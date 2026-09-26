# Submission — GridSignal Control Room

**Project title:** GridSignal Control Room
**Tracks:** Orchestration (primary) + Open Grid Data
**Repository:** https://github.com/Regantih/gridsignal

## Checklist (due Sun Sep 27, 11:00 AM CT)

Submit via https://airtable.com/appWQWPtBqDUhCPPj/shrU4GuBeUnMzyrd5. One per team.

- [x] Project title — GridSignal Control Room
- [ ] 2 to 5 min Loom demo showing the core loop live — record from [`DEMO.md`](DEMO.md) (timed to 4:40)
- [ ] Repo set to PUBLIC — repository setting, do this before submitting
- [x] README: quick start, stack + architecture diagram, reproduce steps, data provenance, limitations — [`../README.md`](../README.md)
- [x] Deployed URL or short screen capture — deploy steps below, or `python scripts/capture_demo.py`
- [ ] Team roster (names, roles, contacts) — fill in [`ROSTER.md`](ROSTER.md)
- [x] 150 to 300 word write-up — below, and in [`WRITEUP.md`](WRITEUP.md)

## Write-up (150–300 words)

**Problem.** A distributed home-battery fleet earns most of its money in a handful of hours a
year, and that is exactly when a device stops answering. The operator has to notice the silence,
price it and coordinate the fix.

**Who it helps.** Fleet operators and reliability engineers at a company like Base, their field
techs, and members who only want their lights to stay on.

**Solution.** GridSignal Control Room is a simulation-only operator console for a fleet of
batteries run as a mesh of agents. Every battery, gateway and zone publishes an HMAC-signed
capability card; when capacity is lost, healthy agents bid a price reflecting wear and the
member's backup reserve, and the cheapest covering set is proposed. Jev, a fast decision model,
judges root cause, trust and backup risk with a confidence, but signatures, rules and a named
human decide. Only then is the device quarantined, its kW
reassigned and the sequence audited. Related alarms collapse into one incident timeline; any
award is overridable by hand with a logged reason. Dispatch is home-first: each battery serves
its simulated household load before exporting, a partner utility's units are a tenant the mesh
may never touch, and mutual aid never spends a giver's reserve. The same
orchestration ships firmware in gated canary rings; Grid Signals scores a day-ahead-anchored
policy on real ERCOT prices and prices the wear of each cycle, so a spread too thin to pay for
the pack is not taken.

**Impact.** One approval turns $4,812 at risk into $4,761 recovered across 10,000 simulated
devices on a real ERCOT scarcity day, in ~0.1 s of compute. Only 47% of capturable scarcity-day
value was visible day-ahead; the home eats export revenue; and wear-gating at a modelled
$100/MWh skips 5.55 of 7.55 held-out cycles on an older pack. Measured, not smoothed.

## Deployed URL

_Not deployed yet: Community Cloud needs a sign-in with the repository owner's GitHub account,
which this build cannot create. Everything else is done — exact steps in
[`docs/DEPLOY.md`](DEPLOY.md), two clicks after sign-in._

1. [share.streamlit.io](https://share.streamlit.io) → sign in with GitHub (private repo is fine;
   grant the Streamlit GitHub App access — the repo does **not** need to be made public).
2. **Create app → Deploy from GitHub**: repository `Regantih/gridsignal`, branch
   `devin/1790382033-control-room` (or `main` after merge), main file `app/dashboard.py`,
   **Advanced settings → Python 3.11**.
3. Dependencies come from `requirements.txt` (installs the package itself). No secrets required;
   Jev replays `data/jev_fixtures/`. Optional: `AI_GATEWAY_API_KEY` or `TYPESAFE_API_KEY` under
   **Settings → Secrets** to run Jev live.

A clean non-editable install (what the host does) was reproduced from a fresh clone; the one
deployment bug it found — bundled data not found when the package is installed outside the
checkout — is fixed in `src/gridsignal/paths.py` and guarded by `tests/test_paths.py`.

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

Record the screen while reading [`DEMO.md`](DEMO.md); it is timed to 4:40 and the core loop is
live, not slides. Keep the "simulation only, a human approves every action" banner on screen
during the Control Room segment.

## Judging evidence

[`JUDGING_MAP.md`](JUDGING_MAP.md) maps each sub-criterion to the file, test or screen that
proves it.
