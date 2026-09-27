// Justified layout: photos in their order, in rows of equal height that fill the
// width exactly, so a set of mixed portrait and landscape photos reads as one
// block. Where the rows break is chosen for the whole set at once (dynamic
// programming, like line breaking in text), so no row ends up far taller or
// flatter than the others; filling rows greedily would leave odd last rows.

/** The aspect ratio a photo is laid out with: very long panoramas and tall strips
 * are limited (and cropped to it), so they cannot squeeze a whole row. */
export const layoutAspect = (width, height) => Math.min(Math.max(width && height ? width / height : 1.5, 0.4), 3);

/**
 * @param {number[]} aspects width / height of each photo, in order
 * @param {number} width the width to fill (px)
 * @param {{target?: number, gap?: number, min?: number, max?: number}} options
 *   target: the row height to aim for; min/max: allowed range as a factor of it
 * @returns {{start: number, end: number, height: number, full: boolean}[]} rows
 *   (photos start..end-1); full = the row spans the width (the last one may not)
 */
export function justify(aspects, width, { target = 300, gap = 8, min = 0.6, max = 1.6 } = {}) {
  const n = aspects.length;
  if (!n || width <= 0) return [];
  const a = aspects.map((r) => Math.min(Math.max(r || 1, 0.4), 3)); // as layoutAspect
  const lo = target * min;
  const hi = target * max;

  // best[i]: the lowest cost for laying out the first i photos; from[i]: where its last row starts.
  const best = new Array(n + 1).fill(Infinity);
  const from = new Array(n + 1).fill(0);
  best[0] = 0;
  for (let end = 1; end <= n; end++) {
    let sum = 0;
    for (let start = end - 1; start >= 0; start--) {
      sum += a[start];
      const count = end - start;
      const h = (width - gap * (count - 1)) / sum;
      if (h < lo && count > 1) break; // adding more photos only makes the row flatter
      let cost;
      if (h <= hi) {
        const d = (h - target) / target;
        cost = d * d * count; // deviations weigh more in rows with more photos
      } else if (end === n) {
        // Too few photos left to fill a row: keep them at the target height; the
        // empty space counts against it, so this is only chosen when needed.
        const empty = 1 - (target * sum + gap * (count - 1)) / width;
        cost = 0.5 + empty * empty * 4;
      } else {
        continue; // far too tall
      }
      if (best[start] + cost < best[end]) {
        best[end] = best[start] + cost;
        from[end] = start;
      }
    }
    if (best[end] === Infinity) {
      // Nothing fits the range (e.g. a very narrow window): a row of its own.
      best[end] = best[end - 1] + 1;
      from[end] = end - 1;
    }
  }

  const rows = [];
  for (let end = n; end > 0; end = from[end]) {
    const start = from[end];
    const sum = a.slice(start, end).reduce((s, r) => s + r, 0);
    const fill = (width - gap * (end - start - 1)) / sum;
    const short = fill > hi; // only a last row: too few photos to fill the width
    rows.unshift({ start, end, height: short ? target : fill, full: !short });
  }
  return rows;
}
