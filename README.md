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

`src/barcelona_climate_tracker/xema_daily.py` downloads real weather stations and writes
each to its own directory under `data/xema/`, in the **same season-file shape** as ERA5,
so any source can be compared against any other day for day.

| Code | Station | Altitude | Directory |
|---|---|---|---|
| `D5` | Barcelona - Observatori Fabra | 410 m | `data/xema/D5/` |
| `X4` | Barcelona - el Raval | 33 m | `data/xema/X4/` |

Fabra sits on the Collserola ridge well above the city; el Raval is in the dense centre.
Between them and ERA5 they bracket the urban heat island.

```bash
pixi run fetch-xema --full
```

```bash
pixi run fetch-xema
```

```bash
pixi run fetch-xema --station D5
```

Both stations are fetched unless `--station` names one. The first form is a bulk download
of the whole record; the second is incremental — it reads the newest day already stored,
re-fetches a 7-day trailing window (recent rows are unvalidated and get revised, and the
newest day is usually partial), merges, and rewrites only the season files whose content
actually changed. A run with nothing new touches no files, so it produces no git diff.

Source is the [Socrata mirror](https://analisi.transparenciacatalunya.cat/Medi-Ambient/Dades-meteorol-giques-de-la-XEMA/nzvn-apee)
of Meteocat's XEMA network — **no API key needed**. Set `SOCRATA_APP_TOKEN` to lift the
anonymous rate limit if you have one.

Things that bite, all learned the hard way:

- **Don't filter `codi_estat` to `'V'`.** `V` is validated, `T` is suspect (~0.3%), and
  *null* means not-yet-validated — which is every recent day. The script drops `T` and
  keeps the rest. Filtering to `V` would silently discard the last months of data.
- **Don't page with `$offset` + `$order=data_lectura`.** That column is unindexed and the
  query times out. The script chunks by calendar month instead, which is also resumable.
- **The reporting cadence changes mid-record.** X4 logged hourly (`codi_base` `HO`,
  24/day) until partway through 2014 and half-hourly (`SH`, 48/day) since. The expected
  sample count is derived per day from `codi_base` — a hardcoded 48 treats every
  complete hourly day as half-missing and silently discards 2009–2013 entirely.
- Days below 75% of **their own** cadence have their **averages and extremes** withheld
  — a mean over a handful of night-time samples is a biased estimate of the day.
- **Precipitation is deliberately not gated that way.** It is an accumulation of
  intervals that really happened, so a short day is a *lower bound on observed rain*,
  not a biased estimate. Withholding it is strictly worse: a cumulative total treats a
  missing day as zero either way, so nulling only discards rain that was genuinely
  measured. Partial rain days are kept and the fetch prints how many there were.
- A day is dropped only when **every** series is missing. Keying that on temperature
  would throw away good rain data whenever the temperature sensor alone dropped out.
- **The mirror starts in 2009 regardless of station age.** X4's metadata claims 2006 and
  Fabra's series began in 1913, but neither is available through this endpoint before
  2009 — only ERA5 reaches back further, to 2000. See the note below on Fabra's long
  series.

Because X4 records true sub-hourly extremes (variables `40`/`42`), its daily max/min are
real extremes rather than the min/max of spot readings — unlike the ERA5 pipeline, which
can only sample hourly.

### TODO: Fabra's pre-2009 record, direct from Meteocat

The Socrata mirror this project uses only carries XEMA from **2009**, which throws away
almost the entire point of Fabra: its series began **6 August 1913** and is the reference
century-long record for Barcelona. Worth fetching separately.

What the landscape looks like, from a first pass — none of it verified against a working
download yet:

- **Meteocat CADTEP** — continuous, homogenised daily and monthly series, free access,
  but only back to **1950**. Selected by series then variable through the climatology
  pages, not the XEMA REST API this project uses. Covers three-quarters of the gap and is
  the obvious first target.
- **1913–1950** is not in CADTEP. Fabra's originals belong to the Reial Acadèmia de
  Ciències i Arts de Barcelona (RACAB), and the pre-1950 material exists through
  digitisation work rather than an API. Barcelona has instrumental records to 1780 and a
  rainfall series reconstructed from 1786, so the material is there — it just is not a
  download.
- The homogenised CADTEP values will **not** be identical to the raw XEMA automatic
  readings already stored, so the two cannot simply be concatenated. Either keep them as
  separate sources or reconcile the overlap deliberately.

Note also that RACAB's **manual** station at Fabra and Meteocat's **automatic** station
(D5) are different instruments reporting slightly different numbers — see the record
check below.

### The three sources, measured

Over 6414 days where all three overlap (2009-01-01 → 2026-08-09), daily means in °C:

| series | Fabra (410 m) | Raval (33 m) | ERA5 | Fabra − Raval |
|---|---|---|---|---|
| tasmin | 12.5 | 15.4 | 12.8 | **−2.96** |
| tasmean | 15.7 | 18.3 | 16.2 | **−2.58** |
| tasmax | 20.4 | 21.7 | 19.7 | **−1.24** |
| precipitation | 569 | 577 | 626 | mm/yr |

| | record maximum |
|---|---|
| Fabra | **40.7 °C** on 2026-07-08 |
| Raval | 39.3 °C on 2010-08-27 |
| ERA5 | 34.8 °C on 2023-08-23 |

**Checked against reporting — the pipeline is right, but there are two thermometers.**

The Fabra Observatory hosts *two* stations. RACAB (Reial Acadèmia de Ciències i Arts de
Barcelona) runs the historic **manual** station, read by an observer, whose series is the
century-long Barcelona reference. Meteocat runs the **automatic** station **D5**,
electronic and logging every 30 minutes — that is the one this project fetches, and its
series only starts in 1995.

They read the same air a few tenths apart, and both set their own record on 2026-07-08:

| | 2024-07-30 | 2026-07-08 |
|---|---|---|
| RACAB manual (113-year series) | 40.0 °C | **40.9 °C** |
| Meteocat automatic D5 (*this data*) | 39.5 °C | **40.7 °C** |

So the headline "Barcelona hits 40.9 °C, hottest in 113 years" is the manual series, which
is the only one old enough to make a 113-year claim. This project's 40.7 °C is the correct
automatic value, verified against the raw half-hourly rows: 48/48 samples that day, peak
at 13:00, nothing dropped by the quality filter. The 2024 pair differ the same way — 40.0
manual against 39.5 automatic — so do not read the reported previous record as
contradicting the 39.5 stored here.

Some coverage on the day reported **40.5 °C**, an early figure later revised up.

Incidentally this validates using variable `40` rather than spot readings: on 2026-07-08
the half-hourly *maximum* peaked at 40.7 °C while the half-hourly *spot* temperature
(variable `32`) only reached 40.0 °C. Sampling spot values would have understated the
record by 0.7 °C.

The hero on each page is scoped to the years actually held (`2009–2026`), so it does not
claim to be an all-time record.

**Fabra is colder on average but records the hotter extreme**, which looks contradictory
and isn't. The gap is widest at night (−2.96) and narrowest by day (−1.24): the hilltop
sheds heat after dark and sits outside the city's heat island, while in a heatwave with
offshore flow it escapes the sea breeze that caps the coastal station. So the daily
minima separate strongly and the maxima nearly converge — with Fabra overtaking at the
top end.

Against Raval alone, ERA5 runs about **2 °C cold** (tasmin +2.63, tasmean +2.08,
tasmax +1.95 for the station over the reanalysis) with correlations of 0.976–0.992 and
MAE ≈ |bias|, meaning a near-constant offset rather than noise. ERA5 is therefore sound
for the *relative* comparison the chart does — year against year — but reads low in
absolute terms against any city thermometer, and its grid cell averaging coastline and
sea flattens the extremes hardest.

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

`.github/workflows/fetch-data.yml` runs at 06:00 UTC, updates both sources, and commits
only if something changed. `deploy.yml` then rebuilds via `workflow_run`.

**Repo secrets:** `ECMWF_API_KEY` (required, ERA5). `SOCRATA_APP_TOKEN` is optional — it
only lifts the anonymous rate limit on the XEMA mirror.

### How it decides what is missing

There is no run-history lookup. Each fetcher reads the newest day already committed under
`data/`, subtracts a trailing window, and pulls from there:

| | trailing window | why |
|---|---|---|
| ERA5 | 10 days | lands ~5 days behind real time, and preliminary ERA5T values get revised |
| XEMA | 7 days | the newest day is usually partial, and recent rows are unvalidated |

**State lives in the committed data, not in workflow metadata.** A skipped, cancelled or
failed run needs no special handling — the next run sees older files and backfills further
back on its own. That matters because GitHub cron drifts 5–60 minutes and does
occasionally drop a run entirely.

Both fetchers are idempotent: a run with nothing new reports `0 new, 0 file(s) changed`,
`git diff --quiet` sees nothing, and no commit happens.

### Other decisions

- **Each source can fail independently.** Both steps are `continue-on-error`, so a dead
  ECMWF endpoint cannot stop the station update. Whatever landed is committed, and *then*
  the job fails so the failure stays visible.
- **`workflow_run` on deploy is required, not decorative.** Commits pushed with
  `GITHUB_TOKEN` deliberately do not fire `push`, so a `push`-only deploy would let data
  land and never ship. It runs on completion regardless of conclusion, since the fetch
  commits before failing.
- **`locked: true` on setup-pixi** fails the run if `pixi.lock` has drifted from
  `pyproject.toml`, rather than quietly installing something else.
- `pyproject.toml` lists **both `osx-arm64` and `linux-64`** — the lockfile needs the
  runner platform or the install fails.
- Scheduled workflows auto-disable after 60 days of repo inactivity. Daily data commits
  count as activity, so this is self-solving.
- ERA5 goes back to 1940. Pushing `RECORD_START` earlier costs only fetch time and repo
  size, but the whole archive inlined into a page would stop being free — at some point
  the data has to move to files fetched on demand instead.

Force a complete re-download from the Actions tab: run the workflow manually with **full**
ticked, which passes `--full` to both fetchers. Worth doing occasionally — the trailing
window does not reach back far enough to pick up ERA5T→ERA5 revisions, which land months
later.
## The chart

`src/lib/season-chart.js` holds the model builder and SVG renderer. Astro calls it at
build time to emit the default view; the browser calls the same functions to re-render
on interaction, so build and runtime output cannot drift.

Controls: season (4), measure (temperature / precipitation), series
(mean / minimum / maximum), and up to **5 years** from 2000–2026. Clicking a year brings
it to the front — it gets the daily low–high band and a heavier stroke. Selection is
purely additive; removal is the × on a selected chip, or "Clear all". Each chip is a
wrapper holding two buttons, since a button cannot nest inside another button.

**Precipitation is plotted cumulatively** across the season, and the series control
hides for it. A daily total has no min/mean/max to choose between, and five years of
spiky daily rainfall overlaid is unreadable — running totals stay comparable and answer
the actual question ("is this year running wet or dry?"). The day's own rainfall is in
the tooltip as `+x`.

Each selected year holds a colour slot until it is deselected, so removing one year never
repaints the others. The five slots are validated for colour-vision deficiency and
contrast in both light and dark mode; three of them fall below 3:1 on the light surface,
which is why the legend swatches and the table view are not optional.

## Three pages, one per source

| Page | Source | Years |
|---|---|---|
| `/` | XEMA D5, Observatori Fabra | 2009–2026 |
| `/raval/` | XEMA X4, el Raval | 2009–2026 |
| `/reanalysis/` | ERA5 reanalysis | 2000–2026 |

**Fabra is the entry point.** It is the least heat-island-affected of the three and the
steadiest reference, so it is what the site opens on.

The source is a **navigation choice, not a control**, so each page inlines only its own
data. `src/lib/sources.js` holds one `import.meta.glob` per source — they run in Node
during the build, so the unselected sources never reach the browser. Verified: no
source's years appear in another source's payload.

`src/components/SourcePage.astro` is the whole page body; `index.astro`, `raval.astro`
and `reanalysis.astro` are four-line wrappers that pass a source key.

Everything for the selected source is inlined, so switching seasons, measures or years
costs no network round trip. If a page grows again, the escape hatch is to emit one JSON
file per season via an Astro static endpoint and fetch on demand — cheaper on first
paint, but slower for anyone who clicks through more than one season.
