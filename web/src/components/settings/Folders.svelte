<script>
  // Settings → Photo folders: the folders in the library, and a browser to add one.
  import { addSource, removeSource } from '../../lib/api.js';
  import FolderBrowser from '../FolderBrowser.svelte';

  let { sources, busy, act } = $props();

  let adding = $state(false);
  let dir = $state(null);
  let browser = $state();
  // With no folders yet, the browser is the point of the page.
  const browsing = $derived(adding || (sources && !sources.sources.length));

  async function add() {
    adding = true; // stays open after the first folder, for more
    if (await act(() => addSource(dir.path))) await browser?.open(dir.path); // shows it as "In library"
  }
  function remove(path) {
    if (confirm(`Remove ${path} from the library?\n\nThe files are not touched; its photos are hidden after re-indexing.`)) act(() => removeSource(path));
  }
</script>

<h3 class="text-base font-semibold text-neutral-100">Photo folders</h3>
<p class="mb-3 mt-1 text-xs text-neutral-400">The folders in your library, with their subfolders. Originals are only read, never modified.</p>

{#if !sources}
  <p class="text-neutral-500">Loading…</p>
{:else if !sources.sources.length}
  <p class="text-neutral-400">No folders yet. Browse to one below and add it.</p>
{:else}
  <ul class="divide-y divide-neutral-800 rounded border border-neutral-800">
    {#each sources.sources as s (s.path)}
      <li class="flex items-center gap-3 px-3 py-2">
        <span class="min-w-0 flex-1 break-all font-mono text-xs {s.exists ? 'text-neutral-200' : 'text-red-400'}">
          {s.path}
          {#if !s.exists}<span class="ml-1 font-sans">(not found)</span>{/if}
        </span>
        <span class="shrink-0 text-xs tabular-nums text-neutral-400">{s.photos.toLocaleString()} photos</span>
        <button
          class="shrink-0 rounded px-2 py-0.5 text-xs text-neutral-400 hover:bg-neutral-800 hover:text-red-300 disabled:opacity-40"
          disabled={busy || !sources.editable}
          onclick={() => remove(s.path)}>Remove</button
        >
      </li>
    {/each}
  </ul>
{/if}

{#if browsing}
  <div class="mt-4">
    <div class="mb-2 flex items-center justify-between">
      <h4 class="text-xs font-semibold uppercase tracking-wider text-neutral-500">Add a folder</h4>
      {#if sources?.sources.length}
        <button class="text-xs text-neutral-400 hover:text-white" onclick={() => (adding = false)}>Done</button>
      {/if}
    </div>
    <FolderBrowser bind:this={browser} bind:dir>
      {#snippet footer(dir)}
        <div class="mt-2 flex items-center justify-between gap-3">
          <span class="text-xs text-neutral-400">
            {dir.images} image{dir.images === 1 ? '' : 's'} directly in this folder; subfolders are included too.
          </span>
          {#if dir.source}
            <span class="shrink-0 text-xs text-emerald-400">In library{dir.source !== dir.path ? ' (via parent)' : ''}</span>
          {:else}
            <button
              class="shrink-0 rounded bg-sky-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-600 disabled:opacity-50"
              disabled={busy || !sources?.editable}
              onclick={add}>Add this folder and index</button
            >
          {/if}
        </div>
      {/snippet}
    </FolderBrowser>
  </div>
{:else}
  <button
    class="mt-3 rounded bg-sky-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-600 disabled:opacity-40"
    disabled={!sources?.editable}
    onclick={() => (adding = true)}>Add a folder…</button
  >
{/if}
