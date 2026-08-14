/**
 * The two data sources, and the build-time loader that shapes either one for
 * the page.
 *
 * Each source gets its own page so only that source's data is inlined — the
 * globs below run in Node during the build and never reach the browser.
 */

import { SEASON_NAMES } from './season-chart.js';

// Glob patterns have to be literal, so one per source rather than a parameter.
const MODULES = {
  era5: import.meta.glob('../../data/era5/*.json', { eager: true }),
  xema: import.meta.glob('../../data/xema/*.json', { eager: true }),
};

export const SOURCES = {
  era5: {
    key: 'era5',
    label: 'ERA5 reanalysis',
    nav: 'Reanalysis',
    href: '',
    blurb:
      'Modelled reanalysis on a grid cell covering the city and the sea beside it. ' +
      'Complete and gap-free, but around 2 °C cooler than a city-centre thermometer.',
  },
  xema: {
    key: 'xema',
    label: 'Barcelona - el Raval weather station',
    nav: 'Station',
    href: 'station',
    blurb:
      'A real thermometer in the middle of the city (Meteocat XEMA station X4). ' +
      'Warmer than the reanalysis and closer to lived experience, with occasional gaps ' +
      'where the station dropped out.',
  },
};

/** Highest daily maximum anywhere in the record, tolerating gaps. */
function hottestDay(files) {
  let best = { value: -Infinity, date: null };
  for (const file of files) {
    file.tasmax.forEach((value, i) => {
      if (value != null && value > best.value) {
        best = { value, date: file.time[i] };
      }
    });
  }
  return best;
}

export function loadSource(key) {
  const modules = MODULES[key];
  const manifest = modules[`../../data/${key}/index.json`].default;

  const files = Object.entries(modules)
    .filter(([path]) => !path.endsWith('index.json'))
    .map(([, module]) => module.default);

  // The `time` array is dropped from the inlined payload: every date is
  // recoverable from `start` plus the index, which roughly halves the page.
  // Humidity is fetched and stored but deliberately not charted.
  const dataset = {};
  for (const file of files) {
    const season = (dataset[file.season] ??= { name: SEASON_NAMES[file.season], years: {} });
    season.years[file.season_year] = {
      start: file.time[0],
      days: file.days,
      complete: file.complete,
      tas: { min: file.tasmin, mean: file.tasmean, max: file.tasmax },
      pr: { sum: file.prsum },
    };
  }

  // The season holding the newest observation leads the chart.
  const latest = files.reduce((a, b) => (b.time[b.days - 1] > a.time[a.days - 1] ? b : a));
  const defaultSeason = latest.season;
  const defaultYears = Object.keys(dataset[defaultSeason].years)
    .map(Number)
    .sort((a, b) => b - a)
    .slice(0, 3);

  return {
    source: SOURCES[key],
    manifest,
    dataset,
    defaultSeason,
    defaultYears,
    firstYear: Math.min(...files.map((file) => file.season_year)),
    lastYear: Math.max(...files.map((file) => file.season_year)),
    hottest: hottestDay(files),
  };
}
