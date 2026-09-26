<script>
  import { view, toggleFilterValue } from '../lib/state.svelte.js';
  import Section from './Section.svelte';

  let { facets } = $props();

  const f = $derived(view.filters);

  // A facet is worth showing when it can narrow the results, or when it is in use.
  const showList = (key) => (facets?.[key]?.length ?? 0) > 1 || f[key].length > 0;
  const showRange = (key) => {
    const r = facets?.[key];
    return (r && r.count > 0 && r.min !== r.max) || f[`${key}_min`] !== '' || f[`${key}_max`] !== '';
  };
  const showDate = $derived(
    (facets?.date.count > 0 && facets.date.min !== facets.date.max) || f.date_from !== '' || f.date_to !== ''
  );
  // Location: where the position came from, then countries and places (like lenses).
  const SOURCE_LABELS = [
    ['exif', 'Camera GPS'],
    ['visit', 'Timeline: stayed'],
    ['route', 'Timeline: on the move'],
    ['nearby', 'Timeline: nearby'],
    ['none', 'Unknown'],
  ];
  const sources = $derived(
    facets?.loc_source ? SOURCE_LABELS.filter(([v]) => facets.loc_source[v] > 0 || f.loc_source.includes(v)) : []
  );
  const showSources = $derived(sources.length > 1 || f.loc_source.length > 0);
  const PLACES_SHOWN = 15;
  let allPlaces = $state(false);
  const places = $derived(
    facets?.place ? (allPlaces ? facets.place : facets.place.slice(0, PLACES_SHOWN)) : []
  );
  const flagOptions = [
    ['pick', 'Picked'],
    ['reject', 'Rejected'],
    ['none', 'Unflagged'],
  ];
  const showFlag = $derived(
    (facets?.flag && facets.flag.pick + facets.flag.reject > 0) || f.flag.length > 0
  );

  const ranges = [
    { key: 'focal', label: 'Focal length', step: 1, span: (a, b) => `${a}–${b} mm` },
    { key: 'aperture', label: 'Aperture', step: 0.1, span: (a, b) => `f/${a}–${b}` },
    { key: 'iso', label: 'ISO', step: 1, span: (a, b) => `${a}–${b}` },
  ];
  const orientationLabels = { landscape: 'Landscape', portrait: 'Portrait', square: 'Square' };

  function setValue(key, value) {
    view.filters[key] = value.trim();
  }

  // "/photos/Sweden/day 1/" -> "day 1"
  const folderName = (key) => key.replace(/\/+$/, '').split('/').pop() || key;

  const fmt = (n) => (n == null ? '' : Number.isInteger(n) ? String(n) : n.toFixed(1));
</script>

