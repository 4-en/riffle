// Grid layout maths for the virtualised grid (Grid.svelte), kept free of Svelte so
// it can be checked on its own. Positions are *visible* positions: the listing's
// offsets minus the photos hidden since loading (see listing.svelte.js).

export const GAP = 4; // between tiles, and the padding around each section's tiles
export const HEADER = 40; // a group header's height

/** Columns and tile size for a width, like CSS `repeat(auto-fill, minmax(min, 1fr))`. */
export function columns(width, minTile) {
  const inner = Math.max(0, width - 2 * GAP);
  const cols = Math.max(1, Math.floor((inner + GAP) / (minTile + GAP)));
  const tile = Math.max(1, (inner - (cols - 1) * GAP) / cols);
  return { cols, tile };
}

/**
 * Stack the sections: [{key, count, vstart}] (visible counts) → the same with
 * top, rows and height; `header` is false for an ungrouped listing.
 */
export function blocks(sections, cols, tile, header = true) {
  let top = 0;
  return sections.map((s) => {
    const rows = Math.ceil(s.count / cols);
    const height = (header ? HEADER : 0) + GAP + rows * (tile + GAP);
    const b = { ...s, top, rows, height, rowsTop: top + (header ? HEADER : 0) + GAP };
    top += height;
    return b;
  });
}

export const totalHeight = (bs) => (bs.length ? bs.at(-1).top + bs.at(-1).height : 0);

/** The last block starting at or above y (binary search). */
export function blockAt(bs, y) {
  let lo = 0, hi = bs.length - 1, found = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (bs[mid].top <= y) {
      found = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return found;
}

/** The block holding visible position v. */
export function blockOf(bs, v) {
  let lo = 0, hi = bs.length - 1, found = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (bs[mid].vstart <= v) {
      found = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  // Skip empty sections that share the start.
  while (found < bs.length - 1 && v >= bs[found].vstart + bs[found].count) found++;
  return found;
}

/** Rows between y0 and y1: [{block, row, top, from, to}] with visible positions from..to (exclusive). */
export function rowsBetween(bs, cols, tile, y0, y1) {
  const out = [];
  if (!bs.length) return out;
  const step = tile + GAP;
  for (let i = blockAt(bs, Math.max(0, y0)); i < bs.length && bs[i].top < y1; i++) {
    const b = bs[i];
    const first = Math.max(0, Math.floor((y0 - b.rowsTop) / step));
    const last = Math.min(b.rows - 1, Math.floor((y1 - b.rowsTop) / step));
    for (let r = first; r <= last; r++) {
      const from = b.vstart + r * cols;
      out.push({ block: i, row: r, top: b.rowsTop + r * step, from, to: Math.min(from + cols, b.vstart + b.count) });
    }
  }
  return out;
}

/** The top of the row holding visible position v. */
export function rowTop(bs, cols, tile, v) {
  const b = bs[blockOf(bs, v)];
  if (!b) return 0;
  return b.rowsTop + Math.floor((v - b.vstart) / cols) * (tile + GAP);
}

/** Arrow keys: the visible position after moving from v (or v itself at an edge). */
export function move(bs, cols, v, dir) {
  const total = bs.length ? bs.at(-1).vstart + bs.at(-1).count : 0;
  if (!total) return -1;
  if (dir === 'left') return Math.max(0, v - 1);
  if (dir === 'right') return Math.min(total - 1, v + 1);
  const i = blockOf(bs, v);
  const b = bs[i];
  const local = v - b.vstart;
  const row = Math.floor(local / cols), col = local % cols;
  if (dir === 'down') {
    if (row + 1 < b.rows) return Math.min(b.vstart + (row + 1) * cols + col, b.vstart + b.count - 1);
    for (let j = i + 1; j < bs.length; j++) if (bs[j].count) return bs[j].vstart + Math.min(col, bs[j].count - 1);
    return v;
  }
  if (row > 0) return b.vstart + (row - 1) * cols + col;
  for (let j = i - 1; j >= 0; j--) {
    const p = bs[j];
    if (p.count) return Math.min(p.vstart + (p.rows - 1) * cols + col, p.vstart + p.count - 1);
  }
  return v;
}

/** Visible positions of the tiles a rectangle (grid coordinates) touches. */
export function hits(bs, cols, tile, x0, y0, x1, y1) {
  const step = tile + GAP;
  const c0 = Math.max(0, Math.floor((Math.min(x0, x1) - GAP) / step));
  const c1 = Math.min(cols - 1, Math.floor((Math.max(x0, x1) - GAP) / step));
  const left = Math.min(x0, x1), right = Math.max(x0, x1);
  const top = Math.min(y0, y1), bottom = Math.max(y0, y1);
  const out = [];
  for (const r of rowsBetween(bs, cols, tile, top, bottom)) {
    if (r.top + tile <= top || r.top >= bottom) continue;
    for (let c = c0; c <= c1; c++) {
      const x = GAP + c * step;
      if (x + tile <= left || x >= right) continue;
      if (r.from + c < r.to) out.push(r.from + c);
    }
  }
  return out;
}

// ---- hidden photos: a sorted array of listing offsets ------------------------------

/** How many hidden offsets are below o. */
export function hiddenBefore(hidden, o) {
  let lo = 0, hi = hidden.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (hidden[mid] < o) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

export const isHidden = (hidden, o) => {
  const i = hiddenBefore(hidden, o);
  return hidden[i] === o;
};

/** The listing offset of visible position v. */
export function offsetAt(hidden, v) {
  let o = v;
  for (const h of hidden) {
    if (h <= o) o++;
    else break;
  }
  return o;
}
