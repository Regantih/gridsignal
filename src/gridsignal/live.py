"""Fetch the latest complete ERCOT operating day live from ercot.com.

The Control Room normally prices incidents with a bundled Parquet trace so the demo is
repeatable. This module pulls the same kind of data live: ERCOT's public real-time
settlement point price display (15-minute intervals) and the day-ahead hourly curve for the
same operating day. No key, no account, standard library HTML parsing only.

    python -m gridsignal.live            # yesterday, LZ_HOUSTON
    python -m gridsignal.live --date 2026-09-26 --zone LZ_NORTH

The default is the most recent *complete* operating day, because the engine prices the
day's evening peak and today's evening has not settled yet.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from gridsignal.prices import PriceTrace

CENTRAL = ZoneInfo("America/Chicago")
BASE_URL = "https://www.ercot.com/content/cdr/html"
RT_URL = BASE_URL + "/{ymd}_real_time_spp.html"
DAM_URL = BASE_URL + "/{ymd}_dam_spp.html"
SOURCE_PAGE = "https://www.ercot.com/content/cdr/html/real_time_spp.html"
DEFAULT_ZONE = "LZ_HOUSTON"
TIMEOUT_S = 8.0
_HEADERS = {"User-Agent": "Mozilla/5.0 (GridSignal Control Room; public ERCOT display)"}


class LiveDataError(RuntimeError):
    """ERCOT could not be reached, or the page did not hold a usable day of prices."""


class _TableParser(HTMLParser):
    """Collect every table row as a list of cell strings."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: str | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:  # noqa: ARG002
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = ""

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(self._cell.strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell += data


def parse_table(html: str) -> list[dict[str, str]]:
    """The first header row plus every data row, as dicts keyed by header."""
    parser = _TableParser()
    parser.feed(html)
    header_idx = next((i for i, r in enumerate(parser.rows) if "Oper Day" in r), None)
    if header_idx is None:
        raise LiveDataError("ERCOT page had no price table")
    header = parser.rows[header_idx]
    return [
        dict(zip(header, r, strict=False))
        for r in parser.rows[header_idx + 1 :]
        if len(r) == len(header)
    ]


def _day_start(oper_day: str) -> datetime:
    return datetime.strptime(oper_day, "%m/%d/%Y").replace(tzinfo=CENTRAL)


def parse_real_time(html: str, zone: str = DEFAULT_ZONE) -> pd.DataFrame:
    """15-minute real-time SPP rows to ``interval_start``/``interval_end``/``spp``."""
    rows = parse_table(html)
    out = []
    for r in rows:
        if zone not in r:
            raise LiveDataError(f"zone {zone} not on the ERCOT page")
        hhmm = r["Interval Ending"]
        end = _day_start(r["Oper Day"]) + timedelta(hours=int(hhmm[:2]), minutes=int(hhmm[2:]))
        out.append((end - timedelta(minutes=15), end, float(r[zone])))
    return _tidy(out)


def parse_day_ahead(html: str, zone: str = DEFAULT_ZONE) -> pd.DataFrame:
    """Hourly day-ahead SPP rows to ``interval_start``/``interval_end``/``spp``."""
    rows = parse_table(html)
    out = []
    for r in rows:
        if zone not in r:
            raise LiveDataError(f"zone {zone} not on the ERCOT page")
        end = _day_start(r["Oper Day"]) + timedelta(hours=int(r["Hour Ending"].split(":")[0]))
        out.append((end - timedelta(hours=1), end, float(r[zone])))
    return _tidy(out)


def _tidy(rows: list[tuple[datetime, datetime, float]]) -> pd.DataFrame:
    if not rows:
        raise LiveDataError("ERCOT page held no price rows")
    frame = pd.DataFrame(rows, columns=["interval_start", "interval_end", "spp"])
    frame["interval_start"] = pd.to_datetime(frame["interval_start"]).dt.tz_localize(None)
    frame["interval_end"] = pd.to_datetime(frame["interval_end"]).dt.tz_localize(None)
    return (
        frame.groupby(["interval_start", "interval_end"], as_index=False)["spp"]
        .mean()
        .round({"spp": 2})
        .sort_values("interval_start")
        .reset_index(drop=True)
    )


def latest_complete_day(now: datetime | None = None) -> str:
    """Yesterday in ERCOT's time zone, as ``YYYY-MM-DD``."""
    now = now or datetime.now(CENTRAL)
    return (now.astimezone(CENTRAL) - timedelta(days=1)).strftime("%Y-%m-%d")


def _get(url: str) -> str:
    try:
        resp = httpx.get(url, headers=_HEADERS, timeout=TIMEOUT_S, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise LiveDataError(f"could not reach ERCOT ({exc.__class__.__name__})") from exc
    return resp.text


def fetch_live_trace(date: str | None = None, zone: str = DEFAULT_ZONE) -> PriceTrace:
    """One full operating day of real ERCOT prices, fetched now from ercot.com."""
    date = date or latest_complete_day()
    ymd = date.replace("-", "")
    rt = parse_real_time(_get(RT_URL.format(ymd=ymd)), zone)
    if len(rt) < 90:
        raise LiveDataError(f"ERCOT has only {len(rt)} of 96 intervals for {date} so far")
    try:
        dam = parse_day_ahead(_get(DAM_URL.format(ymd=ymd)), zone)
    except LiveDataError:
        dam = None
    fetched = datetime.now(CENTRAL).strftime("%Y-%m-%d %H:%M %Z")
    return PriceTrace(
        location=zone,
        market="REAL_TIME_15_MIN",
        date=date,
        source=f"{RT_URL.format(ymd=ymd)} (fetched live {fetched})",
        frame=rt,
        dam=dam,
        live=True,
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Fetch a live day of ERCOT prices")
    p.add_argument("--date", help="operating day, YYYY-MM-DD (default: yesterday)")
    p.add_argument("--zone", default=DEFAULT_ZONE)
    p.add_argument(
        "--incident",
        action="store_true",
        help="also run the Control Room incident (10,000 simulated devices) on this live day",
    )
    args = p.parse_args()
    trace = fetch_live_trace(args.date, args.zone)
    start, end, price = trace.peak_window(2.0)
    print(f"ERCOT {trace.location} {trace.date}: {len(trace.frame)} real-time intervals")
    print(f"  peak ${trace.peak_mwh:,.2f}/MWh, mean ${trace.mean_mwh:,.2f}/MWh")
    print(f"  2-hour peak window {start:%H:%M}-{end:%H:%M} at ${price:,.2f}/MWh")
    if trace.dam is not None:
        print(f"  day-ahead peak ${float(trace.dam['spp'].max()):,.2f}/MWh")
    print(f"  source: {trace.source}")
    if args.incident:
        print(run_incident(trace))


def run_incident(trace: PriceTrace, fleet_size: int = 10_000) -> str:
    """The Control Room's gateway-ring incident, priced on this live day."""
    from gridsignal.control_room.engine import ControlRoomEngine
    from gridsignal.demo_numbers import FOCUS_DEVICE_ID

    eng = ControlRoomEngine(price_trace=trace, fleet_size=fleet_size)
    incident = eng.trigger_device_failure(FOCUS_DEVICE_ID)
    eng.approve_recovery()
    return (
        f"  incident on this day, {fleet_size:,} simulated devices: {len(incident.cohort)} out, "
        f"{incident.lost_kw:,.0f} kW lost, ${incident.dollars_at_risk:,.2f} at risk, "
        f"${incident.dollars_recovered:,.2f} recovered after one approval, "
        f"coverage back to {eng.snapshot().coverage_pct:.0f}%"
    )


if __name__ == "__main__":
    main()
