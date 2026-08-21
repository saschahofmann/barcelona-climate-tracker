"""Build Fabra's full record, 1913 to now, by splicing three sources.

    python -m barcelona_climate_tracker.fabra_historical                    # daily
    python -m barcelona_climate_tracker.fabra_historical --refresh-history  # re-pull

Meteocat's daily XEMA series starts when the station did, in November 1995.
Two other sources cover what came before:

    1913-08-06 → 1949-12-31   GHCN-Daily SPE00155259   TX, PPT   (no TN)
    1950-01-01 → 2025-12-31   Meteocat CADTEP          TX, TN, PPT
    1995-11-04 → now          XEMA D5 daily            everything, incl. mean

The last two overlap on purpose. The station is preferred wherever it has a
value — it is the raw observation and carries the true daily mean — but it is
missing TX or TN on roughly 660 days, where CADTEP's gap-filled series still
has one. Merging per field rather than per day keeps both properties: raw where
raw exists, no hole where it does not.

Output goes to `data/fabra/`. The raw automatic series in `data/xema/D5/` is left
untouched, so a pure-measurement view of Fabra stays available.

Both historical sources are static — GHCN's Fabra series ended in 2014 and will
not change, and CADTEP gains a year at a time — so a normal run reuses the
history already committed under `data/fabra/` and re-reads only XEMA. Pass
`--refresh-history` to pull them again, which is worth doing when CADTEP
publishes another year.

The splice is deliberate rather than assumed: both archives overlap the station
series and the offsets were measured before joining them — GHCN +0.13 °C on TX,
CADTEP +0.24 °C on TX and +0.30 °C on TN. All are small and in the same
direction (the manual and homogenised readings sit slightly above the automatic
station), and none are corrected here — raw values are kept and the offsets
documented instead.

Where CADTEP and the station overlap the station wins field by field, so a
CADTEP value only ever appears where the station published none. Those fills
carry CADTEP's small warm offset, which is the price of not having a gap.
"""

import argparse
import csv
import io
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from barcelona_climate_tracker.seasons import (
    SERIES_DIGITS,
    load_existing,
    write_manifest,
    write_season_files,
)

GHCN_URL = (
    "https://www.ncei.noaa.gov/data/global-historical-climatology-network-daily/"
    "access/SPE00155259.csv"
)
CADTEP_URL = (
    "https://static-m.meteo.cat/content/climatologia/series-climatiques/"
    "series-climatiques-baic-diaries-des-de-1950.zip"
)
CADTEP_MEMBER = "baic0008d.txt"

# Each source owns a disjoint slice, newest source wins where they could overlap.
GHCN_UNTIL = date(1949, 12, 31)
CADTEP_FROM = date(1950, 1, 1)
CADTEP_UNTIL = date(2025, 12, 31)

ROOT = Path(__file__).resolve().parents[2]
XEMA_DIR = ROOT / "data" / "xema" / "D5"
OUTPUT_DIR = ROOT / "data" / "fabra"

STATION = {
    "name": "Barcelona - Observatori Fabra",
    "station": "D5",
    "latitude": 41.41864,
    "longitude": 2.12379,
    "altitude": 410,
}


def _frame(rows: dict) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame.from_dict(rows, orient="index")
    frame.index = pd.to_datetime(frame.index)
    return frame.sort_index()


def fetch_ghcn() -> pd.DataFrame:
    """1913–1949 from GHCN-Daily. Values arrive in tenths; TN does not exist."""
    print(f"GHCN-Daily → {GHCN_UNTIL:%Y-%m-%d}")
    response = requests.get(GHCN_URL, timeout=300)
    response.raise_for_status()

    rows = {}
    for record in csv.DictReader(io.StringIO(response.text)):
        day = record["DATE"]
        if day > f"{GHCN_UNTIL:%Y-%m-%d}":
            continue
        entry = {}
        if record.get("TMAX", "").strip():
            entry["tasmax"] = int(record["TMAX"]) / 10.0
        if record.get("PRCP", "").strip():
            entry["prsum"] = int(record["PRCP"]) / 10.0
        # `tasmean` is deliberately absent — see the note in main().
        if entry:
            rows[day] = entry

    print(f"  {len(rows)} days")
    return _frame(rows)


