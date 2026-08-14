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

## Next step: the daily fetch

Add a second workflow rather than folding the fetch into the deploy:

1. `fetch-era5.yml` — cron `0 6 * * *`, runs `pixi run fetch-era5`, commits `data/era5/`
   with `contents: write`. Needs `ECMWF_API_KEY` as a repo secret.
2. `deploy.yml` triggers off it via `workflow_run` — commits made with `GITHUB_TOKEN`
   don't fire a `push` event, so a `push` trigger alone would never run.

Splitting them means a failed fetch leaves the last good deploy standing, and the site
can be rebuilt without re-fetching.

Things that bite:

- GitHub's cron drifts 5–60 minutes and occasionally skips a run. ERA5 lags ~5 days
  anyway, so make the fetch re-request a trailing window rather than only "yesterday".
- Scheduled workflows auto-disable after 60 days of repo inactivity. Daily data commits
  count as activity, so this is self-solving here.
- ERA5 goes back to 1940. Pushing `START_YEAR` earlier costs only fetch time and repo
  size, but the whole archive inlined into the page would stop being free — at some
  point the data has to move to files fetched on demand instead.

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

All 27 years of all four seasons, across the four charted series, are inlined into the
HTML (~53 KB gzipped), so switching seasons, measures or years costs no network round
trip. If that grows again, the escape hatch is to emit one JSON file per season via an
Astro static endpoint and fetch on demand — cheaper on first paint, but slower for
anyone who clicks through more than one season.
