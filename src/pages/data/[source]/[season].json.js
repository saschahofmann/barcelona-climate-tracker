import { SOURCES, loadSource } from '../../../lib/sources.js';

/**
 * One static JSON file per (source, season), so a page can load the season it
 * needs instead of inlining every year of every season.
 *
 * The long Fabra record is ~41k days; baking that into HTML would be several
 * hundred KB gzipped to draw a chart that shows five years at a time.
 */
export function getStaticPaths() {
  return Object.keys(SOURCES).flatMap((source) => {
    const { dataset } = loadSource(source);
    return Object.keys(dataset).map((season) => ({
      params: { source, season },
      props: { years: dataset[season].years },
    }));
  });
}

export function GET({ props }) {
  return new Response(JSON.stringify(props.years), {
    headers: {
      'content-type': 'application/json',
      // Content is immutable per build; the filename changes when data does.
      'cache-control': 'public, max-age=3600',
    },
  });
}
