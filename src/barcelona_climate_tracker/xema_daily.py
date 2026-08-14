"""Download Barcelona - el Raval (XEMA station X4) and write daily season files.

Runs two ways:

    python -m barcelona_climate_tracker.xema_daily            # incremental
    python -m barcelona_climate_tracker.xema_daily --full     # bulk, from 2009

Incremental picks up from the newest day already stored, re-fetching a short
trailing window so the last partial day is corrected once it fills in.

Source is the Socrata open-data mirror of Meteocat's XEMA network, which needs
no API key. An app token lifts the anonymous rate limit; set SOCRATA_APP_TOKEN
if you have one.
"""

import argparse
import json
import os
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

from barcelona_climate_tracker.seasons import (
    SERIES_DIGITS,
    write_manifest,
    write_season_files,
)

load_dotenv()

STATION = "X4"
STATION_NAME = "Barcelona - el Raval"
# From the XEMA station metadata (yqwd-vj5e).
STATION_LAT = 41.3839
STATION_LON = 2.1679
STATION_ALTITUDE = 33

DATASET = "https://analisi.transparenciacatalunya.cat/resource/nzvn-apee.json"

# The station metadata claims 2006, but the open-data mirror only carries X4
# from 2009 — starting earlier just fetches empty months.
RECORD_START = date(2009, 1, 1)

# Re-fetch this many days before the newest stored day. The most recent day is
# usually partial when first seen, and recent rows are unvalidated and may be
# revised.
REFETCH_DAYS = 7

# Variable codes from the XEMA variable metadata (4fb2-n3yi).
VARIABLES = {
    "32": "t",  # Temperature
    "40": "tmax",  # Half-hourly maximum temperature
    "42": "tmin",  # Half-hourly minimum temperature
    "33": "hr",  # Relative humidity
    "3": "hrmax",  # Half-hourly maximum relative humidity
    "44": "hrmin",  # Half-hourly minimum relative humidity
    "35": "ppt",  # Precipitation
}

# `V` is validated and `None` is merely not-yet-validated (recent data, which we
# want). `T` is the small suspect category — around 0.3% of rows — and is the
# only state worth dropping. Filtering *to* `V` would discard every recent day.
REJECTED_STATES = {"T"}

# Readings per day implied by `codi_base`. X4 reported hourly until partway
# through 2014 and half-hourly since, so the expected count MUST be derived per
# day — a fixed 48 would treat every complete hourly day as half-missing and
# discard five years of otherwise perfect data.
BASE_SAMPLES = {"HO": 24, "SH": 48}
DEFAULT_SAMPLES = 48

# Fraction of the day's own expected count needed to publish an average or an
# extreme. A mean over a handful of night-time samples is a genuinely biased
# estimate of the day, so it is better withheld.
#
# Precipitation is deliberately NOT gated. It is an accumulation of intervals
# that really happened, so a short day is a lower bound on observed rain rather
# than a biased estimate — and withholding it is strictly worse, because a
# cumulative total treats a missing day as zero either way. Dropping it would
# discard rain that was actually measured and understate the season by more.
MIN_COVERAGE = 0.75

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "xema"


def month_starts(start: date, end: date):
    """Inclusive month boundaries covering [start, end]."""
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        if cursor.month == 12:
            nxt = date(cursor.year + 1, 1, 1)
        else:
            nxt = date(cursor.year, cursor.month + 1, 1)
        yield max(cursor, start), min(nxt - timedelta(days=1), end)
        cursor = nxt


def fetch_window(session: requests.Session, start: date, end: date) -> pd.DataFrame:
    """One month of half-hourly readings for the station.

    Chunked by month on purpose: `$offset` paging needs an `$order`, and
    ordering by `data_lectura` is unindexed and times the query out.
    """
    response = session.get(
        DATASET,
        params={
            "codi_estacio": STATION,
            "$where": (
                f'data_lectura between "{start:%Y-%m-%d}T00:00:00" '
                f'and "{end:%Y-%m-%d}T23:59:59"'
            ),
            "$select": (
                "data_lectura,codi_variable,valor_lectura,codi_estat,codi_base"
            ),
            "$limit": 200_000,
        },
        timeout=180,
    )
    response.raise_for_status()
    rows = response.json()
    if not rows:
        return pd.DataFrame()

    frame = pd.DataFrame(rows)
    frame = frame[frame["codi_variable"].isin(VARIABLES)]
    if "codi_estat" in frame.columns:
        frame = frame[~frame["codi_estat"].isin(REJECTED_STATES)]

    frame["value"] = pd.to_numeric(frame["valor_lectura"], errors="coerce")
    frame["timestamp"] = pd.to_datetime(frame["data_lectura"])
    return frame.dropna(subset=["value"])


