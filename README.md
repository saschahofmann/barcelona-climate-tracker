# Barcelona Climate Tracker

A static climate dashboard for Barcelona. No backend, no database, no hosting bill —
ERA5 data is committed to the repo, baked into the HTML at build time, and published to
GitHub Pages by Actions.

**Live:** https://saschahofmann.github.io/barcelona-climate-tracker/

## Data

Daily minimum, mean and maximum 2m temperature from the **ERA5 reanalysis**, read
directly from ECMWF's ARCO Zarr store. `src/barcelona_climate_tracker/era5_daily.py`
fetches it and writes one JSON file per season to `data/era5/`:

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
  "units": "degC",
  "time": ["2026-06-01", "..."],
  "tasmin": [18.2, "..."], "tasmean": [21.1, "..."], "tasmax": [24.8, "..."]
}
```

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
- Extending `START_YEAR` back a decade is the point where the chart needs the
  climatology band (percentiles across a 1991–2020 baseline) instead of one line per
  year — past ~4 overlaid lines nobody can track them.
