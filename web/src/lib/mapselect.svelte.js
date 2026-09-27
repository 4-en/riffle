// Box and lasso selection on the maps (world map and Similar map). The map decides
// what lies inside the shape; this handles the drag and draws the shape.
//
// Tools: 'pan' (drag pans; Shift-drag still draws a box), 'box', 'lasso'.
// Ctrl/Cmd while releasing adds to the current selection instead of replacing it.

/** Is (x, y) inside the polygon [[x, y], …]? (Ray casting.) */
export function inside(poly, x, y) {
  let hit = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) hit = !hit;
  }
  return hit;
}

/**
 * @param {{tool: () => string, onselect: (poly: number[][], additive: boolean) => void}} opts
 * Returns pointer handlers for the map's container, the shape being drawn (SVG path
 * data, or '') and whether a click should be ignored (it ended a selection drag).
 */
export function mapSelect(opts) {
  const drag = $state({ mode: null, points: [] });
  let justSelected = false;

  const local = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  };

  function box(a, b) {
    return [a, [b[0], a[1]], b, [a[0], b[1]]];
  }

  return {
    /** A zoom filter: let d3-zoom pan only when not selecting (the wheel always zooms). */
    zoomFilter: (e) => e.type === 'wheel' || (opts.tool() === 'pan' && !e.shiftKey && !e.button && !e.ctrlKey),

    onpointerdown(e) {
      if (e.button !== 0 || e.target.closest?.('button, a, input, select')) return;
      const tool = opts.tool();
      const mode = tool !== 'pan' ? tool : e.shiftKey ? 'box' : null;
      if (!mode) return;
      e.preventDefault();
      e.currentTarget.setPointerCapture(e.pointerId);
      drag.mode = mode;
      drag.points = [local(e)];
    },

    onpointermove(e) {
      if (!drag.mode) return;
      const p = local(e);
      if (drag.mode === 'box') drag.points = [drag.points[0], p];
      else drag.points = [...drag.points, p];
    },

    onpointerup(e) {
      if (!drag.mode) return;
      const pts = drag.points;
      const poly = drag.mode === 'box' ? box(pts[0], pts.at(-1)) : pts;
      drag.mode = null;
      drag.points = [];
      const xs = poly.map((p) => p[0]), ys = poly.map((p) => p[1]);
      if (Math.max(...xs) - Math.min(...xs) < 4 && Math.max(...ys) - Math.min(...ys) < 4) return; // a click
      justSelected = true;
      setTimeout(() => (justSelected = false), 0);
      opts.onselect(poly, e.ctrlKey || e.metaKey);
    },

    /** True right after a selection drag (the click it produces is not a click on a photo). */
    get consumedClick() {
      return justSelected;
    },

    /** The shape being drawn, as SVG path data. */
    get path() {
      if (!drag.mode || drag.points.length < 2) return '';
      const poly = drag.mode === 'box' ? box(drag.points[0], drag.points.at(-1)) : drag.points;
      return 'M' + poly.map((p) => p.join(',')).join('L') + 'Z';
    },
  };
}
