<script>
  // Curate: a draft selection (photo book, exhibition) from the photos within the
  // current filters: good photos, but not ten of the same thing. Sliders steer the
  // draft; removing a photo lets the next one take its place and locking keeps it.
  // Both are for this draft only (remembered per filter set in the browser): they
  // never change flags. Only "Mark as picks" does, and Ctrl+Z undoes it.
  import { untrack } from 'svelte';
  import { view, curateKey } from '../lib/state.svelte.js';
  import { connection } from '../lib/connection.svelte.js';
  import { fetchCurate, fetchAlternatives, fetchStyles, fetchHues } from '../lib/api.js';
  import { applyFlags, flagOf } from '../lib/culling.svelte.js';
  import { justify, layoutAspect } from '../lib/justify.js';

  // onorder(ids): the draft in reading order (the photo view steps through it).
  let { onorder = () => {} } = $props();

  const NO_LOOK = { brightness: 0, contrast: 0, colorfulness: 0, hue: '', accent: '' };
  // surprise: 0 = the same draft every time; seed: which random draw (Shuffle picks a new one).
  const DEFAULTS = { n: 12, variety: 0.4, time_spread: 0.5, place_spread: 0.5, include_rejects: false, look: NO_LOOK, query: '', surprise: 0, seed: 0, like_locked: 0, unique: 0, query_weight: 1 };
  const SIZES = [6, 12, 24, 48];
  // Colour and light: three-way choices (lean one way, or not at all).
  const LOOK_CHOICES = [
    { key: 'brightness', title: 'Light', options: [[-1, 'Dark'], [0, 'Any'], [1, 'Bright']] },
    { key: 'contrast', title: 'Contrast', options: [[-1, 'Soft'], [0, 'Any'], [1, 'Punchy']] },
    { key: 'colorfulness', title: 'Colour', options: [[-1, 'Muted'], [0, 'Any'], [1, 'Vivid']] },
  ];

  // The draft is remembered per filter set: tags, excluded tags and filters.
  // (Per profile; the default profile keeps the original keys, so older drafts remain.)
  const key = untrack(() => {
    const profile = connection.profile && connection.profile !== 'default' ? `${connection.profile}.` : '';
    return `riffle.curate.${profile}${hash(JSON.stringify(curateKey()))}`;
  });
  const saved = load();
  // A search on the main page comes along (it scores the candidates; it does not filter).
  const startQuery = untrack(() => view.q) || saved.settings?.query || '';
  // From a Discover walk: its photos locked in, the rest filled around them (and at least as many photos).
  const walk = untrack(() => (typeof view.curate === 'object' ? view.curate.locked : null));
  const base = { ...DEFAULTS, ...saved.settings };
  let settings = $state({
    ...base,
    n: walk ? Math.min(60, Math.max(base.n, walk.length)) : base.n,
    look: { ...NO_LOOK, ...saved.settings?.look },
    query: startQuery,
  });
  let queryText = $state(startQuery);
  const applyQuery = () => (settings.query = queryText.trim());
  let styles = $state(saved.styles ?? {}); // name -> -1..1
  let locked = $state(walk ?? saved.locked ?? []);
  let removed = $state(walk ? [] : (saved.removed ?? []));
  // How the draft is laid out (the same photos; changing it asks the server for nothing):
  // date, best, flow (alike together), colour, light, or manual (dragged; `manual` ids).
  let order = $state(walk ? 'manual' : (saved.order ?? 'date'));
  let manual = $state(walk ?? saved.manual ?? []);

  let styleList = $state([]);
  let hues = $state([]);
  let showStyles = $state(Object.values(saved.styles ?? {}).some(Boolean));
  let draft = $state(null);
  let loading = $state(true);
  let error = $state('');
  let notice = $state(walk ? `The ${walk.length} photos of your walk are locked in; the rest is filled from the current filters.` : '');
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
  function persist() {
    try {
      localStorage.setItem(key, JSON.stringify({ settings, styles, locked, removed, order, manual }));
    } catch {}
  }
  $effect(() => {
    order, manual;
    untrack(persist);
  });
  $effect(() => {
    const request = JSON.stringify(body());
    untrack(() => {
      persist();
      clearTimeout(timer);
      const mine = ++token;
      loading = true;
      timer = setTimeout(async () => {
        try {
          const d = await fetchCurate(view, JSON.parse(request));
          if (mine !== token) return;
          draft = d;
          error = '';
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
    queryText = '';
    styles = {};
    locked = [];
    removed = [];
    order = 'date';
    manual = [];
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

  // The draft's photos in the chosen order. Manual: the dragged order for the photos still
  // in the draft, then any new ones by date.
  const byId = $derived(new Map((draft?.items ?? []).map((i) => [i.id, i])));
  const items = $derived.by(() => {
    const dated = draft?.orders?.date ?? (draft?.items ?? []).map((i) => i.id);
    let ids = draft?.orders?.[order] ?? dated;
    if (order === 'manual') {
      const kept = manual.filter((id) => byId.has(id));
      ids = [...kept, ...dated.filter((id) => !kept.includes(id))];
    }
    return ids.map((id) => byId.get(id)).filter(Boolean);
  });
  // The photo view steps through the draft in this order too.
  $effect(() => {
    const ids = items.map((i) => i.id);
    untrack(() => onorder(ids));
  });

  const ORDERS = [
    ['date', 'Date', 'By capture time'],
    ['best', 'Best', 'The best photos first'],
    ['flow', 'Alike', 'Each photo next to the one most like it, so the sequence flows'],
    ['colour', 'Colour', 'Around the colour wheel from red; black-and-white and grey last'],
    ['light', 'Light', 'Light to dark'],
    ['route', 'Route', 'The shortest way through the places, like a trip that visits each once (photos without a place last)'],
    ['zigzag', 'Zigzag', 'The longest way: from one side of the map to the other at every step (photos without a place last)'],
    ['manual', 'Yours', 'The order you dragged the photos into'],
  ];

  // Drag a photo onto another to put it before or after it (then the order is yours).
  let dragId = $state(null);
  let dropAt = $state(null); // {id, after}
  function dragOver(e, id) {
    if (dragId == null) return;
    e.preventDefault();
    const r = e.currentTarget.getBoundingClientRect();
    dropAt = { id, after: e.clientX > r.left + r.width / 2 };
  }
  function drop(e) {
    e.preventDefault();
    if (dragId != null && dropAt && dropAt.id !== dragId) {
      const ids = items.map((i) => i.id).filter((i) => i !== dragId);
      ids.splice(ids.indexOf(dropAt.id) + (dropAt.after ? 1 : 0), 0, dragId);
      manual = ids;
      order = 'manual';
    }
    dragId = dropAt = null;
  }
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
  // A -1..1 slider's value: "neutral", or which way and how far.
  const balance = (v, more, less) => (v === 0 ? 'neutral' : `${v > 0 ? more : less} ${pct(Math.abs(v))}`);
  const sliderRow = 'block text-xs text-neutral-300';
  const range = 'mt-1 w-full accent-sky-600';
  const action = 'rounded bg-black/70 px-1.5 py-0.5 text-[11px] text-neutral-100 hover:bg-black';
</script>

<!-- A sidebar section's heading, with Clear when something in it is set. -->
{#snippet heading(title, clear)}
  <h3 class="flex items-center text-xs font-semibold uppercase tracking-wider text-neutral-500">
    {title}
    {#if clear}
      <button class="ml-auto text-[11px] font-normal normal-case tracking-normal text-sky-400 hover:underline" onclick={clear}>Clear</button>
    {/if}
  </h3>
{/snippet}

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
    <aside class="w-64 shrink-0 space-y-5 overflow-y-auto border-r border-neutral-800 bg-neutral-900/60 p-4">
      <section class="space-y-2.5">
        <form
          onsubmit={(e) => {
            e.preventDefault();
            applyQuery();
          }}
        >
          <input
            type="search"
            bind:value={queryText}
            onblur={applyQuery}
            placeholder="Lean towards…, e.g. boats -people"
            title="Photos matching this come first; others can still fill the draft. The same syntax as the main search: -term leaves out, | means either. Enter applies."
            class="w-full rounded-md border bg-neutral-950 px-2.5 py-1.5 text-xs placeholder-neutral-500 outline-none focus:border-sky-600 {settings.query ? 'border-sky-700' : 'border-neutral-700'}"
          />
        </form>
        {#if draft?.used?.query === false}
          <p class="text-[11px] text-amber-300/80">The search is not used: the AI model is not loaded yet, or it has no words.</p>
        {/if}
        {#if settings.query}
          <label class={sliderRow} title="How much the search counts against quality and the other settings. 0: not at all; 100%: its best matches clearly win; 200%: almost only matches.">
            <span class="flex justify-between"><span>Search influence</span><span class="tabular-nums text-neutral-400">{pct(settings.query_weight)}</span></span>
            <input type="range" min="0" max="2" step="0.1" bind:value={settings.query_weight} ondblclick={() => (settings.query_weight = 1)} class={range} />
          </label>
        {/if}
      </section>

      <section class="space-y-2.5">
        {@render heading('Draft')}
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
        <label class={sliderRow} title="Left: simply the best photos. Right: fewer similar ones within the draft, more different subjects.">
          <span class="flex justify-between"><span>Best ↔ Most varied</span><span class="tabular-nums text-neutral-400">{pct(settings.variety)}</span></span>
          <input type="range" min="0" max="1" step="0.05" bind:value={settings.variety} class={range} />
        </label>
        <label class="flex items-start gap-2 text-xs text-neutral-300">
          <input type="checkbox" bind:checked={settings.include_rejects} class="mt-0.5" />
          <span>Include rejected photos <span class="block text-neutral-500">Otherwise picks and unflagged photos only.</span></span>
        </label>
      </section>

      <section class="space-y-2.5">
        {@render heading('Spread')}
        <label class={sliderRow} title="Prefer photos taken hours or days apart over several from the same moment.">
          <span class="flex justify-between"><span>Over time</span><span class="tabular-nums text-neutral-400">{pct(settings.time_spread)}</span></span>
          <input type="range" min="0" max="1" step="0.05" bind:value={settings.time_spread} class={range} />
        </label>
        {#if draft?.used?.locations}
          <label class={sliderRow} title="Prefer photos from different places over several from the same spot (uses the photos' coordinates).">
            <span class="flex justify-between"><span>Over places</span><span class="tabular-nums text-neutral-400">{pct(settings.place_spread)}</span></span>
            <input type="range" min="0" max="1" step="0.05" bind:value={settings.place_spread} class={range} />
          </label>
        {/if}
      </section>

      <section class="space-y-2.5">
        {@render heading('Lean', settings.unique || settings.like_locked || settings.surprise ? () => (settings = { ...settings, unique: 0, like_locked: 0, surprise: 0 }) : null)}
        <label
          class={sliderRow}
          title="Compared with the whole library (not only these candidates; its own stack and duplicates do not count). Right: photos unlike anything else you have. Left: typical photos, like many others. Most varied is different: it spreads the draft itself."
        >
          <span class="flex justify-between">
            <span>Common ↔ Unique</span>
            <span class="tabular-nums text-neutral-400">{balance(settings.unique, 'more unique', 'more common')}</span>
          </span>
          <input type="range" min="-1" max="1" step="0.1" bind:value={settings.unique} ondblclick={() => (settings.unique = 0)} class={range} />
        </label>
        <label
          class="{sliderRow} {locked.length ? '' : 'opacity-50'}"
          title={locked.length
            ? 'Left: the rest of the draft unlike the locked photos. Right: like them (each compared with its closest locked photo). Middle: no influence.'
            : 'Lock photos in the draft (Lock, on a photo when you hover it) to steer the rest towards or away from them.'}
        >
          <span class="flex justify-between">
            <span>Like the locked photos</span>
            <span class="tabular-nums text-neutral-400">{!locked.length ? 'lock some first' : balance(settings.like_locked, 'more', 'less')}</span>
          </span>
          <input type="range" min="-1" max="1" step="0.1" bind:value={settings.like_locked} disabled={!locked.length} ondblclick={() => (settings.like_locked = 0)} class={range} />
        </label>
        <div class={sliderRow}>
          <label class="block" title="Left: the same draft every time. Right: less obvious photos get a chance (never the weakest third). Shuffle draws again.">
            <span class="flex justify-between">
              <span>Surprise</span>
              <span class="tabular-nums text-neutral-400">{settings.surprise ? pct(settings.surprise) : 'off'}</span>
            </span>
            <input type="range" min="0" max="1" step="0.05" bind:value={settings.surprise} class={range} />
          </label>
          {#if settings.surprise > 0}
            <button
              class="mt-1 rounded border border-neutral-700 px-2 py-0.5 text-xs text-neutral-300 hover:bg-neutral-800"
              title="Another random draw with the same settings (locked photos stay)"
              onclick={() => (settings.seed = Math.floor(Math.random() * 2 ** 31))}>Shuffle</button
            >
          {/if}
        </div>
      </section>

      <section class="space-y-2.5">
        {@render heading('Colour & light', lookCount ? () => (settings.look = { ...NO_LOOK }) : null)}
        {#if hues.length}
          <div class="flex items-center gap-2 text-xs">
            <span class="w-14 shrink-0 text-neutral-400" title="The colour that most of the photo has">Main</span>
            <div class="flex flex-wrap gap-1" role="group" aria-label="Lean towards a main colour">
              {#each hues as h (h.name)}
                {@const on = settings.look.hue === h.name}
                <button
                  class="h-5 w-5 rounded-full border-2 transition-transform {on ? 'scale-110 border-white' : 'border-transparent hover:scale-110'}"
                  style="background: {h.color}"
                  title="Mostly {h.name}{on ? ' (click again to turn off)' : ''}"
                  aria-label="Mostly {h.name}"
                  aria-pressed={on}
                  onclick={() => (settings.look.hue = on ? '' : h.name)}
                ></button>
              {/each}
            </div>
          </div>
          <div class="flex items-center gap-2 text-xs">
            <span class="w-14 shrink-0 text-neutral-400" title="An intense colour that need not take up much of the photo: a red balloon in a blue sky">Accent</span>
            <div class="flex flex-wrap gap-1" role="group" aria-label="Lean towards an accent colour">
              {#each hues as h (h.name)}
                {@const on = settings.look.accent === h.name}
                <button
                  class="h-5 w-5 rounded-full border-2 transition-transform {on ? 'scale-110 border-white' : 'border-transparent hover:scale-110'}"
                  style="background: radial-gradient(circle, {h.color} 0 38%, #3f3f46 42%)"
                  title="A {h.name} accent: an intense {h.name} detail, however small{on ? ' (click again to turn off)' : ''}"
                  aria-label="A {h.name} accent"
                  aria-pressed={on}
                  onclick={() => (settings.look.accent = on ? '' : h.name)}
                ></button>
              {/each}
            </div>
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
            The colours of {draft.used.colors_missing} photos are not analysed yet:
            <button class="text-sky-400 hover:underline" onclick={() => (view.settings = 'indexing')}>Index now</button> (it is quick).
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

      <div class="space-y-1 border-t border-neutral-800 pt-3 text-[11px] text-neutral-500">
        {#if draft && !draft.used?.taste}
          <p class="pb-1">
            Tip: <button class="text-sky-400 hover:underline" onclick={() => (view.settings = 'taste')}>calibrate your taste</button> for drafts closer to what you would pick.
          </p>
        {/if}
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
          <div class="mb-3 flex flex-wrap items-center gap-1 text-xs text-neutral-400" role="group" aria-label="Order">
            <span class="mr-1">Order</span>
            {#each ORDERS as [value, label, hint] (value)}
              {#if (value !== 'manual' || manual.length) && ((value !== 'route' && value !== 'zigzag') || draft.used?.locations)}
                <button
                  class="rounded px-2 py-0.5 {order === value ? 'bg-sky-700 text-white' : 'bg-neutral-800 text-neutral-300 hover:bg-neutral-700'}"
                  aria-pressed={order === value}
                  title={hint}
                  onclick={() => (order = value)}>{label}</button
                >
              {/if}
            {/each}
            <span class="ml-2 text-neutral-600">Drag photos to arrange them yourself; the export keeps the order.</span>
          </div>
          <div bind:clientWidth={width}>
            {#each rows as row (row.start + '-' + row.end)}
              <div class="flex overflow-hidden" style="gap: {GAP}px; margin-bottom: {GAP}px; height: {row.height}px">
                {#each row.items as it (it.id)}
                  {@const isLocked = locked.includes(it.id)}
                  <figure
                    class="group relative shrink-0 cursor-grab overflow-hidden rounded-sm bg-neutral-900 bg-cover bg-center {alt?.id === it.id ? 'ring-2 ring-sky-500' : ''} {dragId === it.id ? 'opacity-40' : ''}"
                    style="width: {aspect(it) * row.height}px; background-image: url({it.thumb})"
                    draggable="true"
                    ondragstart={(e) => {
                      dragId = it.id;
                      e.dataTransfer.effectAllowed = 'move';
                    }}
                    ondragover={(e) => dragOver(e, it.id)}
                    ondrop={drop}
                    ondragend={() => (dragId = dropAt = null)}
                  >
                    {#if dropAt?.id === it.id && dragId !== it.id}
                      <div class="pointer-events-none absolute inset-y-0 z-10 w-1 bg-sky-400 {dropAt.after ? 'right-0' : 'left-0'}"></div>
                    {/if}
                    <button class="block h-full w-full" title="Open" onclick={() => (view.photo = it.id)}>
                      <img src="/previews/{it.id}.jpg" alt="" loading="lazy" decoding="async" draggable="false" class="h-full w-full object-cover" onerror={(e) => (e.currentTarget.src = it.thumb)} />
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
