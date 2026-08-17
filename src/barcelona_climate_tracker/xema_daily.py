"""Download Meteocat XEMA stations and write daily season files.

Runs two ways, over every configured station unless one is named:

    python -m barcelona_climate_tracker.xema_daily                 # incremental
    python -m barcelona_climate_tracker.xema_daily --full          # bulk, from 2009
    python -m barcelona_climate_tracker.xema_daily --station D5    # just Fabra

Incremental picks up from the newest day already stored, re-fetching a short
trailing window so the last partial day is corrected once it fills in.

Source is the Socrata open-data mirror of Meteocat's XEMA network, which needs
no API key. An app token lifts the anonymous rate limit; set SOCRATA_APP_TOKEN
if you have one.
"""

import argparse
import os
import sys
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
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
STATIONS = {
    "D5": {
        "name": "Barcelona - Observatori Fabra",
        "latitude": 41.41864,
        "longitude": 2.12379,
        "altitude": 410,
    },
    "X4": {
        "name": "Barcelona - el Raval",
        "latitude": 41.3839,
        "longitude": 2.1679,
        "altitude": 33,
    },
}

DATASET = "https://analisi.transparenciacatalunya.cat/resource/nzvn-apee.json"

# Both stations long predate this — X4's metadata claims 2006, and Fabra's
# series began on 6 August 1913 — but the open-data mirror only carries either
# of them from 2009. Starting earlier just fetches empty months.
#
# TODO: Fabra's pre-2009 record needs a different source. Meteocat's CADTEP
# climate series reaches back to 1950 but is published through the climatology
# pages rather than this REST API, and 1913–1950 sits with RACAB as digitised
# material rather than a download. CADTEP is homogenised, so its values will not
# splice cleanly onto the raw automatic readings here. See the README.
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

# Meteocat's validation states: `V` validated, `T` validation started but the
# result is still pending, blank not yet validated, `N` invalid.
#
# Only `N` means bad data — and the open-data mirror publishes none of it (a
# count over the whole dataset returns 0), so in practice nothing is dropped.
# The guard stays in case that changes.
#
# Rejecting `T` here was a mistake worth recording: it is *pending*, not
# suspect, and Meteocat applies it to entire days at a time. It silently erased
# complete 48/48 days such as Fabra's 2025-02-03 and 2025-12-11. Never filter
# *to* `V` either — that would discard every recent, not-yet-validated day.
REJECTED_STATES = {"N"}

# Readings per day implied by `codi_base`. X4 reported hourly until partway
# through 2014 and half-hourly since, so the expected count MUST be derived per
# day — a fixed 48 would treat every complete hourly day as half-missing and
# discard five years of otherwise perfect data.
BASE_SAMPLES = {"HO": 24, "SH": 48}
DEFAULT_SAMPLES = 48

# Fraction of the day's own expected count needed to publish an average or an
# extreme. This is the weaker of the two guards — MAX_GAP_HOURS below is what
# actually protects the extremes, by rejecting any day with a hole wide enough
# to hide a peak or a pre-dawn trough. With that in place a day can lose half
# its readings and still be sound, provided the survivors are spread across it.
#
# Precipitation is deliberately NOT gated. It is an accumulation of intervals
# that really happened, so a short day is a lower bound on observed rain rather
# than a biased estimate — and withholding it is strictly worse, because a
# cumulative total treats a missing day as zero either way. Dropping it would
# discard rain that was actually measured and understate the season by more.
MIN_COVERAGE = 0.50

# A sample count alone is not enough: a day can keep 77% of its readings and
# still be useless, if the missing quarter is the pre-dawn hours where the
# minimum happens. Barcelona-Fabra on 2026-08-14 did exactly that — the station
# came back at 05:30 with the temperature still falling, so the "minimum" was
# 4 °C above both neighbouring nights. Any hole longer than this may straddle
# the time an extreme is reached, so the day's averages and extremes are
# withheld regardless of how many readings survived elsewhere.
MAX_GAP_HOURS = 3.0

