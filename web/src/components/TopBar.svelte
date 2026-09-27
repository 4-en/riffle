<script>
  import { view, prefs, setPref, search, clearAll } from '../lib/state.svelte.js';

  // The grid's own controls (count, group, sort, stacks) are in ViewBar.
  let { indexStatus = null, textSearch = true, picks = 0 } = $props();

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

  <form class="flex-1" {onsubmit}>
    <div class="relative max-w-2xl">
      <input
        bind:this={input}
        bind:value={text}
        type="search"
        disabled={!textSearch}
        placeholder={textSearch ? 'Search photos, e.g. "boats at sunset", or leave something out: "street -people"   ( / )' : 'Search works once the AI model is loaded'}
        class="w-full rounded-md border border-neutral-700 bg-neutral-950 py-1.5 pl-3 pr-20 text-sm placeholder-neutral-500 outline-none focus:border-sky-600"
      />
      <button
        type="button"
        class="absolute right-1.5 top-1/2 -translate-y-1/2 rounded px-1.5 py-0.5 text-[11px] {prefs.nameMatch
          ? 'bg-sky-800 text-sky-100 hover:bg-sky-700'
          : 'text-neutral-500 hover:bg-neutral-800 hover:text-neutral-300'}"
        aria-pressed={prefs.nameMatch}
        title={prefs.nameMatch
          ? 'File and folder names: on. Photos whose file name (then folder) contains all search words come first. Click to turn off.'
          : 'File and folder names: off. Search looks at the pictures only. Click to also put name matches first.'}
        onclick={() => setPref('nameMatch', !prefs.nameMatch)}>Names</button
      >
    </div>
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

  <!-- The workflow, in order: cull, curate, export. -->
  <div class="flex shrink-0 items-center gap-1.5">
    <button
      class="shrink-0 rounded border border-neutral-700 px-2 py-1 text-xs text-neutral-300 hover:bg-neutral-800"
      title="Go through the stacks that still have unflagged photos, in the current filters (R)"
      onclick={() => (view.compare = { kind: 'review' })}>Review stacks</button
    >
    <button
      class="shrink-0 rounded border border-neutral-700 px-2 py-1 text-xs text-neutral-300 hover:bg-neutral-800 disabled:opacity-40"
      title={view.similar
        ? 'Curate works on the current filters and a text search, not on "similar to"'
        : view.q
          ? 'Draft a small, varied selection from the current filters, leaning towards this search'
          : 'Draft a small, varied selection (photo book, exhibition) from the current filters'}
      disabled={!!view.similar}
      onclick={() => (view.curate = true)}>Curate</button
    >
    <button
      class="shrink-0 rounded px-2.5 py-1 text-xs font-medium {picks
        ? 'bg-emerald-600 text-black hover:bg-emerald-500'
        : 'border border-neutral-700 text-neutral-500'}"
      title="Copy the picked photos to a folder"
      onclick={() => (view.exporting = true)}>Export{picks ? ` ${picks}` : ''}</button
    >
  </div>

  <div class="h-5 w-px shrink-0 bg-neutral-800"></div>

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
