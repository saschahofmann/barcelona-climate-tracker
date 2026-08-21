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

/**
 * How much detail to plot. All of this is arithmetic on the daily arrays the
 * page already holds — no different data is fetched for any of it.
 *
 * `smooth` is a centred rolling mean that keeps one point per day; `bucket`
 * groups whole days and collapses each group. They look similar at 7 days and
 * are not: smoothing keeps 365 points and the shape, bucketing gives 52 and
 * shifts the extremes inward.
 */
export const DETAILS = [
  { key: 'daily', label: 'Daily' },
  { key: 'smooth7', label: '7-day average', smooth: 7 },
  { key: 'weekly', label: 'Weekly', bucket: 7 },
  { key: 'monthly', label: 'Monthly', bucket: 'month' },
];

export const detailOf = (key) => DETAILS.find((d) => d.key === key) ?? DETAILS[0];

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
 * Centred rolling mean. A window that is more than half empty yields null
 * rather than an average of whatever happened to survive, so a station outage
 * stays a hole instead of being papered over.
 */
export function smoothSeries(values, window) {
  const half = Math.floor(window / 2);
  return values.map((_, i) => {
    let sum = 0;
    let seen = 0;
    for (let j = i - half; j <= i + half; j++) {
      const v = values[j];
      if (j >= 0 && j < values.length && v != null) {
        sum += v;
        seen += 1;
      }
    }
    return seen > window / 2 ? sum / seen : null;
  });
}

/**
 * Group whole days and collapse each group. `how` follows the statistic's own
 * meaning: a weekly maximum is the hottest day of that week, not the average of
 * its afternoons — which keeps the low–high band an actual envelope.
 */
export function bucketSeries(values, dates, bucket, how) {
  const groups = new Map();
  values.forEach((value, i) => {
    const iso = dates[i];
    const key =
      bucket === 'month' ? iso.slice(0, 7) : Math.floor(i / bucket);
    if (!groups.has(key)) groups.set(key, { at: i, values: [] });
    if (value != null) groups.get(key).values.push(value);
  });

  const out = [];
  for (const { at, values: group } of groups.values()) {
    let value = null;
    if (group.length) {
      if (how === 'min') value = Math.min(...group);
      else if (how === 'max') value = Math.max(...group);
      else if (how === 'sum') value = group.reduce((a, b) => a + b, 0);
      else value = group.reduce((a, b) => a + b, 0) / group.length;
    }
    out.push({ value, at });
  }
  return out;
}

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
 * Re-express one year's daily arrays at the requested detail, and hand back the
 * dates that go with them. Everything downstream — paths, band, tooltip, table —
 * reads this view, so nothing else has to know whether it is looking at days,
 * weeks or months.
 */
export function applyDetail(data, measure, detail) {
  const spec = measureOf(measure);
  const mode = detailOf(detail);
  // The year view supplies its own dates: its slots skip 29 February, so they
  // are not simply `start + i`.
  const dates =
    data.dates ?? Array.from({ length: data.days }, (_, i) => addDays(data.start, i));
  const fields = spec.stats ? ['min', 'mean', 'max'] : ['sum'];

  // Smoothing a running total is meaningless — the curve is monotonic already —
  // so rain falls back to daily rather than pretending to offer it.
  const smoothing = mode.smooth && !spec.cumulative;
  if (!smoothing && !mode.bucket) return { ...data, dates };

  if (smoothing) {
    const out = {};
    for (const f of fields) out[f] = smoothSeries(data[measure][f], mode.smooth);
    return { ...data, dates, [measure]: out };
  }

  const out = {};
  let at = null;
  for (const f of fields) {
    // A weekly minimum is the coldest night of that week, not the average one;
    // rain is a total. Each field collapses by its own meaning.
    const how = spec.cumulative ? 'sum' : f;
    const grouped = bucketSeries(data[measure][f], dates, mode.bucket, how);
    out[f] = grouped.map((g) => g.value);
    at ??= grouped.map((g) => dates[g.at]);
  }
  return {
    ...data,
    days: at.length,
    start: at[0],
    dates: at,
    [measure]: out,
  };
}

/**
 * @param entries  [{ year, slot, data }] in the order they should be drawn
 * @param focus    year whose low–high band is shown, or null for no band
 * @param measure  'tas' | 'pr'
 * @param stat     'min' | 'mean' | 'max' — ignored when the measure has no stats
 * @param detail   'daily' | 'smooth7' | 'weekly' | 'monthly'
 */