OUTPUT_ROOT = Path(__file__).resolve().parents[2] / "data" / "xema"


def output_dir(code: str) -> Path:
    """One directory per station, so their season files never collide."""
    return OUTPUT_ROOT / code


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


def fetch_window(
    session: requests.Session, code: str, start: date, end: date
) -> pd.DataFrame:
    """One month of sub-hourly readings for the station.

    Chunked by month on purpose: `$offset` paging needs an `$order`, and
    ordering by `data_lectura` is unindexed and times the query out.
    """
    response = session.get(
        DATASET,
        params={
            "codi_estacio": code,
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

    def widest_gap_hours(times: pd.Series) -> float:
        """Longest stretch of the day with no reading, midnight to midnight."""
        ordered = times.sort_values()
        midnight = ordered.iloc[0].normalize()
        edges = [midnight, *ordered, midnight + pd.Timedelta(days=1)]
        return max((b - a).total_seconds() for a, b in pairwise(edges)) / 3600.0

    values["gap"] = grouped["timestamp"].agg(widest_gap_hours)

    def series(name: str, stat: str, *, gated: bool = True) -> pd.Series:
        if name not in values.index.get_level_values("name"):
            return pd.Series(dtype="float64")
        chunk = values.xs(name, level="name")
        if not gated:
            return chunk[stat]
        # A day short of its own cadence, or with a hole wide enough to hide an
        # extreme, would bias the result — blank it rather than publish it.
        usable = (chunk["count"] >= MIN_COVERAGE * chunk["expected"]) & (
            chunk["gap"] <= MAX_GAP_HOURS
        )
        return chunk[stat].where(usable)

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
        short = chunk[
            (chunk["count"] < MIN_COVERAGE * chunk["expected"])
            | (chunk["gap"] > MAX_GAP_HOURS)
        ]
        if len(short):
            worst = short.iloc[short["count"].argmin()]
            print(
                f"  note: {len(short)} day(s) with partial rain coverage "
                f"(worst {int(worst['count'])}/{int(worst['expected'])}) — "
                "totals are lower bounds"
            )

    return daily[[name for name in SERIES_DIGITS if name in daily.columns]]


def download(code: str, start: date, end: date) -> pd.DataFrame:
    session = requests.Session()
    token = os.getenv("SOCRATA_APP_TOKEN")
    if token:
        session.headers["X-App-Token"] = token

    chunks = []
    for window_start, window_end in month_starts(start, end):
        frame = fetch_window(session, code, window_start, window_end)
        got = 0 if frame.empty else len(frame)
        print(f"  {window_start:%Y-%m} … {got:>6} readings", flush=True)
        if got:
            chunks.append(frame)

    if not chunks:
        return pd.DataFrame()
    return to_daily(pd.concat(chunks, ignore_index=True))


def run_station(code: str, args) -> None:
    station = STATIONS[code]
    directory = output_dir(code)
    print(f"\n{code} — {station['name']}")

    # Readings are timestamped in local Catalan time, but UTC "today" is close
    # enough as an upper bound — the API simply returns nothing beyond the end.
    end = date.fromisoformat(args.end) if args.end else datetime.now(tz=UTC).date()
    existing = pd.DataFrame() if args.full else load_existing(directory)

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
        source=f"Meteocat XEMA {code} via Dades Obertes de Catalunya",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch XEMA station daily data.")
    parser.add_argument(
        "--station",
        choices=sorted(STATIONS),
        help="Only fetch this station. Defaults to all of them.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help=f"Re-download the whole record from {RECORD_START:%Y-%m-%d}.",
    )
    parser.add_argument("--start", help="Start date (YYYY-MM-DD); implies a full pass.")
    parser.add_argument("--end", help="End date (YYYY-MM-DD). Defaults to today.")
    args = parser.parse_args()

    for code in [args.station] if args.station else sorted(STATIONS):
        run_station(code, args)


if __name__ == "__main__":
    sys.exit(main())
