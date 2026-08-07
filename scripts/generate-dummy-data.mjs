/**
 * Generates a year of plausible-but-fake daily climate data for Barcelona.
 *
 * The output shape deliberately mirrors the Open-Meteo archive API response
 * (columnar `daily` arrays keyed by variable) so that swapping this generator
 * for a real fetch is a drop-in change on the data side only.
 *
 * Deterministic: same end date in, same numbers out.
 *
 *   node scripts/generate-dummy-data.mjs [YYYY-MM-DD]
 */

import { writeFile, mkdir } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const OUT = resolve(ROOT, 'data/daily.json');

const DAYS = 365;

const LOCATION = {
  name: 'Barcelona',
  latitude: 41.3888,
  longitude: 2.159,
  timezone: 'Europe/Madrid',
};

/** Seeded PRNG so regenerating the file produces no spurious diffs. */
function mulberry32(seed) {
  let a = seed;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const rand = mulberry32(0x2026_0806);

/** Normal-ish noise from the sum of two uniforms. */
const jitter = (spread) => (rand() + rand() - 1) * spread;

const dayOfYear = (date) => {
  const start = Date.UTC(date.getUTCFullYear(), 0, 0);
  return Math.floor((date.getTime() - start) / 86_400_000);
};

/**
 * Seasonal mean temperature: a sinusoid fitted loosely to Barcelona's climate
 * normals — ~9.5 °C in January, ~25 °C in early August.
 */
function seasonalMean(doy) {
  const PEAK_DOY = 213; // 1 August
  const phase = ((doy - PEAK_DOY) / 365) * 2 * Math.PI;
  return 17.2 + 7.8 * Math.cos(phase);
}

/** Rain in Barcelona is bimodal: a spring bump and a much wetter autumn. */
function rainChance(doy) {
  const autumn = Math.exp(-(((doy - 288) / 45) ** 2)); // peaks late October
  const spring = Math.exp(-(((doy - 125) / 40) ** 2)); // peaks early May
  return 0.06 + 0.34 * autumn + 0.16 * spring;
}

const round = (value, places = 1) => Number(value.toFixed(places));

const time = [];
const temperature_2m_mean = [];
const temperature_2m_max = [];
const temperature_2m_min = [];
const precipitation_sum = [];

const endArg = process.argv[2];
const end = endArg ? new Date(`${endArg}T00:00:00Z`) : new Date();
if (Number.isNaN(end.getTime())) {
  console.error(`Invalid date: ${endArg}. Expected YYYY-MM-DD.`);
  process.exit(1);
}
end.setUTCHours(0, 0, 0, 0);

// A drift term stands in for weather persistence, so consecutive days are
// correlated rather than independently noisy.
let drift = 0;

for (let i = DAYS - 1; i >= 0; i--) {
  const date = new Date(end.getTime() - i * 86_400_000);
  const doy = dayOfYear(date);

  drift = drift * 0.7 + jitter(2.4);
  const mean = seasonalMean(doy) + drift;
  const spread = 4.5 + jitter(1.2);

  time.push(date.toISOString().slice(0, 10));
  temperature_2m_mean.push(round(mean));
  temperature_2m_max.push(round(mean + spread * 0.55));
  temperature_2m_min.push(round(mean - spread * 0.45));

  const wet = rand() < rainChance(doy);
  // Rainfall is heavy-tailed — most wet days are light, a few are downpours.
  // Scaled so the annual total lands near Barcelona's ~600 mm normal.
  precipitation_sum.push(wet ? round(rand() ** 3 * 44 + 0.2) : 0);
}

const payload = {
  location: LOCATION,
  source: 'synthetic',
  source_note: 'Generated placeholder data. Not real observations.',
  generated_at: new Date().toISOString(),
  units: {
    temperature_2m_mean: '°C',
    temperature_2m_max: '°C',
    temperature_2m_min: '°C',
    precipitation_sum: 'mm',
  },
  daily: {
    time,
    temperature_2m_mean,
    temperature_2m_max,
    temperature_2m_min,
    precipitation_sum,
  },
};

await mkdir(dirname(OUT), { recursive: true });
await writeFile(OUT, `${JSON.stringify(payload, null, 2)}\n`);

console.log(`Wrote ${time.length} days to ${OUT} (${time[0]} → ${time.at(-1)})`);