def to_daily(readings: pd.DataFrame) -> pd.DataFrame:
    """Collapse half-hourly readings into the daily series the site expects."""
    if readings.empty:
        return pd.DataFrame()

    readings = readings.assign(
        name=readings["codi_variable"].map(VARIABLES),
        day=readings["timestamp"].dt.normalize(),
    )
    grouped = readings.groupby(["day", "name"])
    values = grouped["value"].agg(["mean", "min", "max", "sum", "count"])

    # Expected readings come from the day's own cadence, not a fixed constant.
    modal_base = grouped["codi_base"].agg(
        lambda column: column.value_counts().idxmax() if len(column.dropna()) else None
    )
    values["expected"] = modal_base.map(BASE_SAMPLES).fillna(DEFAULT_SAMPLES)

    def series(name: str, stat: str, *, gated: bool = True) -> pd.Series:
        if name not in values.index.get_level_values("name"):
            return pd.Series(dtype="float64")
        chunk = values.xs(name, level="name")
        if not gated:
            return chunk[stat]
        # A day short of its own cadence would bias an average or clip an
        # extreme, so blank it rather than publish it.
        return chunk[stat].where(chunk["count"] >= MIN_COVERAGE * chunk["expected"])

    daily = pd.DataFrame(
        {
            "tasmean": series("t", "mean"),
            # True half-hourly extremes, not the min/max of spot readings.
            "tasmax": series("tmax", "max"),
            "tasmin": series("tmin", "min"),
            "hursmean": series("hr", "mean"),
            "hursmax": series("hrmax", "max"),
            "hursmin": series("hrmin", "min"),
            # Ungated on purpose — see MIN_SAMPLES.
            "prsum": series("ppt", "sum", gated=False),
        }
    )

    # Drop a day only when nothing at all survived. Keying this on temperature
    # would throw away good rain data whenever the temperature sensor alone
    # dropped out.
    daily = daily.dropna(how="all")

    # Partial rain days are lower bounds, so say how many there are instead of
    # letting the understatement pass silently.
    if "ppt" in values.index.get_level_values("name"):
        chunk = values.xs("ppt", level="name")
        short = chunk[chunk["count"] < MIN_COVERAGE * chunk["expected"]]
        if len(short):
            worst = short.iloc[short["count"].argmin()]
            print(
                f"  note: {len(short)} day(s) with partial rain coverage "
                f"(worst {int(worst['count'])}/{int(worst['expected'])}) — "
                "totals are lower bounds"
            )

    return daily[[name for name in SERIES_DIGITS if name in daily.columns]]


def load_existing(output_dir: Path) -> pd.DataFrame:
    """Rebuild the stored daily frame from the season files already on disk."""
    rows = {}
    for path in sorted(output_dir.glob("[0-9]*.json")):
        payload = json.loads(path.read_text())
        for i, iso in enumerate(payload["time"]):
            rows[iso] = {
                name: payload[name][i] for name in SERIES_DIGITS if name in payload
            }

    if not rows:
        return pd.DataFrame()

    frame = pd.DataFrame.from_dict(rows, orient="index")
    frame.index = pd.to_datetime(frame.index)
    return frame.sort_index()


def download(start: date, end: date) -> pd.DataFrame:
    session = requests.Session()
    token = os.getenv("SOCRATA_APP_TOKEN")
    if token:
        session.headers["X-App-Token"] = token

    chunks = []
    for window_start, window_end in month_starts(start, end):
        frame = fetch_window(session, window_start, window_end)
        got = 0 if frame.empty else len(frame)
        print(f"  {window_start:%Y-%m} … {got:>6} readings", flush=True)
        if got:
            chunks.append(frame)

    if not chunks:
        return pd.DataFrame()
    return to_daily(pd.concat(chunks, ignore_index=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=f"Fetch XEMA {STATION} daily data.")
    parser.add_argument(
        "--full",
        action="store_true",
        help=f"Re-download the whole record from {RECORD_START:%Y-%m-%d}.",
    )
    parser.add_argument("--start", help="Start date (YYYY-MM-DD); implies a full pass.")
    parser.add_argument("--end", help="End date (YYYY-MM-DD). Defaults to today.")
    args = parser.parse_args()

    # Readings are timestamped in local Catalan time, but UTC "today" is close
    # enough as an upper bound — the API simply returns nothing beyond the end.
    end = date.fromisoformat(args.end) if args.end else datetime.now(tz=UTC).date()
    existing = pd.DataFrame() if args.full else load_existing(OUTPUT_DIR)

    if args.start:
        start = date.fromisoformat(args.start)
    elif args.full or existing.empty:
        start = RECORD_START
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

    fresh = download(start, end)
    if fresh.empty and existing.empty:
        raise SystemExit("No data returned — nothing to write.")

    # Freshly downloaded days win, so revised values replace stored ones.
    combined = fresh if existing.empty else fresh.combine_first(existing)
    combined = combined.sort_index()
    combined = combined[[name for name in SERIES_DIGITS if name in combined.columns]]

    added = 0 if existing.empty else len(combined.index.difference(existing.index))
    print(f"{len(combined)} days total ({added} new, {len(fresh)} fetched).")

    manifest = write_season_files(combined, OUTPUT_DIR)
    write_manifest(
        manifest,
        OUTPUT_DIR,
        location={
            "name": STATION_NAME,
            "station": STATION,
            "latitude": STATION_LAT,
            "longitude": STATION_LON,
            "altitude": STATION_ALTITUDE,
        },
        source="Meteocat XEMA via Dades Obertes de Catalunya",
    )


if __name__ == "__main__":
    sys.exit(main())
