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

/**
 * The points to draw: one per bucket of ``px`` screen pixels, among the cells on
 * screen, preferring points of the cluster ``focus`` (undefined: none). Buckets are a
 * power-of-two grid over the layout, so while panning at one zoom they stay the same.
 * ``view``: {k, tx, ty} (the zoom transform), {ox, oy, side} (the layout square at
 * zoom 1), {width, height}. Returns point indices.
 */
export function representatives(grid, points, view, px, focus) {
  const { G, start, items } = grid;
  const { k, tx, ty, ox, oy, side, width, height } = view;
  const wx = (sx) => ((sx - tx) / k - ox) / side; // screen pixels to layout units
  const wy = (sy) => ((sy - ty) / k - oy) / side;
  const margin = px / (k * side);
  const x0 = wx(0) - margin, x1 = wx(width) + margin;
  const y0 = wy(0) - margin, y1 = wy(height) + margin;
  const b = 2 ** Math.ceil(Math.log2(px / (k * side))); // bucket size in layout units
  const cx0 = Math.max(0, Math.floor(x0 * G)), cx1 = Math.min(G - 1, Math.floor(x1 * G));
  const cy0 = Math.max(0, Math.floor(y0 * G)), cy1 = Math.min(G - 1, Math.floor(y1 * G));
  const chosen = new Map(); // bucket -> point index
  for (let cy = cy0; cy <= cy1; cy++) {
    for (let cx = cx0; cx <= cx1; cx++) {
      const c = cy * G + cx;
      for (let j = start[c]; j < start[c + 1]; j++) {
        const i = items[j];
        const p = points[i];
        const x = p[1], y = p[2];
        if (x < x0 || x > x1 || y < y0 || y > y1) continue;
        const bucket = Math.floor(x / b) * 4194304 + Math.floor(y / b);
        const had = chosen.get(bucket);
        if (had === undefined || (focus !== undefined && p[3] === focus && points[had][3] !== focus)) chosen.set(bucket, i);
      }
    }
  }
  return [...chosen.values()];
}
