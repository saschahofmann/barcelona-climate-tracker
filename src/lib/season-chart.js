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
export const PAD = { top: 18, right: 72, bottom: 30, left: 46 };
export const PLOT_W = W - PAD.left - PAD.right;
export const PLOT_H = H - PAD.top - PAD.bottom;

export const MAX_YEARS = 5;

export const VARIABLES = [
  { key: 'mean', label: 'Mean' },
  { key: 'min', label: 'Minimum' },
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

/**
 * @param entries  [{ year, slot, data }] in the order they should be drawn
 * @param focus    year whose low–high band is shown
 * @param variable 'mean' | 'min' | 'max'
 */
export function buildModel({ entries, focus, variable }) {
  if (entries.length === 0) return null;

  const focusEntry = entries.find((entry) => entry.year === focus) ?? entries[0];
  const maxDays = Math.max(...entries.map((entry) => entry.data.days));

  // The band is always the focus year's full low–high, so the domain has to
  // cover it even when the plotted variable is narrower.
  const values = [
    ...entries.flatMap((entry) => entry.data[variable]),
    ...focusEntry.data.min,
    ...focusEntry.data.max,
  ];
  const scale = niceTicks(Math.min(...values), Math.max(...values), 5);
  const y = makeScale(scale);
  const x = (i) => xAt(i, maxDays);

  const linePath = (series) =>
    series.map((v, i) => `${i ? 'L' : 'M'}${r2(x(i))},${r2(y(v))}`).join('');

  let bandPath = '';
  const focusDays = focusEntry.data.days;
  for (let i = 0; i < focusDays; i++) {
    bandPath += `${i ? 'L' : 'M'}${r2(x(i))},${r2(y(focusEntry.data.max[i]))}`;
  }
  for (let i = focusDays - 1; i >= 0; i--) {
    bandPath += `L${r2(x(i))},${r2(y(focusEntry.data.min[i]))}`;
  }
  bandPath += 'Z';

  // Month boundaries. Every year shares the same month/day at a given index,
  // so any entry can supply the labels.
  const spine = entries.find((entry) => entry.data.days === maxDays) ?? entries[0];
  const monthTicks = [];
  for (let i = 0; i < maxDays; i++) {
    const iso = addDays(spine.data.start, i);
    if (iso.endsWith('-01')) monthTicks.push({ x: r2(x(i)), label: monthOf(iso) });
  }

  const series = entries.map((entry) => {
    const lastIndex = entry.data.days - 1;
    return {
      year: entry.year,
      slot: entry.slot,
      isFocus: entry.year === focusEntry.year,
      days: entry.data.days,
      path: linePath(entry.data[variable]),
      endX: r2(x(lastIndex)),
      endY: r2(y(entry.data[variable][lastIndex])),
    };
  });

  return {
    variable,
    maxDays,
    focus: focusEntry.year,
    focusStart: focusEntry.data.start,
    focusEnd: addDays(focusEntry.data.start, focusDays - 1),
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
        `<text x="${PAD.left - 10}" y="${r2(y(tick)) + 4}" text-anchor="end">${tick}°</text>`
    )
    .join('');

  const xTicks = model.monthTicks
    .map((tick) => `<text x="${tick.x}" y="${H - 9}" text-anchor="middle">${tick.label}</text>`)
    .join('');

  const lines = model.series
    .map(
      (entry) =>
        `<path class="line${entry.isFocus ? ' is-focus' : ''}" data-year="${entry.year}" ` +
        `style="--series:var(--series-${entry.slot})" d="${entry.path}"/>`
    )
    .join('');

  // Only the focus year is direct-labelled; the legend carries the rest, which
  // keeps five converging line-ends from colliding into noise.
  const endMark = focus
    ? `<circle class="end-dot" style="--series:var(--series-${focus.slot})" cx="${focus.endX}" cy="${focus.endY}" r="4"/>` +
      `<text class="end-label" x="${focus.endX + 10}" y="${focus.endY + 4}">${focus.year}</text>`
    : '';

  const dots = model.series
    .map((entry) => `<circle r="4" cx="0" cy="0" style="--series:var(--series-${entry.slot})"/>`)
    .join('');

  return (
    `<g class="grid">${grid}</g>` +
    `<path class="band" style="--series:var(--series-${focus.slot})" d="${model.bandPath}"/>` +
    lines +
    `<line class="baseline" x1="${PAD.left}" x2="${W - PAD.right}" y1="${model.baselineY}" y2="${model.baselineY}"/>` +
    `<g class="tick y">${yTicks}</g>` +
    `<g class="tick x">${xTicks}</g>` +
    `<g class="crosshair"><line y1="${PAD.top}" y2="${model.baselineY}" x1="0" x2="0"/>${dots}</g>` +
    endMark
  );
}