{#snippet option(key, value, label, count)}
  {@const on = f[key].includes(value)}
  <li>
    <button
      class="flex w-full items-center justify-between rounded px-2 py-0.5 text-left transition-colors
        {on ? 'bg-sky-700 text-white' : 'text-neutral-300 hover:bg-neutral-800'}"
      aria-pressed={on}
      onclick={() => toggleFilterValue(key, value)}
    >
      <span class="truncate {value === '' && !on ? 'italic text-neutral-500' : ''}">{label}</span>
      <span class="ml-2 text-xs tabular-nums {on ? 'text-sky-100' : 'text-neutral-500'}">{count}</span>
    </button>
  </li>
{/snippet}

{#snippet dateInputs()}
  <div class="grid grid-cols-2 gap-1 px-2">
    <input
      type="date"
      aria-label="From date"
      value={f.date_from}
      min={facets.date.min}
      max={f.date_to || facets.date.max}
      onchange={(e) => setValue('date_from', e.currentTarget.value)}
      class="min-w-0 rounded border border-neutral-700 bg-neutral-950 px-1 py-0.5 text-xs outline-none focus:border-sky-600"
    />
    <input
      type="date"
      aria-label="To date"
      value={f.date_to}
      min={f.date_from || facets.date.min}
      max={facets.date.max}
      onchange={(e) => setValue('date_to', e.currentTarget.value)}
      class="min-w-0 rounded border border-neutral-700 bg-neutral-950 px-1 py-0.5 text-xs outline-none focus:border-sky-600"
    />
  </div>
{/snippet}

{#if facets}
  <div class="mb-2 border-b border-neutral-800 pb-2">
    {#if f.folder.length}
      <div class="mx-2 mb-1 flex items-center justify-between gap-2 rounded bg-sky-900/50 px-2 py-1 text-xs text-sky-100">
        <span class="truncate" title={f.folder.join(', ')}>Folder: {folderName(f.folder[0])}{f.folder.length > 1 ? ` +${f.folder.length - 1}` : ''}</span>
        <button class="shrink-0 hover:text-white" aria-label="Clear the folder filter" onclick={() => (view.filters.folder = [])}>✕</button>
      </div>
    {/if}

    {#if showFlag || (facets.exported && facets.exported.yes > 0) || f.exported !== ''}
      <Section id="flag" title="Flag" active={f.flag.length + (f.exported !== '' ? 1 : 0)}>
        {#if showFlag}
          <ul>
            {#each flagOptions as [value, label] (value)}
              {@render option('flag', value, label, facets.flag[value])}
            {/each}
          </ul>
        {/if}
        {#if (facets.exported && facets.exported.yes > 0) || f.exported !== ''}
          <div class="mt-1 grid grid-cols-2 gap-1 px-2">
            {#each [['true', 'Exported', facets.exported?.yes ?? 0], ['false', 'Not yet', facets.exported?.no ?? 0]] as [value, label, count] (value)}
              <button
                class="rounded border px-1 py-0.5 text-xs {f.exported === value
                  ? 'border-sky-700 bg-sky-700 text-white'
                  : 'border-neutral-700 text-neutral-300 hover:bg-neutral-800'}"
                aria-pressed={f.exported === value}
                onclick={() => (view.filters.exported = f.exported === value ? '' : value)}
              >
                {label} <span class="tabular-nums opacity-70">{count}</span>
              </button>
            {/each}
          </div>
        {/if}
      </Section>
    {/if}

    {#if showDate}
      <Section id="date" title="Date taken" active={(f.date_from ? 1 : 0) + (f.date_to ? 1 : 0)}>
        {@render dateInputs()}
      </Section>
    {/if}

    {#if showList('camera')}
      <Section id="camera" title="Camera" active={f.camera.length}>
        <ul>
          {#each facets.camera as c (c.value)}
            {@render option('camera', c.value, c.value || 'Unknown', c.count)}
          {/each}
        </ul>
      </Section>
    {/if}

    {#if showList('lens')}
      <Section id="lens" title="Lens" active={f.lens.length}>
        <ul>
          {#each facets.lens as l (l.value)}
            {@render option('lens', l.value, l.value || 'Unknown', l.count)}
          {/each}
        </ul>
      </Section>
    {/if}

    {#if ranges.some((r) => showRange(r.key))}
      <Section id="exposure" title="Exposure" active={ranges.reduce((n, r) => n + (f[`${r.key}_min`] !== '' ? 1 : 0) + (f[`${r.key}_max`] !== '' ? 1 : 0), 0)}>
        {#each ranges as r (r.key)}
          {#if showRange(r.key)}
            {@const avail = facets[r.key]}
            <p class="mb-1 mt-1.5 px-2 text-[11px] text-neutral-500">
              {avail.count ? `${r.label} (${r.span(fmt(avail.min), fmt(avail.max))})` : r.label}
            </p>
            <div class="grid grid-cols-2 gap-1 px-2">
              {#each ['min', 'max'] as end (end)}
                {@const k = `${r.key}_${end}`}
                <input
                  type="number"
                  inputmode="decimal"
                  step={r.step}
                  min="0"
                  aria-label="{r.label} {end}"
                  placeholder={end === 'min' ? `min ${fmt(avail.min)}` : `max ${fmt(avail.max)}`}
                  value={f[k]}
                  onchange={(e) => setValue(k, e.currentTarget.value)}
                  class="min-w-0 rounded border border-neutral-700 bg-neutral-950 px-1.5 py-0.5 text-xs tabular-nums outline-none placeholder:text-neutral-600 focus:border-sky-600"
                />
              {/each}
            </div>
          {/if}
        {/each}
      </Section>
    {/if}

    {#if showList('orientation')}
      <Section id="orientation" title="Orientation" active={f.orientation.length}>
        <ul>
          {#each facets.orientation as o (o.value)}
            {@render option('orientation', o.value, orientationLabels[o.value], o.count)}
          {/each}
        </ul>
      </Section>
    {/if}

    {#if showSources || showList('country')}
      <Section id="location" title="Location" active={f.loc_source.length + f.country.length + f.region.length}>
        {#if showSources}
          <ul>
            {#each sources as [value, label] (value)}
              {@render option('loc_source', value, label, facets.loc_source[value])}
            {/each}
          </ul>
        {/if}
        {#if showList('country')}
          <p class="mt-1.5 px-2 text-[11px] text-neutral-600">Country</p>
          <ul>
            {#each facets.country as c (c.value)}
              {@render option('country', c.value, c.label, c.count)}
            {/each}
          </ul>
        {/if}
      </Section>
    {/if}

    {#if showList('place')}
      <Section id="places" title="Places" active={f.place.length}>
        <ul>
          {#each places as pl (pl.value)}
            {@render option('place', pl.value, pl.label, pl.count)}
          {/each}
        </ul>
        {#if facets.place.length > PLACES_SHOWN}
          <button class="px-2 text-xs text-sky-400 hover:underline" onclick={() => (allPlaces = !allPlaces)}>
            {allPlaces ? 'Fewer' : `All ${facets.place.length} places`}
          </button>
        {/if}
      </Section>
    {/if}
  </div>
{/if}
