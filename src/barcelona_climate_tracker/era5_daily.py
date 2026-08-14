"""Fetch ERA5 for Barcelona and write one JSON file per season.

Runs two ways:

    python -m barcelona_climate_tracker.era5_daily            # incremental
    python -m barcelona_climate_tracker.era5_daily --full     # bulk, from 2000

Incremental picks up from the newest day already stored, re-fetching a short
trailing window. Output lands in `data/era5/` as `<season_year>-<season>.json`
plus an `index.json` manifest; the Astro build imports these directly, so the
deployed site stays static.
"""

import argparse
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import xarray as xr
from dotenv import load_dotenv
from xclim.core.units import convert_units_to
from xclim.indices import relative_humidity

from barcelona_climate_tracker.seasons import (
    load_existing,
    merge,
    write_manifest,
    write_season_files,
)

load_dotenv()
ECMWF_API_KEY = os.getenv("ECMWF_API_KEY")
BARCELONA_LAT = 41.388
BARCELONA_LON = 2.158

RECORD_START = date(2000, 1, 1)

# ERA5 lands roughly five days behind real time, and the preliminary ERA5T
# values for recent days are revised later, so always re-pull a trailing window
# rather than only the days that are strictly missing.
REFETCH_DAYS = 10

ERA5_URL = (
    "https://arco.datastores.ecmwf.int/cadl-arco-geo-002/arco/"
    "reanalysis_era5_single_levels/sfc/geoChunked.zarr"
)

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "era5"


def load_daily(start: date, end: date) -> xr.Dataset:
    """Daily temperature, relative humidity and precipitation at Barcelona."""
    storage_options = {"headers": {"Authorization": f"Bearer {ECMWF_API_KEY}"}}
    era5 = xr.open_zarr(ERA5_URL, storage_options=storage_options)

    # ERA5 timestamps are UTC. Barcelona is UTC+1/+2, so a "day" here is offset
    # by an hour or two against local midnight — immaterial for the means, worth
    # knowing for the extremes and for where a rainy night lands.
    point = era5.sel(time=slice(f"{start:%Y-%m-%d}", f"{end:%Y-%m-%d}")).sel(
        longitude=BARCELONA_LON, latitude=BARCELONA_LAT, method="nearest"
    )

    tas = convert_units_to(point.t2m, "degC")

    # ERA5 single levels carries no relative humidity, only 2m dewpoint, so
    # derive it from the temperature/dewpoint pair.
    hurs = relative_humidity(tas=point.t2m, tdps=point.d2m, method="sonntag90")

    # `tp` is metres accumulated over the preceding hour; summing the 24 hourly
    # values and scaling gives the daily total in mm.
    pr = point.tp * 1000.0

    # Min/max come from the 24 hourly samples, so they sit fractionally inside
    # the true daily extremes, which fall between samples.
    return xr.Dataset(
        {
            "tasmin": tas.resample(time="1D").min(),
            "tasmean": tas.resample(time="1D").mean(),
            "tasmax": tas.resample(time="1D").max(),
            "hursmin": hurs.resample(time="1D").min(),
            "hursmean": hurs.resample(time="1D").mean(),
            "hursmax": hurs.resample(time="1D").max(),
            "prsum": pr.resample(time="1D").sum(),
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch ERA5 daily data for Barcelona.")
    parser.add_argument(
        "--full",
        action="store_true",
        help=f"Re-download the whole record from {RECORD_START:%Y-%m-%d}.",
    )
    parser.add_argument("--start", help="Start date (YYYY-MM-DD).")
    parser.add_argument("--end", help="End date (YYYY-MM-DD). Defaults to today.")
    args = parser.parse_args()

    if not ECMWF_API_KEY:
        raise SystemExit(
            "ECMWF_API_KEY is not set — put it in .env or the environment."
        )

    end = date.fromisoformat(args.end) if args.end else datetime.now(tz=UTC).date()
    existing = load_existing(OUTPUT_DIR) if not args.full else None

    if args.start:
        start = date.fromisoformat(args.start)
    elif args.full or existing is None or existing.empty:
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

    daily = load_daily(start, end)
    # Drop the scalar lat/lon coords so the frame is indexed by time alone.
    fresh = daily.reset_coords(drop=True).to_dataframe()

    combined = merge(fresh, existing if existing is not None else fresh.iloc[:0])
    added = 0 if existing is None else len(combined.index.difference(existing.index))
    print(f"{len(combined)} days total ({added} new, {len(fresh)} fetched).")

    manifest = write_season_files(combined, OUTPUT_DIR)
    write_manifest(
        manifest,
        OUTPUT_DIR,
        location={
            "name": "Barcelona",
            "latitude": BARCELONA_LAT,
            "longitude": BARCELONA_LON,
        },
        source="ERA5 reanalysis (ECMWF ARCO)",
    )


if __name__ == "__main__":
    main()
