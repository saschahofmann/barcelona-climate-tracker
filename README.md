# Barcelona Climate Tracker

A static climate dashboard for Barcelona. No backend, no database, no hosting bill —
data is committed to the repo, baked into the HTML at build time, and published to
GitHub Pages by Actions.

**Live:** https://saschahofmann.github.io/barcelona-climate-tracker/

## Status

The data in `data/daily.json` is **synthetic placeholder data**, produced by
`scripts/generate-dummy-data.mjs`. Its shape deliberately mirrors the
[Open-Meteo](https://open-meteo.com/) archive API response (columnar `daily` arrays),
so replacing the generator with a real fetch is a change to one script.

## Stack

| Piece | Choice | Why |
|---|---|---|
| Site | Astro | Ships zero JS by default; data imports resolve at build time |
| Charts | Hand-rolled SVG | Rendered during the build — no charting library in the bundle |
| Data | JSON in git | Git is the time-series store: free, versioned, diffable |
| Host | GitHub Pages | Free for public repos |
| CI | GitHub Actions | Unlimited minutes on public repos |

## Local development

```bash
pnpm install
```

```bash
pnpm dev
```

Regenerate the placeholder data (deterministic — same end date gives the same numbers):

```bash
pnpm data:generate
```

## Deployment

`.github/workflows/deploy.yml` builds and publishes on every push to `main`, and on
manual dispatch. It needs GitHub Pages set to **Source: GitHub Actions** under
Settings → Pages.

`astro.config.mjs` sets `base: '/barcelona-climate-tracker'` because this is a project
page. Drop that line if the site moves to a custom domain.

## Next step: real data on a daily schedule

Add a second workflow that fetches and commits, rather than folding the fetch into the
deploy:

1. `fetch-data.yml` — cron `0 6 * * *`, writes `data/`, commits with `contents: write`.
2. `deploy.yml` triggers off it via `workflow_run` (commits made with `GITHUB_TOKEN`
   don't fire a `push` event, so a `push` trigger alone would never run).

Splitting them means a failed fetch leaves the last good deploy standing, and the site
can be rebuilt without re-fetching.

Things that bite:

- GitHub's cron drifts 5–60 minutes and occasionally skips a run. Make the fetch
  idempotent and able to backfill a missed day.
- Scheduled workflows auto-disable after 60 days of repo inactivity. Daily data commits
  count as activity, so this is self-solving here.
- Pass `timezone=Europe/Madrid` to the API rather than doing timezone maths locally.
- Open-Meteo is CC-BY-4.0 and needs attribution in the footer.
