// Level of detail for maps with many photos (SimilarMap): a grid over the layout to
// find the photos on screen quickly, and one representative per screen spot.

/** A G × G grid over layout coordinates in 0..1: point indices by cell, and within a
 *  cell by a fixed rank per photo (a hash of its id), so the same photo represents a
 *  spot every time. ``points``: [[id, x, y, key], …]. */
export function buildGrid(points, G = 128) {
  const n = points.length;
  const cell = new Int32Array(n);
  const start = new Int32Array(G * G + 1);
  for (let i = 0; i < n; i++) {
    const cx = Math.min(G - 1, Math.max(0, Math.floor(points[i][1] * G)));
    const cy = Math.min(G - 1, Math.max(0, Math.floor(points[i][2] * G)));
    cell[i] = cy * G + cx;
    start[cell[i] + 1]++;
  }
  for (let c = 0; c < G * G; c++) start[c + 1] += start[c];
  const rank = new Uint32Array(n);
  for (let i = 0; i < n; i++) rank[i] = Math.imul(points[i][0], 2654435761) >>> 0;
  const items = Int32Array.from({ length: n }, (_, i) => i).sort((a, b) => cell[a] - cell[b] || rank[a] - rank[b]);
  return { G, start, items };
}

/** The layout rectangle on screen (with a margin of ``px``) and the grid cells over it.
 *  ``view``: {k, tx, ty} (the zoom transform), {ox, oy, side} (the layout square at
 *  zoom 1), {width, height}. */
function visible(grid, view, px) {
  const { G } = grid;
  const { k, tx, ty, ox, oy, side, width, height } = view;
  const wx = (sx) => ((sx - tx) / k - ox) / side; // screen pixels to layout units
  const wy = (sy) => ((sy - ty) / k - oy) / side;
  const margin = px / (k * side);
  const r = { x0: wx(0) - margin, x1: wx(width) + margin, y0: wy(0) - margin, y1: wy(height) + margin };
  r.cx0 = Math.max(0, Math.floor(r.x0 * G));
  r.cx1 = Math.min(G - 1, Math.floor(r.x1 * G));
  r.cy0 = Math.max(0, Math.floor(r.y0 * G));
  r.cy1 = Math.min(G - 1, Math.floor(r.y1 * G));
  // Buckets: a power-of-two grid over the layout, so while panning at one zoom they stay put.
  r.b = 2 ** Math.ceil(Math.log2(px / (k * side)));
  return r;
}

/** Every point on screen (indices, by rank within each grid cell). Stops early past ``limit``. */
function onScreen(grid, points, r, limit = Infinity) {
  const { G, start, items } = grid;
  const out = [];
  for (let cy = r.cy0; cy <= r.cy1; cy++) {
    for (let cx = r.cx0; cx <= r.cx1; cx++) {
      const c = cy * G + cx;
      for (let j = start[c]; j < start[c + 1]; j++) {
        const i = items[j];
        const x = points[i][1], y = points[i][2];
        if (x < r.x0 || x > r.x1 || y < r.y0 || y > r.y1) continue;
        out.push(i);
        if (out.length > limit) return out;
      }
    }
  }
  return out;
}

/**
 * The points to draw, as [[index, x, y], …] in layout units.
 *
 * Points are drawn at the centres of spots of ``px`` screen pixels (a grid aligned
 * with the layout), so tiles never overlap. When at most ``all`` points are on screen,
 * every one is drawn: points that would share a spot (near-identical photos sit on
 * top of each other in the layout) take the nearest free spot around it, so none is
 * hidden. Otherwise one point per spot is drawn (a fixed rank per photo decides
 * which, so nothing changes while panning); the rest appear when zooming in.
 * ``only``: draw only this cluster's points (for drawing a hovered cluster on top).
 * The result's ``complete`` says whether every point on screen is in it.
 */
export function pointsToDraw(grid, points, view, px, all = 0, only = undefined) {
  const r = visible(grid, view, px);
  let idx = onScreen(grid, points, r, all); // (stops once past ``all``)
  if (only !== undefined) idx = onScreen(grid, points, r).filter((i) => points[i][3] === only);
  else if (idx.length > all) idx = onScreen(grid, points, r);
  const b = r.b;
  const key = (bx, by) => bx * 4194304 + by;
  if (idx.length <= all) {
    const taken = new Set();
    const out = [];
    for (const i of idx) {
      const x = points[i][1], y = points[i][2];
      const bx = Math.floor(x / b), by = Math.floor(y / b);
      let best = null;
      // The nearest free spot: rings around the point's own, closest first.
      for (let ring = 0; best === null; ring++) {
        let bestD = Infinity;
        for (let dx = -ring; dx <= ring; dx++) {
          for (let dy = -ring; dy <= ring; dy++) {
            if (Math.max(Math.abs(dx), Math.abs(dy)) !== ring || taken.has(key(bx + dx, by + dy))) continue;
            const cx = (bx + dx + 0.5) * b, cy = (by + dy + 0.5) * b;
            const d = (cx - x) ** 2 + (cy - y) ** 2;
            if (d < bestD) (bestD = d), (best = [bx + dx, by + dy]);
          }
        }
      }
      taken.add(key(best[0], best[1]));
      out.push([i, (best[0] + 0.5) * b, (best[1] + 0.5) * b]); // spots' centres: tiles never overlap
    }
    out.complete = true;
    return out;
  }
  const chosen = new Map();
  for (const i of idx) {
    const k = key(Math.floor(points[i][1] / b), Math.floor(points[i][2] / b));
    if (!chosen.has(k)) chosen.set(k, i);
  }
  const out = [...chosen].map(([k, i]) => [i, (Math.floor(points[i][1] / b) + 0.5) * b, (Math.floor(points[i][2] / b) + 0.5) * b]);
  out.complete = out.length === idx.length;
  return out;
}
