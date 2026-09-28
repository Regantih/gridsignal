"""Turn engine dataclasses into JSON without touching a single value.

Enums become their ``value``, datetimes ISO strings, numpy scalars Python numbers. Floats
are passed through exactly as the engine rounded them; formatting is the front end's job.
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np


def to_json(value: Any) -> Any:
    """Recursively convert ``value`` into JSON-serialisable Python objects."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_json(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return [to_json(v) for v in value.tolist()]
    if isinstance(value, dict):
        return {str(k): to_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_json(v) for v in value]
    if isinstance(value, float) and (value != value or value in (float("inf"), -float("inf"))):
        return None
    return value
