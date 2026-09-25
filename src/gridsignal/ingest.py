"""Pull ERCOT prices, load and generation and store them as Parquet."""
from pathlib import Path

import pandas as pd

RAW = Path(__file__).resolve().parents[2] / "data" / "raw"


def fetch_prices(start: str, end: str) -> pd.DataFrame:
    """Real-time settlement point prices. TODO: implement with gridstatus."""
    raise NotImplementedError


def fetch_load(start: str, end: str) -> pd.DataFrame:
    """System load. TODO."""
    raise NotImplementedError


def fetch_fuel_mix(start: str, end: str) -> pd.DataFrame:
    """Generation by fuel type. TODO."""
    raise NotImplementedError


def save(df: pd.DataFrame, name: str) -> Path:
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / f"{name}.parquet"
    df.to_parquet(path)
    return path
