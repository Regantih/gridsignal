"""Short-horizon forecast of spike probability by zone."""

from __future__ import annotations

import numpy as np
import pandas as pd

FEATURE_COLUMNS = ("z", "ramp", "level")

# Hand-tuned logistic coefficients (no training data ships with the repo, and the demo
# must stay deterministic and dependency-free). They encode three grid intuitions:
# a price already stretched above its baseline, a steep ramp, and a high absolute level
# each make the *next* interval more likely to settle as a spike.
INTERCEPT = -4.2
WEIGHTS = {"z": 1.15, "ramp": 0.9, "level": 0.8}


def build_features(detections: pd.DataFrame) -> pd.DataFrame:
    """Features describing how stretched the market is right now.

    - ``z``: robust deviation above the rolling baseline (from :mod:`gridsignal.detect`)
    - ``ramp``: change in ``z`` since the previous interval
    - ``level``: ``log1p(price / baseline)``, so absolute scarcity still registers when
      the baseline has already caught up with the spike
    """
    missing = {"spp", "baseline", "z"} - set(detections.columns)
    if missing:
        raise KeyError(f"run detect.detect_spikes first; missing columns: {sorted(missing)}")

    frame = detections.reset_index(drop=True)
    baseline = frame["baseline"].replace(0.0, np.nan)
    return pd.DataFrame(
        {
            "z": frame["z"].astype(float),
            "ramp": frame["z"].astype(float).diff().fillna(0.0),
            "level": np.log1p((frame["spp"] / baseline).fillna(1.0).clip(lower=0.0)),
        }
    )


def forecast_spike_probability(features: pd.DataFrame) -> pd.Series:
    """Probability that each interval settles as a spike, in ``[0, 1]``.

    A logistic score over :data:`FEATURE_COLUMNS`, shifted forward one interval: the
    value aligned with interval *i* is built from interval *i-1*, so it is a genuine
    forecast and never reads the price it is predicting.
    """
    missing = set(FEATURE_COLUMNS) - set(features.columns)
    if missing:
        raise KeyError(f"missing feature columns: {sorted(missing)}")

    score = INTERCEPT + sum(WEIGHTS[c] * features[c].astype(float) for c in FEATURE_COLUMNS)
    probability = 1.0 / (1.0 + np.exp(-score.clip(-30, 30)))
    return probability.shift(1).fillna(0.0).round(4).rename("spike_prob")
