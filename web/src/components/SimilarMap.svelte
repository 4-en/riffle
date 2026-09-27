<script>
  // Map overview for the Similar grouping: every photo of the current view placed so
  // that alike photos sit close together (t-SNE of the library, laid out once, so
  // places stay put when filters change), coloured by its cluster. Zoom in and the
  // dots become thumbnails. Click a photo to open it, a cluster name to open that
  // group in the grid. Box or lasso selects photos for the selection bar.
  import { untrack } from 'svelte';
  import { select } from 'd3-selection';
  import { zoom as d3zoom, zoomIdentity } from 'd3-zoom';
  import { view } from '../lib/state.svelte.js';
  import { fetchSimilarMap } from '../lib/api.js';
  import { selection } from '../lib/culling.svelte.js';
  import { mapSelect, inside } from '../lib/mapselect.svelte.js';
  import MapTools from './MapTools.svelte';
  import SelectionBar from './SelectionBar.svelte';

  let { onopen } = $props();

  let width = $state(800);
  let height = $state(600);
  let canvas;
  let data = $state(null); // {points: [[id, x, y, key]], groups: [{key, label, count, x, y}], other}
  let loading = $state(true);
  let error = $state('');
  let transform = $state(zoomIdentity);
  let hover = $state(null); // {id, key, x, y}
  let tool = $state('pan');

  // Box / lasso: the photos whose dot or tile centre lies inside the shape.
  const picker = mapSelect({
    tool: () => tool,
    onselect(poly, additive) {
      if (!additive) selection.clear();
      for (const [id, x, y] of data?.points ?? []) {
        const [sx, sy] = at(x, y);
        if (inside(poly, sx, sy)) selection.add(id);
      }
    },
  });
  function selectAll() {
    for (const [id] of data?.points ?? []) selection.add(id);
  }

  const PAD = 40;
  const THUMB_FROM = 20; // tile size (px) from which thumbnails replace the dots
  const LABELS = 24; // cluster names shown when zoomed out (largest first)
  const PALETTE = ['#38bdf8', '#f472b6', '#a3e635', '#fbbf24', '#c084fc', '#34d399', '#fb923c', '#60a5fa', '#f87171', '#2dd4bf', '#e879f9', '#facc15'];

  $effect(() => {
    JSON.stringify(view.filters), view.tags, view.excludeTags, view.ctags, view.excludeCtags, view.collapse, view.level;
    untrack(load);
  });

  async function load() {
    loading = true;
    try {
      data = await fetchSimilarMap(view);
      error = '';
    } catch (e) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  // Layout coordinates (0..1, one scale for both axes) to canvas pixels at zoom 1.
  const frame = $derived.by(() => {
    const side = Math.max(50, Math.min(width, height) - 2 * PAD);
    return { side, ox: (width - side) / 2, oy: (height - side) / 2 };
  });
  const colour = $derived(new Map((data?.groups ?? []).map((g, i) => [g.key, PALETTE[i % PALETTE.length]])));
  const labels = $derived.by(() => {
    const gs = [...(data?.groups ?? [])].sort((a, b) => b.count - a.count);
    return transform.k >= 3 ? gs : gs.slice(0, LABELS);
  });

  const at = (x, y) => [transform.applyX(frame.ox + x * frame.side), transform.applyY(frame.oy + y * frame.side)];
  // Tiles grow more slowly than the spacing, so zooming in spreads them out.
  const tileSize = (k) => Math.min(140, 7 * Math.pow(k, 0.75));

  const images = new Map(); // id -> Image (loaded on demand)
  let frameRequest = 0;
  function redraw() {
    cancelAnimationFrame(frameRequest);
    frameRequest = requestAnimationFrame(draw);
  }

  function draw() {
    if (!canvas || !data) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    const s = tileSize(transform.k);
    for (const [id, x, y, key] of data.points) {
      const [sx, sy] = at(x, y);
      if (sx < -s || sy < -s || sx > width + s || sy > height + s) continue;
      if (s >= THUMB_FROM) {
        let img = images.get(id);
        if (!img) {
          img = new Image();
          img.onload = redraw;
          img.src = `/thumbs/${id}.jpg`;
          images.set(id, img);
        }
        if (img.complete && img.naturalWidth) {
          const r = img.naturalWidth / img.naturalHeight;
          const w = r >= 1 ? s : s * r;
          const h = r >= 1 ? s / r : s;
          ctx.drawImage(img, sx - w / 2, sy - h / 2, w, h);
          if (selection.has(id)) {
            ctx.strokeStyle = '#38bdf8';
            ctx.lineWidth = 3;
            ctx.strokeRect(sx - w / 2, sy - h / 2, w, h);
          } else if (hover?.id === id) {
            ctx.strokeStyle = '#fff';
            ctx.lineWidth = 2;
            ctx.strokeRect(sx - w / 2, sy - h / 2, w, h);
          }
          continue;
        }
      }
      const d = Math.max(3, s * 0.7);
      ctx.fillStyle = key ? colour.get(key) : '#525252';
      ctx.globalAlpha = 0.85;
      ctx.fillRect(sx - d / 2, sy - d / 2, d, d);
      ctx.globalAlpha = 1;
      if (selection.has(id)) {
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 1.5;
        ctx.strokeRect(sx - d / 2 - 1.5, sy - d / 2 - 1.5, d + 3, d + 3);
      }
    }
  }

  $effect(() => {
    data, transform, width, height, hover, selection.size;
    untrack(redraw);
  });

  let zoomer;
  $effect(() => {
    zoomer = d3zoom()
      .scaleExtent([0.5, 40])
      .filter(picker.zoomFilter)
      .on('zoom', (e) => (transform = e.transform));
    select(canvas).call(zoomer);
    return () => select(canvas).on('.zoom', null);
  });
  function fit() {
    if (canvas && zoomer) select(canvas).call(zoomer.transform, zoomIdentity);
  }

  // The photo under the pointer (the nearest within its tile).
  function pick(e) {
    if (!data) return null;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    const reach = Math.max(6, tileSize(transform.k) / 2 + 2);
    let best = null, bestD = reach * reach;
    for (const [id, x, y, key] of data.points) {
      const [sx, sy] = at(x, y);
      const d = (sx - mx) ** 2 + (sy - my) ** 2;
      if (d < bestD) {
        bestD = d;
        best = { id, key, x: sx, y: sy };
      }
    }
    return best;
  }
  const groupLabel = (key) => data?.groups.find((g) => g.key === key)?.label ?? 'Other';
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
  class="relative h-full min-h-[400px] w-full overflow-hidden bg-neutral-950"
  bind:clientWidth={width}
  bind:clientHeight={height}
  onpointerdown={picker.onpointerdown}
  onpointermove={picker.onpointermove}
  onpointerup={picker.onpointerup}
>
  <canvas
    bind:this={canvas}
    style="width: {width}px; height: {height}px"
    class="block {tool !== 'pan' ? 'cursor-crosshair' : hover ? 'cursor-pointer' : 'cursor-grab active:cursor-grabbing'}"
    aria-label="Map of the photos by similarity"
    onmousemove={(e) => (hover = pick(e))}
    onmouseleave={() => (hover = null)}
    onclick={(e) => {
      if (picker.consumedClick || tool !== 'pan') return;
      const p = pick(e);
      if (p) view.photo = p.id;
    }}
  ></canvas>

  {#if picker.path}
    <svg class="pointer-events-none absolute inset-0" {width} {height}>
      <path d={picker.path} class="fill-sky-400/10 stroke-sky-300" stroke-width="1.5" stroke-dasharray="5 3" />
    </svg>
  {/if}

  {#if data}
    {#each labels as g (g.key)}
      {@const [lx, ly] = at(g.x, g.y)}
      {#if lx > 0 && ly > 0 && lx < width && ly < height}
        <button
          class="absolute -translate-x-1/2 -translate-y-1/2 whitespace-nowrap rounded bg-black/70 px-1.5 py-0.5 text-[11px] font-medium hover:bg-black"
          style="left: {lx}px; top: {ly}px; color: {colour.get(g.key)}"
          title="Show these {g.count} photos in the grid"
          onclick={() => onopen('similar', g.key)}>{g.label} · {g.count}</button
        >
      {/if}
    {/each}
  {/if}

  <!-- Broad · Medium · Fine is in the toolbar above. -->
  <div class="absolute left-3 top-3 flex items-center gap-1 rounded bg-neutral-900/90 p-1 text-xs">
    <MapTools bind:tool />
    <button class="rounded px-2 py-1 text-neutral-400 hover:bg-neutral-800" title="Fit to the photos" onclick={fit}>Fit</button>
  </div>

  {#if data}
    <div class="absolute bottom-3 left-3 rounded bg-neutral-900/90 px-2 py-1 text-xs text-neutral-400">
      {data.points.length} photos in {data.groups.length} groups
      {#if data.other}
        · <button class="text-sky-400 hover:underline" onclick={() => onopen('similar', '')}>{data.other} in Other</button>
      {/if}
      · zoom in for thumbnails
    </div>
  {/if}

  <div class="pointer-events-none absolute inset-x-0 bottom-10">
    <SelectionBar onselectall={selectAll} />
  </div>

  {#if loading && !data}
    <p class="absolute inset-0 flex items-center justify-center text-sm text-neutral-500">Laying out the photos… (the first time takes a few seconds)</p>
  {/if}
  {#if error}
    <p class="absolute right-3 top-3 rounded bg-red-950 px-2 py-1 text-xs text-red-300">{error}</p>
  {/if}

  {#if hover}
    <div
      class="pointer-events-none absolute z-10 w-44 overflow-hidden rounded border border-neutral-700 bg-neutral-900 text-xs shadow-xl"
      style="left: {Math.min(hover.x + 14, width - 190)}px; top: {Math.min(hover.y + 14, height - 170)}px"
    >
      <img src="/thumbs/{hover.id}.jpg" alt="" class="h-28 w-full object-cover" />
      <p class="p-1.5 font-medium" style="color: {hover.key ? colour.get(hover.key) : '#a3a3a3'}">{groupLabel(hover.key)}</p>
    </div>
  {/if}
</div>
