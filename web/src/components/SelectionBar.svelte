<script>
  import { view } from '../lib/state.svelte.js';
  import { selection, setFlag, clearSelection } from '../lib/culling.svelte.js';

  let { onselectall } = $props();

  const n = $derived(selection.size);
  const btn = 'rounded px-2.5 py-1 text-xs font-medium';
</script>

{#if n}
  <div class="pointer-events-none sticky bottom-3 z-20 flex justify-center">
    <div class="pointer-events-auto flex items-center gap-1 rounded-lg border border-neutral-700 bg-neutral-900/95 px-2 py-1.5 shadow-xl backdrop-blur">
      <span class="px-2 text-xs tabular-nums text-neutral-300">{n} selected</span>
      <button class="{btn} bg-emerald-600 text-black hover:bg-emerald-500" title="Pick (P)" onclick={() => setFlag(selection, 'pick')}>Pick</button>
      <button class="{btn} bg-red-700 text-white hover:bg-red-600" title="Reject (X)" onclick={() => setFlag(selection, 'reject')}>Reject</button>
      <button class="{btn} text-neutral-300 hover:bg-neutral-800" title="Clear flag (U)" onclick={() => setFlag(selection, null)}>Unflag</button>
      <span class="mx-1 h-4 w-px bg-neutral-700"></span>
      <button
        class="{btn} text-neutral-200 hover:bg-neutral-800 disabled:opacity-40"
        title="Compare side by side (C)"
        disabled={n < 2 || n > 30}
        onclick={() => (view.compare = { kind: 'ids', ids: [...selection] })}>Compare</button
      >
      <button
        class="{btn} text-neutral-200 hover:bg-neutral-800"
        title="Write or generate captions and tags for these photos"
        onclick={() => (view.captioning = { ids: [...selection] })}>Caption…</button
      >
      <button
        class="{btn} text-neutral-200 hover:bg-neutral-800"
        title="Learn a tag from these photos: photos like them get it"
        onclick={() => (view.tagDialog = { mode: 'create', photoIds: [...selection] })}>Learn tag…</button
      >
      <button
        class="{btn} text-neutral-200 hover:bg-neutral-800"
        title="Export these photos (picked or not)"
        onclick={() => (view.exporting = { ids: [...selection], fresh: null, kind: 'selection' })}>Export…</button
      >
      <button class="{btn} text-neutral-300 hover:bg-neutral-800" title="Select all (Ctrl+A)" onclick={onselectall}>All</button>
      <button class="{btn} text-neutral-400 hover:bg-neutral-800" title="Deselect (Esc)" onclick={clearSelection}>✕</button>
    </div>
  </div>
{/if}