export function buildModel({ entries, focus, measure, stat, detail = 'daily' }) {
  if (entries.length === 0) return null;

  const spec = measureOf(measure);
  const views = entries.map((entry) => applyDetail(entry.data, measure, detail));

  // Focus is genuinely optional: clicking the front year clears it, leaving
  // every series plotted with no band at all.
  const focusIndex = focus == null ? -1 : entries.findIndex((e) => e.year === focus);
  const focusView = focusIndex >= 0 ? views[focusIndex] : null;
  const maxDays = Math.max(...views.map((view) => view.days));

  const plotted = views.map((view) => seriesValues(view, measure, stat));

  // The band is the focus year's full low–high, so the domain has to cover it
  // even when the plotted stat is narrower. Rain has no band, nor does an
  // unfocused chart.
  const banded = spec.stats && focusView !== null;
  const values = [
    ...plotted.flat(),
    ...(banded ? focusView[measure].min : []),
    ...(banded ? focusView[measure].max : []),
  ].filter((value) => value != null && Number.isFinite(value));

  // Every selected year could be nothing but gaps for this measure.
  if (values.length === 0) return null;

  // A running total starts at zero, so anchor the axis there rather than
  // floating it at the first period's rainfall.
  const scale = niceTicks(spec.cumulative ? 0 : Math.min(...values), Math.max(...values), 5);
  const y = makeScale(scale);
  const x = (i) => xAt(i, maxDays);

  // Records have gaps, so a null lifts the pen and the line resumes at the next
  // reading rather than drawing a straight lie across the outage.
  const linePath = (series) => {
    let path = '';
    let pen = 'M';
    series.forEach((value, i) => {
      if (value == null) {
        pen = 'M';
        return;
      }
      path += `${pen}${r2(x(i))},${r2(y(value))}`;
      pen = 'L';
    });
    return path;
  };

  let bandPath = '';
  if (banded) {
    const highs = focusView[measure].max;
    const lows = focusView[measure].min;

    // One closed subpath per unbroken run, so a gap splits the band instead of
    // closing it across the missing periods.
    const runs = [];
    let run = [];
    for (let i = 0; i < focusView.days; i++) {
      if (highs[i] == null || lows[i] == null) {
        if (run.length) runs.push(run);
        run = [];
      } else {
        run.push(i);
      }
    }
    if (run.length) runs.push(run);

    bandPath = runs
      .map((indices) => {
        let d = '';
        indices.forEach((i, k) => {
          d += `${k ? 'L' : 'M'}${r2(x(i))},${r2(y(highs[i]))}`;
        });
        for (let k = indices.length - 1; k >= 0; k--) {
          d += `L${r2(x(indices[k]))},${r2(y(lows[indices[k]]))}`;
        }
        return `${d}Z`;
      })
      .join('');
  }

  // Month boundaries, taken from whichever view spans the most of the period.
  const spine = views.find((view) => view.days === maxDays) ?? views[0];
  const monthTicks = [];
  let lastMonth = null;
  spine.dates.forEach((iso, i) => {
    const month = iso.slice(0, 7);
    if (month !== lastMonth) {
      monthTicks.push({ x: r2(x(i)), label: monthOf(iso) });
      lastMonth = month;
    }
  });

  // The end marker rides the last *reading*, not the last slot, when a record
  // trails off into missing periods.
  const lastReading = (series) => {
    for (let i = series.length - 1; i >= 0; i--) {
      if (series[i] != null) return i;
    }
    return -1;
  };

  const series = entries.map((entry, index) => {
    const view = views[index];
    const lastIndex = lastReading(plotted[index]);
    return {
      year: entry.year,
      slot: entry.slot,
      isFocus: focusView !== null && index === focusIndex,
      days: view.days,
      dates: view.dates,
      // Carried on the model so the tooltip and table never re-derive them and
      // never disagree with what was drawn.
      values: plotted[index],
      low: spec.stats ? view[measure].min : null,
      high: spec.stats ? view[measure].max : null,
      path: linePath(plotted[index]),
      hasData: lastIndex >= 0,
      endX: lastIndex >= 0 ? r2(x(lastIndex)) : 0,
      endY: lastIndex >= 0 ? r2(y(plotted[index][lastIndex])) : 0,
    };
  });

  return {
    measure,
    stat,
    detail,
    suffix: spec.suffix,
    banded,
    cumulative: Boolean(spec.cumulative),
    maxDays,
    focus: focusView === null ? null : entries[focusIndex].year,
    dates: spine.dates,
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
  // keeps five converging line-ends from colliding into noise. With no focus
  // there is nothing to single out, so the legend carries identity alone.
  const endMark =
    focus && focus.hasData
      ? `<circle class="end-dot" style="--series:var(--series-${focus.slot})" cx="${focus.endX}" cy="${focus.endY}" r="4"/>` +
        `<text class="end-label" x="${focus.endX + 10}" y="${focus.endY + 4}">${focus.year}</text>`
      : '';

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
