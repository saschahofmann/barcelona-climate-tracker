"""Fetch ERA5 2m temperature for Barcelona and write one JSON file per season.

Output lands in `data/era5/` as `<season_year>-<season>.json`, plus an
`index.json` manifest. The Astro build imports these directly, so the deployed
site stays static.
"""

import os
from pathlib import Path

import xarray as xr
from dotenv import load_dotenv
from xclim.core.units import convert_units_to
from xclim.indices import relative_humidity

from barcelona_climate_tracker.seasons import write_manifest, write_season_files

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

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "era5"


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


def main() -> None:
    if not ECMWF_API_KEY:
        raise SystemExit(
            "ECMWF_API_KEY is not set — put it in .env or the environment."
        )

    daily = load_daily(START_YEAR, END_YEAR)
    # Drop the scalar lat/lon coords so the frame is indexed by time alone.
    frame = daily.reset_coords(drop=True).to_dataframe()

    manifest = write_season_files(frame, OUTPUT_DIR)
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
