"""Season splitting and JSON output, shared by the ERA5 and XEMA fetchers.

Both sources land in the same on-disk shape, so the frontend can read either
and they can be compared day for day.
"""

import calendar
import json
import math
from pathlib import Path

import pandas as pd

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


def expected_days(season_year: int, season: str) -> int:
    """How many days a complete run of this season holds (leap-aware)."""
    total = 0
    for month in SEASON_MONTHS[season]:
        # DJF's December is the tail of the previous calendar year.
        year = season_year - 1 if season == "DJF" and month == 12 else season_year
        total += calendar.monthrange(year, month)[1]
    return total


def to_series(values, digits: int) -> list[float | int | None]:
    """Round down to chart precision — source float64 detail is noise here."""
    return [
        None
        if value is None or (isinstance(value, float) and math.isnan(value))
        else (round(float(value)) if digits == 0 else round(float(value), digits))
        for value in values
    ]


def season_payload(group: pd.DataFrame, season_year: int, season: str) -> dict:
    """The JSON body for one season, from a date-indexed frame of that season."""
    days = len(group)
    series_names = [name for name in SERIES_DIGITS if name in group.columns]

    return {
        "season_year": season_year,
        "season": season,
        # Array index is days since the season started, which keeps the same
        # calendar day at the same index across years. Feb 29 lands last in
        # DJF, so it never shifts anything.
        "start_date": group.index[0].strftime("%Y-%m-%d"),
        "days": days,
        # A season still in progress, or clipped by the requested range, is
        # short. Flag it so the frontend can draw it as partial.
        "complete": days == expected_days(season_year, season),
        "units": UNITS,
        "time": [ts.strftime("%Y-%m-%d") for ts in group.index],
        **{name: to_series(group[name], SERIES_DIGITS[name]) for name in series_names},
    }


def split_by_season(frame: pd.DataFrame) -> pd.DataFrame:
    """Tag a date-indexed frame with its season and year-shifted season year."""
    frame = frame.sort_index()
    months = frame.index.month
    frame["season"] = [MONTH_TO_SEASON[month] for month in months]
    frame["season_year"] = frame.index.year + (months == 12)
    return frame


def write_season_files(frame: pd.DataFrame, output_dir: Path) -> list[dict]:
    """One JSON file per (season year, season). Returns the manifest entries.

    Only writes a file whose content actually changed, so a routine incremental
    run touches the current season and leaves closed history alone.
    """
    frame = split_by_season(frame)
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = []
    written = 0

    for (season_year, season), group in frame.groupby(["season_year", "season"]):
        season_year = int(season_year)
        payload = season_payload(
            group.drop(columns=["season", "season_year"]), season_year, season
        )

        filename = f"{season_year}-{season}.json"
        path = output_dir / filename
        # indent=2 puts one value per line: a changed day is a one-line diff,
        # which matters when git is the datastore.
        body = f"{json.dumps(payload, indent=2)}\n"

        if not path.exists() or path.read_text() != body:
            path.write_text(body)
            written += 1

        manifest.append(
            {
                "file": filename,
                "season_year": season_year,
                "season": season,
                "start_date": payload["start_date"],
                "days": payload["days"],
                "complete": payload["complete"],
            }
        )

    print(f"{len(manifest)} seasons, {written} file(s) changed")
    return manifest


def load_existing(output_dir: Path) -> pd.DataFrame:
    """Rebuild the stored daily frame from the season files already on disk.

    This is what makes the fetchers resumable: the state lives in the committed
    data, not in run metadata, so a run after a failed one just picks up where
    the files left off.
    """
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


def merge(fresh: pd.DataFrame, existing: pd.DataFrame) -> pd.DataFrame:
    """Freshly downloaded days win, so revised values replace stored ones."""
    if existing.empty:
        combined = fresh
    elif fresh.empty:
        combined = existing
    else:
        combined = fresh.combine_first(existing)
    combined = combined.sort_index()
    return combined[[name for name in SERIES_DIGITS if name in combined.columns]]


def write_manifest(
    manifest: list[dict], output_dir: Path, location: dict, source: str
) -> None:
    index = {
        "location": location,
        "source": source,
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
    (output_dir / "index.json").write_text(f"{json.dumps(index, indent=2)}\n")
    print(f"Wrote index.json ({len(manifest)} seasons)")
