<script>
  import { view, findSimilar, DATE_GROUPS, LOCATION_GROUPS } from '../lib/state.svelte.js';
  import { fetchPhoto } from '../lib/api.js';
  import { culling, flagOf, saveSetting } from '../lib/culling.svelte.js';

  // onflag(flag): flag this photo (App handles auto-advance).
  let { id, hasPrev, hasNext, onstep, ontimeline, onflag } = $props();

  let photo = $state(null);
  const flag = $derived(photo ? flagOf(photo) : null);
  // The grouping level each "Show" button uses: the current one of that kind, or the finest.
  const dateMode = $derived(DATE_GROUPS.includes(view.group) ? view.group : 'day');
  const placeMode = $derived(LOCATION_GROUPS.includes(view.group) ? view.group : 'place');
  const SOURCES = {
    exif: 'camera GPS',
    visit: 'timeline: stayed here',
    route: 'timeline: estimated along the route',
    nearby: 'timeline: nearest position',
  };
  const accuracy = (m) => (m == null ? '' : m >= 1000 ? `±${(m / 1000).toFixed(1)} km` : `±${Math.round(m)} m`);
  let error = $state('');
  let copied = $state(false);

  $effect(() => {
    const current = id;
    error = '';
    fetchPhoto(current)
      .then((p) => {
        if (current === id) photo = p;
      })
      .catch((e) => (error = e.message));
  });

  const close = () => (view.photo = null);

  async function copyPath() {
    try {
      await navigator.clipboard.writeText(photo.path);
    } catch {
      const ta = Object.assign(document.createElement('textarea'), { value: photo.path });
      document.body.append(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    copied = true;
    setTimeout(() => (copied = false), 1200);
  }

  const date = (s) => s?.replace(/^(\d{4}):(\d{2}):(\d{2})/, '$1-$2-$3');
  function exposure(p) {
    const parts = [];
    if (p.focal_length) {
      const equiv = p.focal_length_35 && p.focal_length_35 !== p.focal_length ? ` (${Math.round(p.focal_length_35)} mm equiv.)` : '';
      parts.push(`${+p.focal_length.toFixed(1)} mm${equiv}`);
    }
    if (p.aperture) parts.push(`f/${+p.aperture.toFixed(1)}`);
    if (p.exposure_time) {
      const t = p.exposure_time;
      parts.push(t >= 1 ? `${+t.toFixed(1)} s` : `1/${Math.round(1 / t)} s`);
    }
    if (p.iso) parts.push(`ISO ${p.iso}`);
    return parts.join(' · ');
  }
  const size = (n) => (n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.round(n / 1e3)} kB`);
  const families = $derived.by(() => {
    const out = {};
    for (const t of photo?.tags ?? []) (out[t.family] ??= []).push(t);
    return out;
  });
</script>

<div class="fixed inset-0 z-20 flex bg-black/80 backdrop-blur-sm" role="dialog" aria-modal="true">
  <button class="absolute inset-0 cursor-default" aria-label="Close" onclick={close}></button>

  <!-- Preview -->
  <div class="relative flex min-w-0 flex-1 items-center justify-center p-4">
    {#if photo?.id === id}
      <img
        src={photo.preview}
        alt={photo.rel_path}
        class="relative max-h-full max-w-full object-contain shadow-2xl"
      />
    {:else}
      <img src="/thumbs/{id}.jpg" alt="" class="relative max-h-full max-w-full object-contain opacity-60 blur-[1px]" />
    {/if}
    {#if hasPrev}
      <button
        class="absolute left-2 top-1/2 -translate-y-1/2 rounded-full bg-black/50 px-3 py-2 text-2xl text-neutral-200 hover:bg-black/80"
        aria-label="Previous"
        onclick={() => onstep(-1)}>‹</button
      >
    {/if}
    {#if hasNext}
      <button
        class="absolute right-2 top-1/2 -translate-y-1/2 rounded-full bg-black/50 px-3 py-2 text-2xl text-neutral-200 hover:bg-black/80"
        aria-label="Next"
        onclick={() => onstep(1)}>›</button
      >
    {/if}
  </div>

  <!-- Info panel -->
  <aside class="relative flex w-80 shrink-0 flex-col overflow-y-auto border-l border-neutral-800 bg-neutral-900 text-sm">
    <div class="flex items-center justify-between border-b border-neutral-800 px-4 py-2">
      <span class="text-xs text-neutral-500">#{id}</span>
      <button class="text-neutral-400 hover:text-white" aria-label="Close (Esc)" onclick={close}>✕</button>
    </div>

    {#if error}
      <p class="p-4 text-red-400">{error}</p>
    {:else if photo}
      <div class="space-y-4 p-4">
        <div class="flex items-center gap-1">
          <button
            class="flex-1 rounded px-2 py-1.5 text-xs font-medium {flag === 'pick' ? 'bg-emerald-500 text-black' : 'border border-neutral-700 text-emerald-300 hover:bg-emerald-950'}"
            title="Pick (P)"
            onclick={() => onflag(flag === 'pick' ? null : 'pick')}>✓ Pick</button
          >
          <button
            class="flex-1 rounded px-2 py-1.5 text-xs font-medium {flag === 'reject' ? 'bg-red-600 text-white' : 'border border-neutral-700 text-red-300 hover:bg-red-950'}"
            title="Reject (X)"
            onclick={() => onflag(flag === 'reject' ? null : 'reject')}>✕ Reject</button
          >
        </div>
        <label class="flex items-center gap-2 text-xs text-neutral-400" title="After P / X / U, go to the next photo">
          <input
            type="checkbox"
            checked={culling.autoAdvance}
            onchange={(e) => {
              culling.autoAdvance = e.currentTarget.checked;
              saveSetting('autoAdvance', culling.autoAdvance);
            }}
          />
          Next photo after flagging
        </label>

        <div class="flex flex-wrap gap-2">
          {#if photo.stack.length > 1}
            <button
              class="rounded border border-sky-800 px-3 py-1.5 text-xs text-sky-200 hover:bg-sky-950"
              title="Compare the similar shots in this stack"
              onclick={() => {
                view.photo = null;
                view.compare = { kind: 'stack', id: photo.stack_id };
              }}>Stack ({photo.stack.length})</button
            >
          {/if}
          <button class="rounded bg-sky-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-600" onclick={() => findSimilar(photo.id)}>
            Find similar
          </button>
          <button
            class="rounded border border-neutral-700 px-3 py-1.5 text-xs hover:bg-neutral-800"
            title="Browse this photo's {dateMode} in the grouped grid"
            onclick={() => ontimeline(photo, 'date')}
          >
            {photo.taken_at ? `Show ${dateMode}` : 'Show undated'}
          </button>
          {#if photo.location}
            <button
              class="rounded border border-neutral-700 px-3 py-1.5 text-xs hover:bg-neutral-800"
              title="Browse this photo's {placeMode} in the grouped grid"
              onclick={() => ontimeline(photo, 'location')}>Show {placeMode}</button
            >
          {/if}
          <button class="rounded border border-neutral-700 px-3 py-1.5 text-xs hover:bg-neutral-800" onclick={copyPath}>
            {copied ? 'Copied' : 'Copy path'}
          </button>
          <button class="rounded border border-neutral-700 px-3 py-1.5 text-xs hover:bg-neutral-800 disabled:opacity-40" disabled={!hasPrev} onclick={() => onstep(-1)}>
            Previous
          </button>
          <button class="rounded border border-neutral-700 px-3 py-1.5 text-xs hover:bg-neutral-800 disabled:opacity-40" disabled={!hasNext} onclick={() => onstep(1)}>
            Next
          </button>
        </div>

        <dl class="grid grid-cols-[5.5rem_1fr] gap-x-2 gap-y-1 text-xs">
          <dt class="text-neutral-500">Taken</dt>
          <dd>{date(photo.taken_at) ?? '—'}{photo.tz_offset ? ` (${photo.tz_offset})` : ''}</dd>
          <dt class="text-neutral-500">Camera</dt>
          <dd>{photo.camera ?? '—'}</dd>
          <dt class="text-neutral-500">Lens</dt>
          <dd>{photo.lens ?? '—'}</dd>
          {#if exposure(photo)}
            <dt class="text-neutral-500">Exposure</dt>
            <dd>{exposure(photo)}</dd>
          {/if}
          {#if photo.clip_highlights != null}
            {@const hi = photo.clip_highlights * 100}
            {@const lo = photo.clip_shadows * 100}
            <dt class="text-neutral-500">Clipping</dt>
            <dd>
              <span class={hi > 2 ? 'text-amber-400' : ''}>{hi < 0.1 ? 'no' : `${hi.toFixed(1)}%`} blown highlights</span>
              · <span class={lo > 5 ? 'text-amber-400' : ''}>{lo < 0.1 ? 'no' : `${lo.toFixed(1)}%`} crushed shadows</span>
            </dd>
          {/if}
          <dt class="text-neutral-500">Size</dt>
          <dd>{photo.width} × {photo.height} · {size(photo.size_bytes)}</dd>
          {#if photo.location}
            {@const loc = photo.location}
            <dt class="text-neutral-500">Location</dt>
            <dd>
              {loc.label || 'Unknown place'}
              <span class="block tabular-nums text-neutral-400">{loc.lat.toFixed(5)}, {loc.lon.toFixed(5)}</span>
              <span class="block text-[11px] {loc.source === 'exif' || loc.source === 'visit' ? 'text-neutral-500' : 'text-amber-400/80'}">
                {SOURCES[loc.source] ?? loc.source}{loc.source !== 'exif' ? ` · ${accuracy(loc.accuracy_m)}` : ''}
              </span>
            </dd>
          {/if}
          <dt class="text-neutral-500">Path</dt>
          <dd class="break-all font-mono text-[11px] text-neutral-300">{photo.path}</dd>
          {#each photo.raws as raw}
            <dt class="text-amber-400">RAW</dt>
            <dd class="break-all font-mono text-[11px] text-neutral-300">{raw.path}</dd>
          {/each}
        </dl>

        {#each Object.entries(families) as [family, list] (family)}
          <section>
            <h3 class="mb-1 text-xs font-semibold uppercase tracking-wider text-neutral-500">{family}</h3>
            <ul class="space-y-1">
              {#each list as tag (tag.id)}
                <li>
                  <button
                    class="w-full text-left text-xs hover:text-white"
                    title="Filter by this tag · similarity {tag.sim.toFixed(3)}"
                    onclick={() => {
                      if (!view.tags.includes(tag.id)) view.tags = [...view.tags, tag.id];
                      view.photo = null;
                    }}
                  >
                    <div class="flex justify-between">
                      <span>{tag.name}</span>
                      <span class="tabular-nums text-neutral-400">{(tag.prob * 100).toFixed(0)}%</span>
                    </div>
                    <div class="mt-0.5 h-1 rounded bg-neutral-800">
                      <div class="h-1 rounded bg-sky-600" style="width: {tag.prob * 100}%"></div>
                    </div>
                  </button>
                </li>
              {/each}
            </ul>
          </section>
        {/each}

        {#if photo.duplicates.length}
          <section>
            <h3 class="mb-1 text-xs font-semibold uppercase tracking-wider text-neutral-500">
              Duplicates ({photo.duplicates.length})
            </h3>
            <div class="grid grid-cols-3 gap-1">
              {#each photo.duplicates as d (d.id)}
                <button onclick={() => (view.photo = d.id)} title={d.rel_path} class="aspect-square overflow-hidden bg-neutral-800">
                  <img src={d.thumb} alt={d.rel_path} class="h-full w-full object-cover" />
                </button>
              {/each}
            </div>
          </section>
        {/if}
      </div>
    {:else}
      <p class="p-4 text-neutral-500">Loading…</p>
    {/if}
  </aside>
</div>
