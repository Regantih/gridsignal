"""Cache ERCOT's published ancillary service plan (procured MW) for bundled trade dates.

The clearing prices say what capacity is worth; the AS plan says how much of it ERCOT
buys. Without the second number a simulated fleet can quietly "sell" more Reg Down than
the whole market procures, so `gridsignal.ancillary` flags its offer against this file.

ERCOT's MIS keeps the AS plan for roughly the last month, so only the recent bundled
trade dates can be cached; older ones are simply absent and the flag says so.

    python scripts/fetch_as_plan.py       # needs the [ercot] extra and network
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pandas as pd

from gridsignal.ancillary import AS_PLAN_DIR, as_plan_path
from gridsignal.holdout import HOLDOUT_DIR, TUNING_DIR, rtm_paths
from gridsignal.ingest import AS_PRODUCTS
from gridsignal.prices import PROCESSED, load_price_trace

SOURCE_URL = "https://www.ercot.com/mp/data-products/data-product-details?id=NP3-905-ER"
COLUMNS = {"REGUP": "regup", "REGDN": "regdn", "RRS": "rrs", "ECRS": "ecrs", "NSPIN": "nonspin"}


def fetch(date: str) -> pd.DataFrame:
    import gridstatus

    raw = gridstatus.Ercot().get_as_plan(date)
    frame = raw.rename(columns={"Interval Start": "interval_start", **COLUMNS})
    frame["interval_start"] = pd.to_datetime(frame["interval_start"]).dt.tz_localize(None)
    # The plan is published a week at a time; keep the trade date it was fetched for.
    day = frame[frame["interval_start"].dt.date.astype(str) == date]
    return day[["interval_start", *AS_PRODUCTS]].reset_index(drop=True)


def main() -> None:
    AS_PLAN_DIR.mkdir(parents=True, exist_ok=True)
    for directory in (PROCESSED, TUNING_DIR, HOLDOUT_DIR):
        for path in rtm_paths(directory):
            date = load_price_trace(path).date
            try:
                day = fetch(date)
            except Exception as exc:  # noqa: BLE001 - the archive simply does not go back
                print(f"{date}: no published AS plan available ({type(exc).__name__}), skipped")
                continue
            out = as_plan_path(date)
            day.to_parquet(out, index=False)
            out.with_suffix(".json").write_text(
                json.dumps(
                    {
                        "location": "ERCOT system-wide",
                        "market": "DAM_ANCILLARY_PLAN",
                        "date": date,
                        "source": SOURCE_URL,
                        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                        "intervals": int(len(day)),
                        "products": list(AS_PRODUCTS),
                        "units": "MW of capacity procured per hour",
                    },
                    indent=2,
                )
                + "\n"
            )
            print(f"{date}: {len(day)} hours, Reg Down {day['regdn'].mean():,.0f} MW avg -> {out}")


if __name__ == "__main__":
    main()
