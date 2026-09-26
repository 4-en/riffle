<script>
  // Map overview: photo clusters (one per place, region, or country) on offline country
  // outlines (Natural Earth, bundled; nothing is fetched from a map server).
  // Clicking a cluster opens that group in the grouped grid.
  import { untrack } from 'svelte';
  import { geoNaturalEarth1, geoPath } from 'd3-geo';
  import { select } from 'd3-selection';
  import { zoom as d3zoom, zoomIdentity } from 'd3-zoom';
  import { feature, mesh } from 'topojson-client';
  import { view, dateSpan } from '../lib/state.svelte.js';
  import { fetchGroups } from '../lib/api.js';

  let { onopen } = $props();

  let width = $state(800);
  let height = $state(600);
  let svg;
  let countries = $state(null); // GeoJSON features
  let borders = $state(null);
  let groups = $state([]);
  let error = $state('');
  let transform = $state(zoomIdentity);
  let hover = $state(null);

  const level = $derived(view.group);
  const placed = $derived(groups.filter((g) => g.key && g.lat != null));
  const unknown = $derived(groups.find((g) => !g.key)?.count ?? 0);

  // The outlines are loaded only when the map is first shown (a separate ~200 KB chunk).
  import('world-atlas/countries-50m.json').then((m) => {
    const topo = m.default;
    countries = feature(topo, topo.objects.countries).features;
    borders = mesh(topo, topo.objects.countries, (a, b) => a !== b);
  });

  $effect(() => {
    JSON.stringify(view.filters), view.tags, view.excludeTags, view.collapse, view.group;
    untrack(load);
  });

  async function load() {
    try {
      groups = (await fetchGroups(view, level)).groups;
      error = '';
      resetZoom();
    } catch (e) {
      error = e.message;
    }
  }

  // Fit the view to the photos (with some room around a single place).
  const projection = $derived.by(() => {
    const p = geoNaturalEarth1();
    if (!placed.length) return p.fitExtent([[20, 20], [width - 20, height - 20]], { type: 'Sphere' });
    const lats = placed.map((g) => g.lat), lons = placed.map((g) => g.lon);
    const pad = Math.max(2, (Math.max(...lats) - Math.min(...lats)) * 0.15, (Math.max(...lons) - Math.min(...lons)) * 0.15);
    const box = {
      type: 'MultiPoint',
      coordinates: [
        [Math.min(...lons) - pad, Math.min(...lats) - pad],
        [Math.max(...lons) + pad, Math.max(...lats) + pad],
      ],
    };
    return p.fitExtent([[40, 40], [width - 40, height - 40]], box);
  });
  const path = $derived(geoPath(projection));

  const maxCount = $derived(Math.max(1, ...placed.map((g) => g.count)));
  const radius = (n) => 5 + 18 * Math.sqrt(n / maxCount);

  // The view starts fitted to the photos; zooming out may go as far as the whole world.
  const minZoom = $derived.by(() => {
    const world = geoNaturalEarth1().fitExtent([[20, 20], [width - 20, height - 20]], { type: 'Sphere' });
    return Math.min(0.5, (world.scale() / projection.scale()) * 0.8);
  });

  let zoomer;
  $effect(() => {
    zoomer = d3zoom().on('zoom', (e) => (transform = e.transform));
    select(svg).call(zoomer);
    return () => select(svg).on('.zoom', null);
  });
  $effect(() => {
    zoomer?.scaleExtent([minZoom, 60]);
  });
  function resetZoom() {
    if (svg && zoomer) select(svg).call(zoomer.transform, zoomIdentity);
  }

  const levels = [
    ['place', 'Places'],
    ['region', 'Regions'],
    ['country', 'Countries'],
  ];
</script>

<div class="relative h-full min-h-[400px] w-full overflow-hidden bg-neutral-950" bind:clientWidth={width} bind:clientHeight={height}>
  <svg bind:this={svg} {width} {height} class="block cursor-grab active:cursor-grabbing" role="img" aria-label="Map of photo locations">
    <g transform={transform.toString()}>
      <path d={path({ type: 'Sphere' })} class="fill-neutral-900" />
      {#if countries}
        {#each countries as c, i (i)}
          <path d={path(c)} class="fill-neutral-800" />
        {/each}
        <path d={path(borders)} class="fill-none stroke-neutral-600" stroke-width={0.6 / transform.k} />
      {/if}
      {#each placed as g (g.key)}
        {@const [x, y] = projection([g.lon, g.lat])}
        <circle
          cx={x}
          cy={y}
          r={radius(g.count) / transform.k}
          class="cursor-pointer fill-sky-500/60 stroke-sky-200 hover:fill-sky-400/80"
          stroke-width={1.2 / transform.k}
          role="button"
          tabindex="0"
          aria-label="{g.label}: {g.count} photos"
          onmouseenter={() => (hover = { g, x: transform.applyX(x), y: transform.applyY(y) })}
          onmouseleave={() => (hover = null)}
          onclick={() => onopen(level, g.key)}
          onkeydown={(e) => e.key === 'Enter' && onopen(level, g.key)}
        />
      {/each}
    </g>
  </svg>

  <div class="absolute left-3 top-3 flex items-center gap-1 rounded bg-neutral-900/90 p-1 text-xs">
    {#each levels as [value, label] (value)}
      <button
        class="rounded px-2 py-1 {level === value ? 'bg-sky-700 text-white' : 'text-neutral-300 hover:bg-neutral-800'}"
        onclick={() => (view.group = value)}>{label}</button
      >
    {/each}
    <button class="rounded px-2 py-1 text-neutral-400 hover:bg-neutral-800" title="Fit to the photos" onclick={resetZoom}>Fit</button>
  </div>

  <div class="absolute bottom-3 left-3 rounded bg-neutral-900/90 px-2 py-1 text-xs text-neutral-400">
    {placed.length} {level === 'country' ? 'countries' : level === 'region' ? 'regions' : 'places'}
    {#if unknown}
      · <button class="text-sky-400 hover:underline" onclick={() => onopen(level, '')}>{unknown} without location</button>
    {/if}
    · outlines: Natural Earth
  </div>

  {#if error}
    <p class="absolute right-3 top-3 rounded bg-red-950 px-2 py-1 text-xs text-red-300">{error}</p>
  {/if}

  {#if hover}
    <div
      class="pointer-events-none absolute z-10 w-48 overflow-hidden rounded border border-neutral-700 bg-neutral-900 text-xs shadow-xl"
      style="left: {Math.min(hover.x + 12, width - 200)}px; top: {Math.min(hover.y + 12, height - 180)}px"
    >
      {#if hover.g.cover}<img src="/thumbs/{hover.g.cover}.jpg" alt="" class="h-28 w-full object-cover" />{/if}
      <div class="p-2">
        <p class="font-medium text-neutral-100">{hover.g.label}</p>
        <p class="text-neutral-400">{hover.g.count} photos · {dateSpan(hover.g.first, hover.g.last)}</p>
      </div>
    </div>
  {/if}
</div>
