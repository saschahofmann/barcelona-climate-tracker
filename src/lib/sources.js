/**
 * The data sources, and the build-time loader that shapes any one of them for
 * the page.
 *
 * Each source gets its own page so only that source's data is inlined — the
 * globs below run in Node during the build and never reach the browser.
 */

import { SEASON_NAMES } from './season-chart.js';

// Glob patterns have to be literal, so one per source rather than a parameter.
const MODULES = {
  fabra: import.meta.glob('../../data/xema/D5/*.json', { eager: true }),
  raval: import.meta.glob('../../data/xema/X4/*.json', { eager: true }),
  era5: import.meta.glob('../../data/era5/*.json', { eager: true }),
};

// Order here is the order of the nav.
export const SOURCES = {
  fabra: {
    key: 'fabra',
    label: 'Observatori Fabra weather station',
    nav: 'Fabra',
    href: '',
    blurb:
      'A hilltop observatory at 410 m on the Collserola ridge above the city ' +
      '(Meteocat XEMA station D5). Clear of the street-level heat island, which makes it ' +
      'the steadiest reference of the three: around 2.6 °C cooler than el Raval on ' +
      'average, yet it holds the hotter record — in a heatwave the ridge escapes the sea ' +
      'breeze that caps the coast.',
  },
  raval: {
    key: 'raval',
    label: 'Barcelona - el Raval weather station',
    nav: 'Raval',
    href: 'raval',
    blurb:
      'A thermometer in the dense middle of the city at 33 m (Meteocat XEMA station ' +
      'X4). The warmest of the three and the closest to what the street actually ' +
      'feels like, with occasional gaps where the station dropped out.',
  },
  era5: {
    key: 'era5',
    label: 'ERA5 reanalysis',
    nav: 'Reanalysis',
    href: 'reanalysis',
    blurb:
      'Modelled reanalysis on a grid cell covering the city and the sea beside it. ' +
      'Complete and gap-free, and the only source here reaching back before 2009, but ' +
      'around 2 °C cooler than a city-centre thermometer.',
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
  const entries = Object.entries(modules);

  const manifestEntry = entries.find(([path]) => path.endsWith('index.json'));
  if (!manifestEntry) {
    throw new Error(
      `No data found for source "${key}" — run the fetch script for it before building.`
    );
  }
  const manifest = manifestEntry[1].default;

  const files = entries
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
