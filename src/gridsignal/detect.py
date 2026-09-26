"""Detect price spikes, scarcity windows and zone spreads."""

import pandas as pd


def detect_spikes(prices: pd.DataFrame, z: float = 3.0) -> pd.DataFrame:
    """Flag intervals where price is far above its rolling baseline. TODO."""
    raise NotImplementedError