def fetch_cadtep() -> pd.DataFrame:
    """1950–2008 from Meteocat CADTEP: one 8 MB zip, one station file inside."""
    print(f"Meteocat CADTEP → {CADTEP_FROM:%Y-%m-%d} to {CADTEP_UNTIL:%Y-%m-%d}")
    response = requests.get(CADTEP_URL, timeout=300)
    response.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        member = next(n for n in archive.namelist() if n.endswith(CADTEP_MEMBER))
        text = archive.read(member).decode("latin-1")

    rows = {}
    for line in text.splitlines():
        parts = line.rstrip().split("\t")
        # The file opens with a free-text header; data rows start with a year.
        if len(parts) < 6 or not parts[0].strip().isdigit():
            continue
        year, month, dom = (int(p) for p in parts[:3])
        # Bound by date, not year: the cutoff falls mid-November 1995, where the
        # station's own record takes over.
        if not CADTEP_FROM <= date(year, month, dom) <= CADTEP_UNTIL:
            continue

        entry = {}
        for key, index in (("prsum", 3), ("tasmax", 4), ("tasmin", 5)):
            raw = parts[index].strip() if len(parts) > index else ""
            try:
                entry[key] = float(raw)
            except ValueError:
                continue
        if entry:
            rows[f"{year:04d}-{month:02d}-{dom:02d}"] = entry

    print(f"  {len(rows)} days")
    return _frame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Fabra's full record.")
    parser.add_argument(
        "--refresh-history",
        action="store_true",
        help="Re-download GHCN and CADTEP instead of reusing the stored history.",
    )
    args = parser.parse_args()

    stored = load_existing(OUTPUT_DIR)

    if args.refresh_history or stored.empty:
        base = pd.concat([fetch_ghcn(), fetch_cadtep()])
        base = base[~base.index.duplicated(keep="last")].sort_index()
    else:
        # The archives never gain days retroactively, and the previous build
        # already merged them, so the committed series is the base to build on.
        base = stored
        print(
            f"Reusing stored series → {len(base)} days "
            "(pass --refresh-history to re-pull the archives)"
        )

    xema = load_existing(XEMA_DIR)
    if xema.empty:
        raise SystemExit(
            f"No XEMA data in {XEMA_DIR} — run `pixi run fetch-xema --station D5` first."
        )
    print(f"XEMA D5 (already stored) → {len(xema)} days")

    # Field-level, not row-level: the station wins wherever it published a
    # value, and the archives fill only what it left empty.
    combined = xema.combine_first(base).sort_index()

    # Neither historical source records a daily mean, and deriving one as
    # (TX+TN)/2 would be wrong to publish: measured against this station's own
    # half-hourly data it runs +0.78 °C warm (MAE 0.83), so splicing it onto the
    # true means from 2009 would fake a 0.78 °C step exactly at the join. The
    # column is left empty instead, and the chart skips the gap.
    for name in SERIES_DIGITS:
        if name not in combined.columns:
            combined[name] = pd.NA
    combined = combined[list(SERIES_DIGITS)]

    span = f"{combined.index.min():%Y-%m-%d} → {combined.index.max():%Y-%m-%d}"
    print(f"\n{len(combined)} days total, {span}")
    for name in ("tasmin", "tasmean", "tasmax", "prsum"):
        print(f"  {name}: {int(combined[name].notna().sum())} values")

    manifest = write_season_files(combined, OUTPUT_DIR)
    write_manifest(
        manifest,
        OUTPUT_DIR,
        location=STATION,
        source="Fabra composite: GHCN-Daily 1913–1949, Meteocat CADTEP 1950–2008, XEMA D5 2009–",
    )


if __name__ == "__main__":
    main()
