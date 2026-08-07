/** Shared helpers for the build-time SVG charts. */

/** Round to 2dp so generated SVG paths stay small and diff cleanly. */
export const r2 = (v) => Math.round(v * 100) / 100;

/**
 * Axis ticks on round numbers, covering [min, max].
 * Returns the padded domain alongside the ticks so the scale and the axis agree.
 */
export function niceTicks(min, max, target = 5) {
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const rawStep = (max - min) / target;
  const magnitude = 10 ** Math.floor(Math.log10(rawStep));
  const normalized = rawStep / magnitude;
  const step =
    (normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10) * magnitude;

  const start = Math.floor(min / step) * step;
  const end = Math.ceil(max / step) * step;

  const ticks = [];
  // Nudge the bound to survive floating-point drift on the final tick.
  for (let v = start; v <= end + step * 1e-9; v += step) {
    ticks.push(Number(v.toPrecision(12)));
  }
  return { ticks, min: start, max: end };
}

const MONTH_FMT = new Intl.DateTimeFormat('en-GB', { month: 'short', timeZone: 'UTC' });
const DAY_FMT = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  timeZone: 'UTC',
});

export const asDate = (iso) => new Date(`${iso}T00:00:00Z`);
export const monthLabel = (iso) => MONTH_FMT.format(asDate(iso));
export const dayLabel = (iso) => DAY_FMT.format(asDate(iso));

const sum = (values) => values.reduce((acc, v) => acc + v, 0);

/**
 * Collapses the daily series into calendar months.
 *
 * Partial months at either end of the window are dropped — a half-month
 * precipitation total sitting next to full months would read as a real dip.
 */
export function monthlyAggregates(daily, { limit = 12 } = {}) {
  const buckets = new Map();

  daily.time.forEach((iso, i) => {
    const key = iso.slice(0, 7);
    if (!buckets.has(key)) buckets.set(key, { key, days: [] });
    buckets.get(key).days.push(i);
  });

  const daysInMonth = (key) => {
    const [year, month] = key.split('-').map(Number);
    return new Date(Date.UTC(year, month, 0)).getUTCDate();
  };

  return [...buckets.values()]
    .filter((bucket) => bucket.days.length === daysInMonth(bucket.key))
    .slice(-limit)
    .map(({ key, days }) => ({
      key,
      label: monthLabel(`${key}-01`),
      year: Number(key.slice(0, 4)),
      meanTemp: sum(days.map((i) => daily.temperature_2m_mean[i])) / days.length,
      maxTemp: Math.max(...days.map((i) => daily.temperature_2m_max[i])),
      minTemp: Math.min(...days.map((i) => daily.temperature_2m_min[i])),
      precipitation: sum(days.map((i) => daily.precipitation_sum[i])),
    }));
}
