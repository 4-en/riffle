<script>
  import { view, search, clearAll, hasOverview } from '../lib/state.svelte.js';

  // taste: the taste model's status (enables the "likely keepers / rejects" sorts).
  let { total, loading, indexStatus = null, textSearch = true, picks = 0, taste = null } = $props();

  const indexPct = $derived(
    indexStatus?.running && indexStatus.total ? Math.round((100 * indexStatus.done) / indexStatus.total) : null
  );

  let input;
  let text = $state(view.q);

  // Keep the box in sync when the query changes elsewhere (back button, Find similar).
  $effect(() => {
    text = view.q;
  });

  export function focus() {
    input.focus();
    input.select();
  }

  function onsubmit(e) {
    e.preventDefault();
    search(text);
  }

  function clear() {
    text = '';
    clearAll();
  }

</script>

<header class="flex items-center gap-3 border-b border-neutral-800 bg-neutral-900 px-4 py-2">
  <button
    class="shrink-0 text-sm font-semibold tracking-wide text-neutral-100"
    onclick={() => {
      clear();
      view.photo = null;
    }}
  >
    Riffle
  </button>

  <form class="relative flex-1" {onsubmit}>
    <input
      bind:this={input}
      bind:value={text}
      type="search"
      disabled={!textSearch}
      placeholder={textSearch ? 'Search photos, e.g. "red lanterns at night"   ( / )' : 'Text search unavailable (model not loaded)'}
      class="w-full max-w-2xl rounded-md border border-neutral-700 bg-neutral-950 px-3 py-1.5 text-sm placeholder-neutral-500 outline-none focus:border-sky-600"
    />
  </form>

  {#if view.similar}
    <span class="flex items-center gap-2 rounded-full bg-sky-900/60 py-0.5 pl-1 pr-1 text-xs text-sky-100">
      <img src="/thumbs/{view.similar}.jpg" alt="" class="h-6 w-6 rounded-full object-cover" />
      Similar to #{view.similar}
      <button
        class="rounded-full px-1.5 text-sky-300 hover:bg-sky-800 hover:text-white"
        title="Back to browsing"
        aria-label="Clear the similar search"
        onclick={() => (view.similar = null)}>✕</button
      >
    </span>
  {/if}

  <label
    class="flex shrink-0 items-center gap-1 text-xs text-neutral-400 {view.q || view.similar ? 'opacity-40' : ''}"
    title={view.q || view.similar ? 'Grouping applies when browsing, not to search results' : 'Group the grid by date or place'}
  >
    Group
    <select
      bind:value={view.group}
      disabled={!!(view.q || view.similar)}
      class="rounded border border-neutral-700 bg-neutral-900 px-1 py-1 text-neutral-200"
    >
      <option value="">None</option>
      <optgroup label="Date">
        <option value="day">Day</option>
        <option value="month">Month</option>
        <option value="year">Year</option>
      </optgroup>
      <optgroup label="Location">
        <option value="place">Place</option>
        <option value="region">Region</option>
        <option value="country">Country</option>
      </optgroup>
      <optgroup label="Files">
        <option value="folder">Folder</option>
      </optgroup>
    </select>
  </label>
  <label
    class="flex shrink-0 items-center gap-1 text-xs text-neutral-400 {view.q || view.similar ? 'opacity-40' : ''}"
    title={view.q || view.similar
      ? 'Search results are ordered by relevance'
      : taste?.enabled
        ? 'Order photos by date, or by how likely you are to keep them (learned from your picks and rejects)'
        : 'Order photos by date'}
  >
    Sort
    <select
      bind:value={view.sort}
      disabled={!!(view.q || view.similar)}
      onchange={() => {
        // The model rates scenes, so a burst's siblings rank together: one tile per stack reads best.
        if (view.sort === 'taste' || view.sort === '-taste') view.collapse = 'stacks';
      }}
      class="rounded border border-neutral-700 bg-neutral-900 px-1 py-1 text-neutral-200"
    >
      <optgroup label="Date">
        <option value="taken_at">Oldest first</option>
        <option value="-taken_at">Newest first</option>
      </optgroup>
      <optgroup label="Name">
        <option value="place" disabled={!!view.group}>Place A → Z</option>
        <option value="-place" disabled={!!view.group}>Place Z → A</option>
        <option value="name" disabled={!!view.group}>File name A → Z</option>
        <option value="-name" disabled={!!view.group}>File name Z → A</option>
      </optgroup>
      <optgroup label="Your taste">
        <option value="taste" disabled={!taste?.enabled || !!view.group}>Likely keepers first</option>
        <option value="-taste" disabled={!taste?.enabled || !!view.group}>Likely rejects first</option>
      </optgroup>
    </select>
  </label>

  {#if hasOverview(view.group)}
    {@const isMap = ['place', 'region', 'country'].includes(view.group)}
    <button
      class="shrink-0 rounded border px-2 py-1 text-xs disabled:opacity-40 {view.overview
        ? 'border-sky-700 bg-sky-800 text-white'
        : 'border-neutral-700 text-neutral-400 hover:bg-neutral-800'}"
      aria-pressed={view.overview}
      disabled={!!(view.q || view.similar)}
      title={view.q || view.similar ? 'Overviews apply when browsing, not to search results' : `${isMap ? 'Map' : 'Calendar'} overview of the groups (O)`}
      onclick={() => (view.overview = !view.overview)}
    >
      {isMap ? 'Map' : 'Calendar'}
    </button>
  {/if}

  <button
    class="shrink-0 rounded border px-2 py-1 text-xs {view.collapse === 'stacks'
      ? 'border-sky-700 bg-sky-800 text-white'
      : 'border-neutral-700 text-neutral-400 hover:bg-neutral-800'}"
    aria-pressed={view.collapse === 'stacks'}
    title="Show one tile per stack of similar shots (S)"
    onclick={() => (view.collapse = view.collapse === 'stacks' ? 'dupes' : 'stacks')}>Stacks</button
  >

  <span class="shrink-0 text-xs tabular-nums text-neutral-400">
    {#if loading && !total}Loading…{:else}{total.toLocaleString()} {total === 1 ? 'photo' : 'photos'}{/if}
  </span>

  <button
    class="shrink-0 rounded border border-neutral-700 px-2 py-1 text-xs text-neutral-300 hover:bg-neutral-800"
    title="Go through the stacks that still have unflagged photos, in the current filters (R)"
    onclick={() => (view.compare = { kind: 'review' })}>Review stacks</button
  >
  <button
    class="shrink-0 rounded px-2.5 py-1 text-xs font-medium {picks
      ? 'bg-emerald-600 text-black hover:bg-emerald-500'
      : 'border border-neutral-700 text-neutral-500'}"
    title="Copy the picked photos to a folder"
    onclick={() => (view.exporting = true)}>Export{picks ? ` ${picks}` : ''}</button
  >

  <button
    class="flex shrink-0 items-center gap-2 rounded border border-neutral-700 px-2 py-1 text-xs text-neutral-300 hover:bg-neutral-800"
    title={indexStatus?.running ? indexStatus.step : 'Photo folders and indexing'}
    onclick={() => (view.library = true)}
  >
    {#if indexStatus?.running}
      <span class="h-2 w-2 animate-pulse rounded-full bg-sky-500"></span>
      Indexing{indexPct !== null ? ` ${indexPct}%` : '…'}
    {:else}
      Library
    {/if}
  </button>

  <button
    class="shrink-0 rounded-full border border-neutral-700 px-2 py-0.5 text-xs font-semibold text-neutral-400 hover:bg-neutral-800 hover:text-white"
    title="How it works (?)"
    aria-label="How it works"
    onclick={() => (view.help = true)}>?</button
  >

</header>
