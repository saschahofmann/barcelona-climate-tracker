/**
 * Season chart model + SVG renderer.
 *
 * Shared deliberately: Astro calls this at build time to emit the default view,
 * and the browser calls the same functions to re-render when controls change.
 * One implementation, so the two can't drift.
 */

import { niceTicks, r2 } from './chart.js';

export const W = 900;
export const H = 360;
export const PAD = { top: 18, right: 72, bottom: 30, left: 54 };
export const PLOT_W = W - PAD.left - PAD.right;
export const PLOT_H = H - PAD.top - PAD.bottom;

export const MAX_YEARS = 5;

/**
 * `stats: true` means the measure has min/mean/max and a daily low–high band.
 * Precipitation has neither — it is a daily total, and five years of spiky
 * daily bars overlaid is unreadable, so it accumulates across the season.
 */
export const MEASURES = [
  { key: 'tas', label: 'Temperature', unit: '°C', suffix: '°', stats: true, digits: 1 },
  {
    key: 'pr',
    label: 'Precipitation',
    unit: 'mm',
    suffix: 'mm',
    stats: false,
    digits: 1,
    cumulative: true,
  },
];

export const measureOf = (key) => MEASURES.find((measure) => measure.key === key) ?? MEASURES[0];

export const STATS = [
  { key: 'min', label: 'Minimum' },
  { key: 'mean', label: 'Mean' },
  { key: 'max', label: 'Maximum' },
];

export const SEASON_NAMES = {
  DJF: 'Winter',
  MAM: 'Spring',
  JJA: 'Summer',
  SON: 'Autumn',
};

// Five validated categorical slots. A year keeps its slot for as long as it is
// selected, so removing one year never repaints the others.
export const SERIES_SLOTS = [1, 2, 3, 4, 5];

