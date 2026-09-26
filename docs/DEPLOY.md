# Deploy GridSignal to a free public URL

The app needs no secrets, no database and no network at run time: the ERCOT price traces, the
chaos scenarios and the recorded Jev answers are all committed. Anything a judge clicks runs
from the repository itself.

## Streamlit Community Cloud (two clicks after sign-in)

Prerequisite: a Streamlit Community Cloud account signed in with the GitHub account that can see
this repository. Community Cloud can deploy a **private** repository — you do not have to make
`Regantih/gridsignal` public; you only have to grant the Streamlit GitHub App access to it when
it asks.

1. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub. If the repo
   list does not show `Regantih/gridsignal`, click **Authorize / configure the Streamlit app on
   GitHub** and tick this repository.
2. **Create app → Deploy from GitHub** and fill in exactly:

   | Field | Value |
   |---|---|
   | Repository | `Regantih/gridsignal` |
   | Branch | `devin/1790382033-control-room` (or `main` once the PR is merged) |
   | Main file path | `app/dashboard.py` |
   | Python version (Advanced settings) | `3.11` |

3. Click **Deploy**. First build takes 2–4 minutes; dependencies come from
   [`requirements.txt`](../requirements.txt), which installs the repository itself so
   `gridsignal` is importable and the bundled data ships with it.

Then paste the resulting `https://<name>.streamlit.app` URL into the README badge line and
`docs/SUBMISSION.md`.

Optional: to run Jev live instead of replaying the recorded answers, add `AI_GATEWAY_API_KEY` or
`TYPESAFE_API_KEY` under **Settings → Secrets**. With neither key the app replays
`data/jev_fixtures/` and, failing that, shows "Jev offline, rules fallback" — that is the
supported judging path.

## What was verified for this deploy

Community Cloud runs `pip install -r requirements.txt` in a clean checkout, which is a
**non-editable** install: the package lands in `site-packages` while `data/` and `scenarios/`
stay in the working directory. That was reproduced here from a fresh clone and a fresh 3.11
virtualenv, and it found a real deployment bug — every module resolved its data directory two
levels above its own file, so the deployed app showed "No options to select" for the price day
and then raised `StopIteration`. [`src/gridsignal/paths.py`](../src/gridsignal/paths.py) now
resolves one project root by looking for the bundled inputs, from the module outwards and then
from the working directory upwards, and `tests/test_paths.py` guards it.

Re-verify at any time:

```bash
git clone https://github.com/Regantih/gridsignal.git && cd gridsignal
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt   # what the host does
.venv/bin/python -m streamlit run app/dashboard.py
```

## Fallbacks if no host is available

- `python scripts/no_crash_sweep.py` drives every screen and every CLI entry point locally and
  exits non-zero on any traceback.
- `python scripts/capture_demo.py` writes `docs/media/*.png` and `docs/media/demo.mp4`, the same
  walkthrough as a video.

## Other hosts

Any container host works with `streamlit run app/dashboard.py --server.port $PORT
--server.address 0.0.0.0` after `pip install .`. Nothing in the app opens an outbound
connection, so an egress-blocked host is fine.
