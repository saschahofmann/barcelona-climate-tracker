# Barcelona Climate Tracker

A static climate dashboard for Barcelona. No backend, no database, no hosting bill —
ERA5 data is committed to the repo, baked into the HTML at build time, and published to
GitHub Pages by Actions.

**Live:** https://saschahofmann.github.io/barcelona-climate-tracker/

## Data

Daily **temperature**, **relative humidity** and **precipitation** from the ERA5
reanalysis, read directly from ECMWF's ARCO Zarr store, covering **2000–2026**
(107 seasons). `src/barcelona_climate_tracker/era5_daily.py` fetches it and writes one
JSON file per season to `data/era5/`:

```
data/era5/2026-JJA.json     season year + season code
data/era5/index.json        manifest: location, variables, every season
```

Each file holds parallel arrays where **the index is days since the season started**,
so index 0 is 1 June in every JJA file and years overlay day-for-day with no date
maths in the frontend.

```json
{
  "season_year": 2026, "season": "JJA",
  "start_date": "2026-06-01", "days": 63, "complete": false,
  "units": { "tas": "degC", "hurs": "%", "pr": "mm" },
  "time": ["2026-06-01", "..."],
  "tasmin": [18.2, "..."], "tasmean": [21.1, "..."], "tasmax": [24.8, "..."],
  "hursmin": [53, "..."], "hursmean": [70, "..."], "hursmax": [87, "..."],
  "prsum": [0.0, 0.1, "..."]
}
```

Two derivations worth knowing:

- **Relative humidity is not an ERA5 variable.** Single levels carries 2m dewpoint
  (`d2m`) only, so `hurs` is derived from the temperature/dewpoint pair via xclim's
  `relative_humidity` (Sonntag 1990).
- **`tp` is metres accumulated over the preceding hour**, so `prsum` is the 24 hourly
  values summed and scaled to mm.

Humidity is stored as whole percent — the extra digit would be false precision.

> **`hurs*` is fetched and stored but not charted.** The humidity view wasn't useful, so
> it is excluded from the measures the page offers and from the payload the page inlines
> (it was a third of it). The data is still written on every fetch, so bringing it back
> is a two-line change — a `MEASURES` entry in `src/lib/season-chart.js` and a line in
> the dataset shape in `src/pages/index.astro` — with no re-fetch.

Conventions worth knowing:

- **December belongs to the following winter.** DJF 2026 is Dec 2025 + Jan/Feb 2026.
- **`complete: false`** marks a season still in progress or clipped by the fetch range,
  so the chart can draw it as partial rather than as a short year.
- **Leap days land last in DJF**, so Feb 29 never shifts anything ahead of it.
- Daily statistics are aggregated from **hourly values in UTC**. Barcelona is UTC+1/+2,
  so daily extremes sit fractionally inside the true local ones.

Needs `ECMWF_API_KEY` in `.env`.

```bash
pixi run fetch-era5
```

## Station data: Meteocat XEMA

`src/barcelona_climate_tracker/xema_daily.py` downloads Meteocat's **daily** station
statistics and writes each station to its own directory under `data/xema/`, in the same
season-file shape as ERA5, so any source can be compared against any other day for day.

| Code | Station | Altitude | Record | Directory |
|---|---|---|---|---|
| `D5` | Barcelona - Observatori Fabra | 410 m | 1995-11-04 → | `data/xema/D5/` |
| `X4` | Barcelona - el Raval | 33 m | 2006-10-11 → | `data/xema/X4/` |

Fabra sits on the Collserola ridge well above the city; el Raval is in the dense centre.
Between them and ERA5 they bracket the urban heat island.

```bash
pixi run fetch-xema           # incremental, both stations
pixi run fetch-xema --full    # whole record
pixi run fetch-xema --station D5
```

Incremental reads the newest day already stored, re-fetches a 10-day trailing window,
merges, and rewrites only the season files whose content changed. A run with nothing new
touches no files, so it produces no git diff.

