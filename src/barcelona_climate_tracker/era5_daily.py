"""Fetch ERA5 2m temperature for Barcelona and write one JSON file per season.

Output lands in `data/era5/` as `<season_year>-<season>.json`, plus an
`index.json` manifest. The Astro build imports these directly, so the deployed
site stays static.
"""

import calendar
import json
import math
import os
from pathlib import Path

import xarray as xr
from dotenv import load_dotenv
from xclim.core.units import convert_units_to
from xclim.indices import relative_humidity

load_dotenv()
ECMWF_API_KEY = os.getenv("ECMWF_API_KEY")
BARCELONA_LAT = 41.388
BARCELONA_LON = 2.158

START_YEAR = 2000
END_YEAR = 2026

ERA5_URL = (
    "https://arco.datastores.ecmwf.int/cadl-arco-geo-002/arco/"
    "reanalysis_era5_single_levels/sfc/geoChunked.zarr"
)

# Meteorological seasons. December belongs to the *following* year's winter,
# so DJF 2026 means Dec 2025 + Jan/Feb 2026 — the convention xarray's
# `time.dt.season` labels but does not year-shift for you.
SEASON_MONTHS = {
    "DJF": (12, 1, 2),
    "MAM": (3, 4, 5),
    "JJA": (6, 7, 8),
    "SON": (9, 10, 11),
}

MONTH_TO_SEASON = {
    month: season for season, months in SEASON_MONTHS.items() for month in months
}

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "era5"

# Decimal places per series. Humidity is whole percent — the extra digit would
# be false precision and it inflates the payload the page inlines.
SERIES_DIGITS = {
    "tasmin": 1,
    "tasmean": 1,
    "tasmax": 1,
    "hursmin": 0,
    "hursmean": 0,
    "hursmax": 0,
    "prsum": 1,
}

UNITS = {"tas": "degC", "hurs": "%", "pr": "mm"}


def load_daily(start_year: int, end_year: int) -> xr.Dataset:
    """Daily temperature, relative humidity and precipitation at Barcelona."""
    storage_options = {"headers": {"Authorization": f"Bearer {ECMWF_API_KEY}"}}
    era5 = xr.open_zarr(ERA5_URL, storage_options=storage_options)

    # ERA5 timestamps are UTC. Barcelona is UTC+1/+2, so a "day" here is offset
    # by an hour or two against local midnight — immaterial for the means, worth
    # knowing for the extremes and for where a rainy night lands.
    point = era5.sel(time=slice(str(start_year), str(end_year))).sel(
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


def expected_days(season_year: int, season: str) -> int:
    """How many days a complete run of this season holds (leap-aware)."""
    total = 0
    for month in SEASON_MONTHS[season]:
        # DJF's December is the tail of the previous calendar year.
        year = season_year - 1 if season == "DJF" and month == 12 else season_year
        total += calendar.monthrange(year, month)[1]
    return total


def to_series(values, digits: int) -> list[float | int | None]:
    """Round down to chart precision — ERA5 float64 detail is noise here."""
    return [
        None
        if math.isnan(v)
        else (round(float(v)) if digits == 0 else round(float(v), digits))
        for v in values
    ]


def write_season_files(daily: xr.Dataset, output_dir: Path) -> list[dict]:
    """One JSON file per (season year, season). Returns the manifest entries."""
    # Drop the scalar lat/lon coords so the frame is indexed by time alone.
    frame = daily.reset_coords(drop=True).to_dataframe().sort_index()
    series_names = [name for name in SERIES_DIGITS if name in frame.columns]

    months = frame.index.month
    frame["season"] = [MONTH_TO_SEASON[month] for month in months]
    frame["season_year"] = frame.index.year + (months == 12)

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = []

    for (season_year, season), group in frame.groupby(["season_year", "season"]):
        season_year = int(season_year)
        days = len(group)
        # A season still in progress, or clipped by the requested range, is
        # short. Flag it so the frontend can draw it as partial.
        complete = days == expected_days(season_year, season)

        payload = {
            "season_year": season_year,
            "season": season,
            # Array index is days since the season started, which keeps the
            # same calendar day at the same index across years. Feb 29 lands
            # last in DJF, so it never shifts anything.
            "start_date": group.index[0].strftime("%Y-%m-%d"),
            "days": days,
            "complete": complete,
            "units": UNITS,
            "time": [ts.strftime("%Y-%m-%d") for ts in group.index],
            **{
                name: to_series(group[name], SERIES_DIGITS[name])
                for name in series_names
            },
        }

        filename = f"{season_year}-{season}.json"
        path = output_dir / filename
        # indent=2 puts one value per line: a changed day is a one-line diff,
        # which matters when git is the datastore.
        path.write_text(f"{json.dumps(payload, indent=2)}\n")

        manifest.append(
            {
                "file": filename,
                "season_year": season_year,
                "season": season,
                "start_date": payload["start_date"],
                "days": days,
                "complete": complete,
            }
        )
        print(
            f"Wrote {filename} ({days} days, {'complete' if complete else 'partial'})"
        )

    return manifest


def write_manifest(manifest: list[dict], output_dir: Path) -> None:
    index = {
        "location": {
            "name": "Barcelona",
            "latitude": BARCELONA_LAT,
            "longitude": BARCELONA_LON,
        },
        "source": "ERA5 reanalysis (ECMWF ARCO)",
        "variables": list(SERIES_DIGITS),
        "units": UNITS,
        # Chronological, not alphabetical — DJF, MAM, JJA, SON.
        "seasons": sorted(
            manifest,
            key=lambda entry: (
                entry["season_year"],
                list(SEASON_MONTHS).index(entry["season"]),
            ),
        ),
    }
    path = output_dir / "index.json"
    path.write_text(f"{json.dumps(index, indent=2)}\n")
    print(f"Wrote index.json ({len(manifest)} seasons)")


def main() -> None:
    if not ECMWF_API_KEY:
        raise SystemExit(
            "ECMWF_API_KEY is not set — put it in .env or the environment."
        )

    daily = load_daily(START_YEAR, END_YEAR)
    manifest = write_season_files(daily, OUTPUT_DIR)
    write_manifest(manifest, OUTPUT_DIR)


if __name__ == "__main__":
    main()
