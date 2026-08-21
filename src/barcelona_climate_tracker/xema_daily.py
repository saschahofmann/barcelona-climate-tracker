"""Download Meteocat XEMA daily station values and write season files.

Runs over every configured station unless one is named:

    python -m barcelona_climate_tracker.xema_daily                 # incremental
    python -m barcelona_climate_tracker.xema_daily --full          # bulk
    python -m barcelona_climate_tracker.xema_daily --station D5    # just Fabra

Source is `7bvh-jvq2`, Meteocat's **daily** XEMA statistics on the Catalonia
open-data portal. No API key.

This replaced an earlier pipeline that pulled the half-hourly feed (`nzvn-apee`)
and aggregated it here. That was worse in every respect:

  * the half-hourly feed only starts in 2009, while the daily one goes back to
    each station's own beginning — 1995 for Fabra, 2006 for el Raval;
  * the half-hourly extract has holes the station itself never had. Fabra on
    2026-08-14 has no extract rows between 00:00 and 05:00, yet Meteocat's daily
    record knows the minimum was 26.9 °C at 03:16. Whole days missing from the
    extract (2025-03-08, 03-09, 04-02) are present and complete here;
  * aggregating the extract meant inventing coverage thresholds and gap rules to
    guess which days were trustworthy. Meteocat already publishes that judgement
    as `estat`, so all of that guesswork is gone;
  * `1000` is the true daily mean over the full record, not the (TX+TN)/2
    approximation — Meteocat publishes that separately as `1003`.
"""

import argparse
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

from barcelona_climate_tracker.seasons import (
    SERIES_DIGITS,
    load_existing,
    merge,
    write_manifest,
    write_season_files,
)

load_dotenv()

# Coordinates and altitudes from the XEMA station metadata (yqwd-vj5e).
# `start` is the first day this dataset actually carries for the station.
STATIONS = {
    "D5": {
        "name": "Barcelona - Observatori Fabra",
        "latitude": 41.41864,
        "longitude": 2.12379,
        "altitude": 410,
        "start": date(1995, 11, 4),
    },
    "X4": {
        "name": "Barcelona - el Raval",
        "latitude": 41.3839,
        "longitude": 2.1679,
        "altitude": 33,
        "start": date(2006, 10, 11),
    },
}

DATASET = "https://analisi.transparenciacatalunya.cat/resource/7bvh-jvq2.json"

# Re-fetch this many days before the newest stored day: recent days are revised,
# and days absent one morning appear later.
REFETCH_DAYS = 10

# Daily variable codes → the series names the site uses. `1300` is the
# midnight-to-midnight rain total; `1301` is the 08–08 h version, which would
# shift rain onto the wrong day here.
VARIABLES = {
    "1000": "tasmean",
    "1001": "tasmax",
    "1002": "tasmin",
    "1100": "hursmean",
    "1101": "hursmax",
    "1102": "hursmin",
    "1300": "prsum",
}

# Meteocat's own verdict on whether a daily value represents the day. Roughly
# 64 values in 225k are flagged otherwise; a blank has simply not been assessed.
REJECTED_ESTAT = {"No representatiu"}

OUTPUT_ROOT = Path(__file__).resolve().parents[2] / "data" / "xema"


def output_dir(code: str) -> Path:
    """One directory per station, so their season files never collide."""
    return OUTPUT_ROOT / code


def year_spans(start: date, end: date):
    """Calendar-year chunks; a year of one station is a few thousand rows."""
    for year in range(start.year, end.year + 1):
        yield max(start, date(year, 1, 1)), min(end, date(year, 12, 31))


def fetch_span(
    session: requests.Session, code: str, start: date, end: date
) -> pd.DataFrame:
    codes = ",".join(f"'{v}'" for v in VARIABLES)
    response = session.get(
        DATASET,
        params={
            "codi_estacio": code,
            "$where": (
                f"codi_variable in ({codes}) AND data_lectura between "
                f"'{start:%Y-%m-%d}T00:00:00' and '{end:%Y-%m-%d}T23:59:59'"
            ),
            "$select": "data_lectura,codi_variable,valor,estat",
            "$limit": 50_000,
        },
        timeout=180,
    )
    response.raise_for_status()
    rows = response.json()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def to_daily(readings: pd.DataFrame) -> pd.DataFrame:
    """Pivot one row per (day, variable) into one row per day."""
    if readings.empty:
        return pd.DataFrame()

    frame = readings[~readings["estat"].isin(REJECTED_ESTAT)].copy()
    frame["series"] = frame["codi_variable"].map(VARIABLES)
    frame["value"] = pd.to_numeric(frame["valor"], errors="coerce")
    frame["day"] = pd.to_datetime(frame["data_lectura"]).dt.normalize()
    frame = frame.dropna(subset=["value", "series"])

    daily = frame.pivot_table(
        index="day", columns="series", values="value", aggfunc="last"
    )
    daily = daily.dropna(how="all")
    return daily[[name for name in SERIES_DIGITS if name in daily.columns]]


def download(code: str, start: date, end: date) -> pd.DataFrame:
    session = requests.Session()
    chunks = []
    for span_start, span_end in year_spans(start, end):
        frame = fetch_span(session, code, span_start, span_end)
        print(f"  {span_start:%Y} … {len(frame):>6} values", flush=True)
        if not frame.empty:
            chunks.append(frame)

    if not chunks:
        return pd.DataFrame()
    return to_daily(pd.concat(chunks, ignore_index=True))


def run_station(code: str, args) -> None:
    station = STATIONS[code]
    directory = output_dir(code)
    print(f"\n{code} — {station['name']}")

    end = date.fromisoformat(args.end) if args.end else datetime.now(tz=UTC).date()
    existing = pd.DataFrame() if args.full else load_existing(directory)

    if args.start:
        start = date.fromisoformat(args.start)
    elif args.full or existing.empty:
        start = station["start"]
        print(f"Bulk download from {start:%Y-%m-%d}.")
    else:
        newest = existing.index.max().date()
        start = newest - timedelta(days=REFETCH_DAYS)
        print(
            f"Newest stored day is {newest:%Y-%m-%d}; re-fetching from {start:%Y-%m-%d}."
        )
        if start > end:
            print("Already up to date.")
            return

    fresh = download(code, start, end)
    if fresh.empty and existing.empty:
        raise SystemExit(f"{code}: no data returned — nothing to write.")

    combined = merge(fresh, existing)
    added = 0 if existing.empty else len(combined.index.difference(existing.index))
    print(f"{len(combined)} days total ({added} new, {len(fresh)} fetched).")

    manifest = write_season_files(combined, directory)
    write_manifest(
        manifest,
        directory,
        location={
            "name": station["name"],
            "station": code,
            "latitude": station["latitude"],
            "longitude": station["longitude"],
            "altitude": station["altitude"],
        },
        source=f"Meteocat XEMA {code} daily values via Dades Obertes de Catalunya",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch XEMA daily station data.")
    parser.add_argument(
        "--station",
        choices=sorted(STATIONS),
        help="Only fetch this station. Defaults to all of them.",
    )
    parser.add_argument("--full", action="store_true", help="Re-download everything.")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD).")
    parser.add_argument("--end", help="End date (YYYY-MM-DD). Defaults to today.")
    args = parser.parse_args()

    for code in [args.station] if args.station else sorted(STATIONS):
        run_station(code, args)


if __name__ == "__main__":
    sys.exit(main())