export function addDays(startIso, days) {
  const date = new Date(`${startIso}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

const MONTH_FMT = new Intl.DateTimeFormat('en-GB', { month: 'short', timeZone: 'UTC' });
const DAY_FMT = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  timeZone: 'UTC',
});

export const monthOf = (iso) => MONTH_FMT.format(new Date(`${iso}T00:00:00Z`));
export const dayOf = (iso) => DAY_FMT.format(new Date(`${iso}T00:00:00Z`));

export const xAt = (i, maxDays) =>
  PAD.left + (maxDays > 1 ? (i / (maxDays - 1)) * PLOT_W : PLOT_W / 2);

export const makeScale = (domain) => (value) =>
  PAD.top + (1 - (value - domain.min) / (domain.max - domain.min)) * PLOT_H;

/** The plotted series for one year: a running total for rain, else the stat. */
export function seriesValues(data, measure, stat) {
  if (measureOf(measure).cumulative) {
    let running = 0;
    return data[measure].sum.map((value) => (running += value ?? 0));
  }
  return data[measure][stat];
}

export const formatValue = (value, measure) =>
  value == null ? '—' : value.toFixed(measureOf(measure).digits);

/**
 * @param entries  [{ year, slot, data }] in the order they should be drawn
 * @param focus    year whose low–high band is shown (stat measures only)
 * @param measure  'tas' | 'hurs' | 'pr'
 * @param stat     'min' | 'mean' | 'max' — ignored when the measure has no stats
 */
export function buildModel({ entries, focus, measure, stat }) {
  if (entries.length === 0) return null;

  const spec = measureOf(measure);
  const focusEntry = entries.find((entry) => entry.year === focus) ?? entries[0];
  const maxDays = Math.max(...entries.map((entry) => entry.data.days));

  const plotted = entries.map((entry) => seriesValues(entry.data, measure, stat));

  // The band is the focus year's full low–high, so the domain has to cover it
  // even when the plotted stat is narrower. Rain has no band.
  const banded = spec.stats;
  const values = [
    ...plotted.flat(),
    ...(banded ? focusEntry.data[measure].min : []),
    ...(banded ? focusEntry.data[measure].max : []),
  ].filter((value) => value != null);

  // A running total starts at zero, so anchor the axis there rather than
  // floating it at the first day's rainfall.
  const scale = niceTicks(
    spec.cumulative ? 0 : Math.min(...values),
    Math.max(...values),
    5
  );
  const y = makeScale(scale);
  const x = (i) => xAt(i, maxDays);

  const linePath = (series) =>
    series.map((v, i) => `${i ? 'L' : 'M'}${r2(x(i))},${r2(y(v))}`).join('');

  let bandPath = '';
  const focusDays = focusEntry.data.days;
  if (banded) {
    for (let i = 0; i < focusDays; i++) {
      bandPath += `${i ? 'L' : 'M'}${r2(x(i))},${r2(y(focusEntry.data[measure].max[i]))}`;
    }
    for (let i = focusDays - 1; i >= 0; i--) {
      bandPath += `L${r2(x(i))},${r2(y(focusEntry.data[measure].min[i]))}`;
    }
    bandPath += 'Z';
  }

  // Month boundaries. Every year shares the same month/day at a given index,
  // so any entry can supply the labels.
  const spine = entries.find((entry) => entry.data.days === maxDays) ?? entries[0];
  const monthTicks = [];
  for (let i = 0; i < maxDays; i++) {
    const iso = addDays(spine.data.start, i);
    if (iso.endsWith('-01')) monthTicks.push({ x: r2(x(i)), label: monthOf(iso) });
  }

  const series = entries.map((entry, index) => {
    const lastIndex = entry.data.days - 1;
    return {
      year: entry.year,
      slot: entry.slot,
      isFocus: entry.year === focusEntry.year,
      days: entry.data.days,
      path: linePath(plotted[index]),
      endX: r2(x(lastIndex)),
      endY: r2(y(plotted[index][lastIndex])),
    };
  });

  return {
    measure,
    stat,
    suffix: spec.suffix,
    banded,
    cumulative: Boolean(spec.cumulative),
    maxDays,
    focus: focusEntry.year,
    focusStart: focusEntry.data.start,
    domain: scale,
    ticks: scale.ticks,
    monthTicks,
    bandPath,
    series,
    baselineY: PAD.top + PLOT_H,
  };
}

/** SVG inner markup. Every value here is a number or a year, so no escaping. */
export function renderSvg(model) {
  if (!model) {
    return `<text x="${W / 2}" y="${H / 2}" text-anchor="middle" class="empty">Select a year to plot</text>`;
  }

  const y = makeScale(model.domain);
  const focus = model.series.find((entry) => entry.isFocus);

  const grid = model.ticks
    .map(
      (tick) =>
        `<line x1="${PAD.left}" x2="${W - PAD.right}" y1="${r2(y(tick))}" y2="${r2(y(tick))}"/>`
    )
    .join('');

  const yTicks = model.ticks
    .map(
      (tick) =>
        `<text x="${PAD.left - 10}" y="${r2(y(tick)) + 4}" text-anchor="end">${tick}${model.suffix}</text>`
    )
    .join('');

  const xTicks = model.monthTicks
    .map((tick) => `<text x="${tick.x}" y="${H - 9}" text-anchor="middle">${tick.label}</text>`)
    .join('');

  const band = model.banded
    ? `<path class="band" style="--series:var(--series-${focus.slot})" d="${model.bandPath}"/>`
    : '';

  const lines = model.series
    .map(
      (entry) =>
        `<path class="line${entry.isFocus ? ' is-focus' : ''}" data-year="${entry.year}" ` +
        `style="--series:var(--series-${entry.slot})" d="${entry.path}"/>`
    )
    .join('');

  // Only the focus year is direct-labelled; the legend carries the rest, which
  // keeps five converging line-ends from colliding into noise.
  const endMark =
    `<circle class="end-dot" style="--series:var(--series-${focus.slot})" cx="${focus.endX}" cy="${focus.endY}" r="4"/>` +
    `<text class="end-label" x="${focus.endX + 10}" y="${focus.endY + 4}">${focus.year}</text>`;

  const dots = model.series
    .map((entry) => `<circle r="4" cx="0" cy="0" style="--series:var(--series-${entry.slot})"/>`)
    .join('');

  return (
    `<g class="grid">${grid}</g>` +
    band +
    lines +
    `<line class="baseline" x1="${PAD.left}" x2="${W - PAD.right}" y1="${model.baselineY}" y2="${model.baselineY}"/>` +
    `<g class="tick y">${yTicks}</g>` +
    `<g class="tick x">${xTicks}</g>` +
    `<g class="crosshair"><line y1="${PAD.top}" y2="${model.baselineY}" x1="0" x2="0"/>${dots}</g>` +
    endMark
  );
}
