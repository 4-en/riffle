<script>
  // Curate: a draft selection (photo book, exhibition) from the photos within the
  // current filters: good photos, but not ten of the same thing. Sliders steer the
  // draft; removing a photo lets the next one take its place and locking keeps it.
  // Both are for this draft only (remembered per filter set in the browser): they
  // never change flags. Only "Mark as picks" does, and Ctrl+Z undoes it.
  import { untrack } from 'svelte';
  import { view } from '../lib/state.svelte.js';
  import { fetchCurate, fetchAlternatives, fetchStyles, fetchHues } from '../lib/api.js';
  import { applyFlags, flagOf } from '../lib/culling.svelte.js';
  import { justify, layoutAspect } from '../lib/justify.js';

  // onorder(ids): the draft in reading order (the photo view steps through it).
  let { onorder = () => {} } = $props();

  const NO_LOOK = { brightness: 0, contrast: 0, colorfulness: 0, hue: '' };
  const DEFAULTS = { n: 12, variety: 0.4, time_spread: 0.5, place_spread: 0.5, include_rejects: false, look: NO_LOOK };
  const SIZES = [6, 12, 24, 48];
  // Colour and light: three-way choices (lean one way, or not at all).
  const LOOK_CHOICES = [
    { key: 'brightness', title: 'Light', options: [[-1, 'Dark'], [0, 'Any'], [1, 'Bright']] },
    { key: 'contrast', title: 'Contrast', options: [[-1, 'Soft'], [0, 'Any'], [1, 'Punchy']] },
    { key: 'colorfulness', title: 'Colour', options: [[-1, 'Muted'], [0, 'Any'], [1, 'Vivid']] },
  ];

  // The draft is remembered per filter set: tags, excluded tags and filters.
  const key = untrack(() => `riffle.curate.${hash(JSON.stringify([view.tags, view.excludeTags, view.filters]))}`);
  const saved = load();
  let settings = $state({ ...DEFAULTS, ...saved.settings, look: { ...NO_LOOK, ...saved.settings?.look } });
  let styles = $state(saved.styles ?? {}); // name -> -1..1
  let locked = $state(saved.locked ?? []);
  let removed = $state(saved.removed ?? []);

  let styleList = $state([]);
  let hues = $state([]);
  let showStyles = $state(Object.values(saved.styles ?? {}).some(Boolean));
  let draft = $state(null);
  let loading = $state(true);
  let error = $state('');
  let notice = $state('');
  let alt = $state(null); // {id, items} alternatives for one slot

  function hash(s) {
    let h = 5381;
    for (let i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0;
    return h.toString(36);
  }

  function load() {
    try {
      return JSON.parse(localStorage.getItem(key)) ?? {};
    } catch {
      return {};
    }
  }

  const body = () => ({ ...settings, styles: $state.snapshot(styles), locked: [...locked], removed: [...removed] });

  fetchStyles()
    .then((s) => (styleList = s))
    .catch(() => {});
  fetchHues()
    .then((h) => (hues = h))
    .catch(() => {});

  const lookCount = $derived(Object.values(settings.look).filter(Boolean).length);

  // Regenerate (debounced) whenever a slider, lock or removal changes; remember the draft.
  let timer;
  let token = 0;
  $effect(() => {
    const request = JSON.stringify(body());
    untrack(() => {
      try {
        localStorage.setItem(key, JSON.stringify({ settings, styles, locked, removed }));
      } catch {}
      clearTimeout(timer);
      const mine = ++token;
      loading = true;
      timer = setTimeout(async () => {
        try {
          const d = await fetchCurate(view, JSON.parse(request));
          if (mine !== token) return;
          draft = d;
          error = '';
          onorder(d.items.map((i) => i.id));
        } catch (e) {
          if (mine === token) error = e.message;
        } finally {
          if (mine === token) loading = false;
        }
      }, draft ? 300 : 0);
    });
  });

  function reset() {
    settings = { ...DEFAULTS, look: { ...NO_LOOK } };
    styles = {};
    locked = [];
    removed = [];
    alt = null;
  }

  function remove(id) {
    locked = locked.filter((x) => x !== id);
    removed = [...removed, id];
    if (alt?.id === id) alt = null;
  }

  function toggleLock(id) {
    locked = locked.includes(id) ? locked.filter((x) => x !== id) : [...locked, id];
  }

  async function showAlternatives(id) {
    if (alt?.id === id) {
      alt = null;
      return;
    }
    alt = { id, items: null };
    try {
      const { items } = await fetchAlternatives(view, { ...body(), id });
      if (alt?.id === id) alt = { id, items };
    } catch (e) {
      error = e.message;
      alt = null;
    }
  }

  /** Put an alternative in a slot: it is locked, the old photo leaves the draft. */
  function swap(slot, id) {
    removed = [...removed.filter((x) => x !== id), slot];
    locked = [...locked.filter((x) => x !== slot && x !== id), id];
    alt = null;
  }

  const items = $derived(draft?.items ?? []);
  const toPick = $derived(items.filter((i) => flagOf(i) !== 'pick'));

  async function markPicks() {
    const ids = toPick.map((i) => i.id);
    await applyFlags([{ ids, flag: 'pick' }]);
    notice = `Marked ${ids.length} photo${ids.length === 1 ? '' : 's'} as picks. Ctrl+Z undoes it.`;
    setTimeout(() => (notice = ''), 5000);
  }

  function exportDraft() {
    view.exporting = { ids: items.map((i) => i.id), fresh: items.filter((i) => !i.exported).length };
  }

  // One justified block: rows of equal height that fill the width (lib/justify.js).
  const GAP = 8;
  let width = $state(0);
  const aspect = (it) => layoutAspect(it.width, it.height);
  const rows = $derived.by(() => {
    const target = Math.max(200, Math.min(340, width / 3.6));
    return justify(items.map(aspect), width, { target, gap: GAP }).map((r) => ({ ...r, items: items.slice(r.start, r.end) }));
  });

  const pct = (v) => `${Math.round(v * 100)}%`;
  const sliderRow = 'block text-xs text-neutral-300';
  const range = 'mt-1 w-full accent-sky-600';
  const action = 'rounded bg-black/70 px-1.5 py-0.5 text-[11px] text-neutral-100 hover:bg-black';
</script>

<div class="fixed inset-0 z-20 flex flex-col bg-neutral-950" role="dialog" aria-modal="true" aria-label="Curate">
  <header class="flex flex-wrap items-center gap-3 border-b border-neutral-800 bg-neutral-900 px-4 py-2 text-sm">
    <h2 class="font-semibold">Curate</h2>
    <span class="text-xs text-neutral-400">
      {#if draft}{items.length} of {draft.candidates} candidate{draft.candidates === 1 ? '' : 's'} in the current filters{/if}
      {#if loading}<span class="ml-1 text-neutral-500">· updating…</span>{/if}
    </span>
    <div class="ml-auto flex items-center gap-2">
      <button
        class="rounded border border-neutral-700 px-2.5 py-1 text-xs text-neutral-300 hover:bg-neutral-800 disabled:opacity-40"
        title="Flag the photos of this draft as picks (undo with Ctrl+Z)"
        disabled={!toPick.length}
        onclick={markPicks}>Mark as picks{toPick.length ? ` (${toPick.length})` : ''}</button
      >
      <button
        class="rounded bg-emerald-600 px-3 py-1 text-xs font-medium text-black hover:bg-emerald-500 disabled:opacity-40"
        disabled={!items.length}
        onclick={exportDraft}>Export {items.length} photos</button
      >
      <button class="text-neutral-400 hover:text-white" aria-label="Close (Esc)" onclick={() => (view.curate = false)}>✕</button>
    </div>
  </header>

  {#if error || notice}
    <p class="px-4 py-1.5 text-xs {error ? 'bg-red-950/60 text-red-300' : 'bg-emerald-950/60 text-emerald-200'}">{error || notice}</p>
  {/if}

  <div class="flex min-h-0 flex-1">
    <aside class="w-64 shrink-0 space-y-4 overflow-y-auto border-r border-neutral-800 bg-neutral-900/60 p-4">
      <div class={sliderRow}>
        <span class="flex justify-between"><span>Photos</span><span class="tabular-nums text-neutral-400">{settings.n}</span></span>
        <div class="mt-1.5 grid grid-cols-4 gap-1" role="group" aria-label="Number of photos">
          {#each SIZES as size (size)}
            <button
              class="rounded py-0.5 text-xs tabular-nums {settings.n === size ? 'bg-sky-700 text-white' : 'bg-neutral-800 text-neutral-300 hover:bg-neutral-700'}"
              aria-pressed={settings.n === size}
              onclick={() => (settings.n = size)}>{size}</button
            >
          {/each}
        </div>
        <input type="range" min="2" max="60" step="1" bind:value={settings.n} class={range} aria-label="Number of photos" />
      </div>
      <label class={sliderRow} title="Left: simply the best photos. Right: fewer similar ones, more different subjects.">
        <span class="flex justify-between"><span>Best ↔ Most varied</span><span class="tabular-nums text-neutral-400">{pct(settings.variety)}</span></span>
        <input type="range" min="0" max="1" step="0.05" bind:value={settings.variety} class={range} />
      </label>
      <label class={sliderRow} title="Prefer photos taken hours or days apart over several from the same moment.">
        <span class="flex justify-between"><span>Spread over time</span><span class="tabular-nums text-neutral-400">{pct(settings.time_spread)}</span></span>
        <input type="range" min="0" max="1" step="0.05" bind:value={settings.time_spread} class={range} />
      </label>
      {#if draft?.used?.locations}
        <label class={sliderRow} title="Prefer photos from different places over several from the same spot (uses the photos' coordinates).">
          <span class="flex justify-between"><span>Spread over places</span><span class="tabular-nums text-neutral-400">{pct(settings.place_spread)}</span></span>
          <input type="range" min="0" max="1" step="0.05" bind:value={settings.place_spread} class={range} />
        </label>
      {/if}

      <section class="space-y-2.5">
        <h3 class="flex items-center text-xs font-semibold uppercase tracking-wider text-neutral-500">
          Colour &amp; light
          {#if lookCount}
            <button class="ml-auto text-[11px] font-normal normal-case tracking-normal text-sky-400 hover:underline" onclick={() => (settings.look = { ...NO_LOOK })}>Clear</button>
          {/if}
        </h3>
        {#if hues.length}
          <div class="flex flex-wrap gap-1.5" role="group" aria-label="Lean towards a colour">
            {#each hues as h (h.name)}
              {@const on = settings.look.hue === h.name}
              <button
                class="h-6 w-6 rounded-full border-2 transition-transform {on ? 'scale-110 border-white' : 'border-transparent hover:scale-110'}"
                style="background: {h.color}"
                title="More {h.name}{on ? ' (click again to turn off)' : ''}"
                aria-label="More {h.name}"
                aria-pressed={on}
                onclick={() => (settings.look.hue = on ? '' : h.name)}
              ></button>
            {/each}
          </div>
        {/if}
        {#each LOOK_CHOICES as choice (choice.key)}
          <div class="flex items-center gap-2 text-xs">
            <span class="w-14 shrink-0 text-neutral-400">{choice.title}</span>
            <div class="grid flex-1 grid-cols-3 overflow-hidden rounded border border-neutral-700" role="group" aria-label={choice.title}>
              {#each choice.options as [value, label] (value)}
                {@const on = settings.look[choice.key] === value}
                <button
                  class="py-0.5 {value === 0 ? 'border-x border-neutral-700' : ''} {on
                    ? value === 0 ? 'bg-neutral-700 text-white' : 'bg-sky-700 text-white'
                    : 'text-neutral-400 hover:bg-neutral-800'}"
                  aria-pressed={on}
                  onclick={() => (settings.look[choice.key] = value)}>{label}</button
                >
              {/each}
            </div>
          </div>
        {/each}
        {#if draft?.used?.colors_missing}
          <p class="text-[11px] text-amber-300/80">
            The colours of {draft.used.colors_missing} photos are not analysed yet: run <em>Index now</em> in the Library (it is quick).
          </p>
        {/if}
      </section>

      {#if styleList.length}
        <section>
          <button
            class="flex w-full items-center gap-1.5 text-left text-xs font-semibold uppercase tracking-wider text-neutral-500 hover:text-neutral-300"
            aria-expanded={showStyles}
            onclick={() => (showStyles = !showStyles)}
          >
            <span class="inline-block w-2 text-[10px] transition-transform {showStyles ? 'rotate-90' : ''}">▶</span>
            Style
            {#if Object.values(styles).some(Boolean)}
              <span class="ml-auto rounded-full bg-sky-700 px-1.5 text-[10px] font-medium normal-case tracking-normal text-white">
                {Object.values(styles).filter(Boolean).length}
              </span>
            {/if}
          </button>
          {#if showStyles}
            <p class="mt-1 text-[11px] text-neutral-500">Less ← off → more. Styles are read from the photos by the AI model; edit them in vocabulary.yaml.</p>
            <div class="mt-2 space-y-3">
              {#each styleList as s (s.name)}
                {@const w = styles[s.name] ?? 0}
                <label class={sliderRow} title={s.description}>
                  <span class="flex justify-between">
                    <span>{s.label}</span>
                    <span class="tabular-nums {w ? 'text-sky-300' : 'text-neutral-500'}">{w > 0 ? '+' : ''}{w ? Math.round(w * 100) : 'off'}</span>
                  </span>
                  <input
                    type="range"
                    min="-1"
                    max="1"
                    step="0.1"
                    value={w}
                    oninput={(e) => (styles = { ...styles, [s.name]: Math.round(Number(e.currentTarget.value) * 10) / 10 })}
                    ondblclick={() => (styles = { ...styles, [s.name]: 0 })}
                    class={range}
                  />
                </label>
              {/each}
            </div>
          {/if}
        </section>
      {/if}

      <label class="flex items-start gap-2 text-xs text-neutral-300">
        <input type="checkbox" bind:checked={settings.include_rejects} class="mt-0.5" />
        <span>Include rejected photos <span class="block text-neutral-500">Otherwise picks and unflagged photos only.</span></span>
      </label>

      {#if draft && !draft.used?.taste}
        <p class="text-[11px] text-neutral-500">Tip: calibrate your taste in the Library for drafts closer to what you would pick.</p>
      {/if}

      <div class="space-y-1 border-t border-neutral-800 pt-3 text-[11px] text-neutral-500">
        {#if locked.length}<p>{locked.length} locked</p>{/if}
        {#if removed.length}<p>{removed.length} removed from this draft</p>{/if}
        <button class="rounded border border-neutral-700 px-2.5 py-1 text-xs text-neutral-300 hover:bg-neutral-800" onclick={reset}>Reset draft</button>
      </div>
    </aside>

    <main class="min-w-0 flex-1 overflow-y-auto">
      {#if draft && !items.length}
        <p class="mt-10 text-center text-sm text-neutral-400">
          No candidates in the current filters{settings.include_rejects ? '' : ' (rejected photos are left out)'}.
        </p>
      {:else if draft}
        <div class="mx-auto max-w-6xl px-8 py-8">
          <div bind:clientWidth={width}>
            {#each rows as row (row.start + '-' + row.end)}
              <div class="flex overflow-hidden" style="gap: {GAP}px; margin-bottom: {GAP}px; height: {row.height}px">
                {#each row.items as it (it.id)}
                  {@const isLocked = locked.includes(it.id)}
                  <figure
                    class="group relative shrink-0 overflow-hidden rounded-sm bg-neutral-900 bg-cover bg-center {alt?.id === it.id ? 'ring-2 ring-sky-500' : ''}"
                    style="width: {aspect(it) * row.height}px; background-image: url({it.thumb})"
                  >
                    <button class="block h-full w-full" title="Open" onclick={() => (view.photo = it.id)}>
                      <img src="/previews/{it.id}.jpg" alt="" loading="lazy" decoding="async" class="h-full w-full object-cover" onerror={(e) => (e.currentTarget.src = it.thumb)} />
                    </button>
                    <div class="absolute left-1.5 top-1.5 flex gap-1 {isLocked ? '' : 'opacity-0 group-hover:opacity-100'}">
                      <button class="{action} {isLocked ? 'bg-sky-700 hover:bg-sky-600' : ''}" title={isLocked ? 'Unlock' : 'Keep this photo when the draft changes'} onclick={() => toggleLock(it.id)}>
                        {isLocked ? 'Locked' : 'Lock'}
                      </button>
                    </div>
                    <div class="absolute right-1.5 top-1.5 flex gap-1 opacity-0 group-hover:opacity-100">
                      <button class={action} title="Other photos that could take this place" onclick={() => showAlternatives(it.id)}>Alternatives</button>
                      <button class={action} title="Remove from this draft; the next photo takes its place (not a reject)" onclick={() => remove(it.id)}>✕</button>
                    </div>
                    {#if it.reason || flagOf(it)}
                      <figcaption class="pointer-events-none absolute inset-x-0 bottom-0 flex items-center gap-1.5 bg-gradient-to-t from-black/80 to-transparent px-2 pb-1.5 pt-6 text-[11px] text-neutral-200 opacity-0 group-hover:opacity-100">
                        {#if flagOf(it) === 'pick'}<span class="text-emerald-400" title="Picked">✓</span>{:else if flagOf(it) === 'reject'}<span class="text-red-400" title="Rejected">✕</span>{/if}
                        <span class="truncate">{it.reason}</span>
                      </figcaption>
                    {/if}
                  </figure>
                {/each}
              </div>
            {/each}
          </div>
        </div>

        {#if alt}
          <div class="sticky bottom-0 border-t border-neutral-800 bg-neutral-900/95 px-8 py-3 backdrop-blur">
            <div class="mx-auto max-w-6xl">
              <div class="mb-2 flex items-center justify-between text-xs text-neutral-400">
                <span>Alternatives · click one to put it in this place</span>
                <button class="hover:text-white" aria-label="Close the alternatives" onclick={() => (alt = null)}>✕</button>
              </div>
              {#if !alt.items}
                <p class="text-xs text-neutral-500">Loading…</p>
              {:else if !alt.items.length}
                <p class="text-xs text-neutral-500">No other candidates.</p>
              {:else}
                <div class="flex gap-2 overflow-x-auto">
                  {#each alt.items as a (a.id)}
                    <button class="shrink-0" title="Use this one" onclick={() => swap(alt.id, a.id)}>
                      <img src={a.thumb} alt="" class="h-28 w-auto rounded-sm hover:ring-2 hover:ring-sky-500" />
                    </button>
                  {/each}
                </div>
              {/if}
            </div>
          </div>
        {/if}
      {/if}
    </main>
  </div>
</div>