Source is [`7bvh-jvq2`](https://analisi.transparenciacatalunya.cat/d/7bvh-jvq2), *Dades
meteorològiques diàries de la XEMA*, on the Catalonia open-data portal. **No API key.**
Variables used: `1000` daily mean, `1001`/`1002` max and min (each with the hour it
occurred), `1100`–`1102` humidity, `1300` midnight-to-midnight rainfall. Filtered on
Meteocat's own `estat`, dropping only *No representatiu*.

> **Use the daily dataset, not the half-hourly feed.** This project first aggregated
> `nzvn-apee` (sub-hourly readings) into daily values itself. That was worse in every
> respect and the mistake is worth recording:
>
> - the half-hourly feed starts in **2009**; the daily one starts when each station did —
>   1995 for Fabra, 2006 for el Raval;
> - **the half-hourly extract has holes the station never had.** Fabra on 2026-08-14 has
>   no extract rows between 00:00 and 05:00, yet the daily record knows the minimum was
>   26.9 °C at 03:16. Whole days absent from the extract (2025-03-08, 03-09, 04-02) are
>   present and complete in the daily set — and two of them carry 9 mm and 37 mm of rain
>   that the old pipeline was silently scoring as zero;
> - aggregating the extract meant inventing coverage thresholds, cadence detection and
>   gap rules to guess which days to trust. Meteocat already publishes that judgement as
>   `estat`, so roughly 80 lines of heuristics went away;
> - `1000` is the **true** daily mean over the whole record, not the (TX+TN)/2
>   approximation — Meteocat publishes that separately as `1003`.
>
> If you ever do go back to `nzvn-apee`: `codi_estat` `T` means *validation pending*, not
> invalid; `N` is invalid and never appears. Rejecting `T` erases whole complete days.

### Fabra's full record, 1913 to now

```bash
pixi run build-fabra                    # daily; reuses the committed history
pixi run build-fabra --refresh-history  # re-pull GHCN and CADTEP
```

`src/barcelona_climate_tracker/fabra_historical.py` splices three sources into
`data/fabra/` — **41,286 days, 1913-08-06 → 2026-08-19**, 453 seasons. The raw station
series in `data/xema/D5/` is left untouched.

| period | source | supplies |
|---|---|---|
| 1913-08-06 → 1949-12-31 | GHCN-Daily [`SPE00155259`](https://www.ncei.noaa.gov/data/global-historical-climatology-network-daily/access/SPE00155259.csv) | TX, PPT — no TN |
| 1950-01-01 → 2025-12-31 | Meteocat CADTEP `baic0008d` | TX, TN, PPT |
| 1995-11-04 → now | XEMA D5 daily | everything, including the true mean |

The last two overlap deliberately, and the merge is **per field, not per day**: the
station wins wherever it published a value, and CADTEP fills only what it left empty.
Merging per day cost 659 values, because the station is missing TX or TN on ~660 days
where CADTEP's gap-filled series still has one. The result:

| series | span | missing after its first value |
|---|---|---|
| `tasmax` | 1913-08-06 → 2026-08-19 | **0** |
| `prsum` | 1913-08-06 → 2026-08-19 | **0** |
| `tasmin` | 1950-01-01 → 2026-08-19 | **0** |
| `tasmean` | 1995-11-04 → 2026-08-19 | 703 |

Exactly **one** calendar day is absent in 113 years — 2026-03-26, a Meteocat-wide outage
where el Raval has nothing either.

**`tasmean` is null before 1995, on purpose.** No source before then records a daily mean,
and the usual stand-in (TX + TN) / 2 was measured against this station's own sub-hourly
data at **+0.78 °C** (MAE 0.83; only 29% of days within 0.5 °C). Publishing it would fake
a 0.78 °C step at the join, which is worse than a gap. The chart skips nulls, so the mean
view simply starts in 1995 while min, max and rainfall run the whole way.

Both archives are static — GHCN's Fabra series ended in 2014, CADTEP gains a year at a
time — so a normal run reuses the committed series and re-reads only the station. No
offsets are corrected: GHCN sits +0.13 °C above the station on TX, CADTEP +0.24 °C on TX
and +0.30 °C on TN, all measured on real overlap and documented rather than adjusted.

### Raval's history: there isn't any

Raval is a modern station. Meteocat's daily set starts 2006-10-11 and nothing older
exists — CADTEP has only one Barcelona series (Fabra), and GHCN's `SPE00155991` runs
2008–2025, behind the live feed. If a long *city-level* series with minima is ever wanted,
the nearest is GHCN `SP000008181`, Barcelona/Aeropuerto, 1924 → present — but that is El
Prat, 15 km south-west and at 4 m, a genuinely different site.


### The three sources, measured

Over 7249 days where all three overlap (2006-10-11 → 2026-08-16), daily means in °C:

| series | Fabra (410 m) | Raval (33 m) | ERA5 | Fabra − Raval |
|---|---|---|---|---|
| tasmin | 12.4 | 15.3 | 12.7 | **−2.96** |
| tasmean | 15.6 | 18.2 | 16.1 | **−2.60** |
| tasmax | 20.4 | 21.6 | 19.6 | **−1.29** |
| precipitation | 570 | 564 | 622 | mm/yr |

| | record maximum |
|---|---|
| Fabra | **40.7 °C** on 2026-07-08 |
| Raval | 39.3 °C on 2010-08-27 |
| ERA5 | 34.8 °C on 2023-08-23 |

**Fabra is colder on average but records the hotter extreme**, which looks contradictory
and isn't. The gap is widest at night (−2.96) and narrowest by day (−1.29): the hilltop
sheds heat after dark and sits outside the city's heat island, while in a heatwave with
offshore flow it escapes the sea breeze that caps the coastal station. So the minima
separate strongly and the maxima nearly converge — with Fabra overtaking at the top end.

ERA5 runs about **2 °C cold** against a city thermometer, with correlations of 0.976–0.992
and MAE ≈ |bias|, meaning a near-constant offset rather than noise. It is therefore sound
for the *relative* comparison the chart does — year against year — but reads low in
absolute terms, and its grid cell averaging coastline and sea flattens the extremes
hardest.

#### Which "Barcelona record"?

The Fabra Observatory hosts **two** instruments. RACAB runs the historic *manual* station
whose century-long series is the city's reference; Meteocat runs the *automatic* station
`D5`, which is what this project reads. Both set records on 2026-07-08:

| | 2024-07-30 | 2026-07-08 |
|---|---|---|
| RACAB manual (113-year series) | 40.0 °C | **40.9 °C** |
| Meteocat automatic D5 (*this data*) | 39.5 °C | **40.7 °C** |

So the headline "40.9 °C, hottest in 113 years" is the manual series — the only one old
enough to support that claim. The 40.7 °C here is the correct automatic value. Some
coverage on the day reported 40.5 °C, an early figure later revised up.


## Stack

| Piece | Choice | Why |
|---|---|---|
| Fetch | Python / xarray / xclim | Reads the ARCO Zarr store directly |
| Site | Astro | Ships zero JS by default; data imports resolve at build time |
| Charts | Hand-rolled SVG | Rendered during the build — no charting library in the bundle |
| Data | JSON in git | Git is the time-series store: free, versioned, diffable |
| Host | GitHub Pages | Free for public repos |
| CI | GitHub Actions | Unlimited minutes on public repos |

Per-season files keep the daily commit small: only the in-progress season's file
changes, and closed seasons are written once and never diff again.

## Local development

```bash
pnpm install
```

```bash
pnpm dev
```

## Deployment

`.github/workflows/deploy.yml` builds and publishes on every push to `main`, and on
manual dispatch. It needs GitHub Pages set to **Source: GitHub Actions** under
Settings → Pages.

`astro.config.mjs` sets `base: '/barcelona-climate-tracker'` because this is a project
page. Drop that line if the site moves to a custom domain.

## The daily fetch

`.github/workflows/fetch-data.yml` runs at 06:00 UTC, updates every source, and commits
only if something changed. `deploy.yml` then rebuilds via `workflow_run`.

**Repo secrets:** `ECMWF_API_KEY` (required, ERA5). The station data needs no key.

### How it decides what is missing

There is no run-history lookup. Each fetcher reads the newest day already committed under
`data/`, subtracts a trailing window, and pulls from there:

| | window | why |
|---|---|---|
| ERA5 | 10 days | lands ~5 days behind, and preliminary ERA5T values get revised |
| XEMA | 10 days | recent days are revised, and days absent one morning appear later |

**State lives in the committed data, not in workflow metadata.** A skipped, cancelled or
failed run needs no special handling — the next run sees older files and backfills further
back on its own. That matters because GitHub cron drifts 5–60 minutes and does drop runs.

Late-arriving data is real and was measured: Fabra's 2026-08-13 was 32/48 sub-hourly
readings one morning and complete by that afternoon. Days genuinely absent from years ago
never return.

All three fetchers are idempotent: a run with nothing new reports `0 new, 0 file(s)
changed`, `git diff --quiet` sees nothing, and no commit happens.

### Steps

1. **Fetch ERA5** — needs the secret.
2. **Fetch XEMA station** — both stations.
3. **Rebuild Fabra composite** — overlays the fresh station data on the committed
   1913–2008 history. Without this the front page would freeze at whatever was last
   committed. No network beyond the station fetch; the manual `full` dispatch passes
   `--refresh-history` to re-pull the archives, which is what you want when CADTEP
   publishes another year.

Each is `continue-on-error`, so one dead source cannot block the others. Whatever landed is
committed, and *then* the job fails so the failure stays visible.

### Other decisions

- **`workflow_run` on deploy is required, not decorative.** Commits pushed with
  `GITHUB_TOKEN` deliberately do not fire `push`, so a `push`-only deploy would let data
  land and never ship.
- **`locked: true` on setup-pixi** fails the run if `pixi.lock` has drifted from
  `pyproject.toml`, rather than quietly installing something else.
- `pyproject.toml` lists **both `osx-arm64` and `linux-64`** — the lockfile needs the
  runner's platform or the install fails.
- Scheduled workflows auto-disable after 60 days of repo inactivity. Daily data commits
  count as activity, so this is self-solving.


## The chart

`src/lib/season-chart.js` holds the model builder and SVG renderer. Astro calls it at
build time to emit the default view; the browser calls the same functions to re-render on
interaction, so build and runtime output cannot drift.

Controls: **season** (four, plus *Whole year*), **measure** (temperature / precipitation),
**detail** (daily / 7-day average / weekly / monthly), **series** (mean / min / max) and up
to **5 years**. Clicking a year brings it to the front — it gets the daily low–high band
and a heavier stroke — and clicking it again drops the band without removing the series.
Removal is the × on a chip, or "Clear all".

Each selected year holds a colour slot until deselected, so removing one year never
repaints the others. The five slots are validated for colour-vision deficiency and
contrast in both modes.

### Detail: smoothing and resampling, all client-side

None of these fetch anything. The page already holds daily arrays; the rest is arithmetic:

| mode | what it does | points over a season |
|---|---|---|
| Daily | raw | ~92 |
| 7-day average | centred rolling mean | ~92 |
| Weekly | groups 7 days | ~13 |
| Monthly | groups calendar months | 3 |

Smoothing and weekly bucketing look similar and are not: smoothing keeps one point per
day and the shape, bucketing gives 13 points and pulls the extremes inward.

Two rules that matter:

- **A rolling window more than half empty yields null**, not an average of whatever
  survived, so a station outage stays a hole instead of being papered over.
- **Buckets collapse by the statistic's own meaning** — a weekly maximum is the hottest
  day of that week, not the average of its afternoons. That keeps the low–high band an
  actual envelope.

Smoothing is **disabled for precipitation**: the line is a running total, so a rolling
mean of it means nothing. The chip greys out rather than looking active while the chart
ignores it.

### The whole-year view

*Whole year* is assembled in the browser from the four season files — it needs no data of
its own, but it does need all four loaded, so selecting it fetches whichever are missing.

Two things it has to get right:

- **December comes from the following winter file.** DJF 2026 is Dec 2025 + Jan/Feb 2026,
  so building calendar year 2026 needs five season-years, not four.
- **29 February is dropped.** Placing the leap day at its natural index shifts March
  onwards by one in leap years only, so overlaying 2024 on 2025 would silently compare
  adjacent days. A fixed 365-slot common-year calendar keeps every year aligned; the leap
  day is still present in the DJF season view, where the season files park it last.


## Three pages, one per source

| Page | Source | Years | Page (gz) |
|---|---|---|---|
| `/` | Fabra, full record | **1913–2026** | 45 KB |
| `/raval/` | XEMA X4, el Raval | 2006–2026 | 14 KB |
| `/reanalysis/` | ERA5 reanalysis | 2000–2026 | 18 KB |

**Fabra is the entry point** — least heat-island-affected, steadiest reference, and the
only one reaching back beyond 2006.

The source is a **navigation choice, not a control**, so each page carries only its own
data. `src/lib/sources.js` holds one `import.meta.glob` per source; they run in Node at
build time, so the unselected sources never reach the browser.
`src/components/SourcePage.astro` is the whole page body, and `index.astro`, `raval.astro`
and `reanalysis.astro` are four-line wrappers passing a source key.

### On-demand season loading

Inlining every season stopped being viable once Fabra reached 41k days. Each page carries
three things instead:

1. the **default view rendered at build time** as SVG, so the chart is present before any
   JavaScript runs;
2. a lightweight **index** — which years exist per season, and how long each ran — so the
   year chips and legend are correct before any data arrives (2 KB gzipped even for
   Fabra's 453 seasons);
3. the **opening season's values**, so nothing is fetched to interact with the season the
   page lands on.

Every other season comes from a static endpoint generated by
`src/pages/data/[source]/[season].json.js`, one file per (source, season). Fetches are
cached in memory, so a season is pulled at most once per page load and switching back is
instant. While one is in flight the chart dims, pointer events are disabled and the note
reads `Loading Winter…`. A failed fetch leaves the previous chart on screen with a
retryable message rather than an empty frame.

The endpoint path uses the **source key**, not the page slug — `/reanalysis/` fetches from
`/data/era5/`.

Fabra's page is the largest because its opening season alone spans 113 years. Dropping
that to just the three default years would take the page under 12 KB, at the cost of a
fetch on the first year-chip click.
