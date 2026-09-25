<script>
  import { view } from '../lib/state.svelte.js';
  import { fetchSources, addSource, removeSource, startIndex } from '../lib/api.js';
  import FolderBrowser from './FolderBrowser.svelte';

  let { status, onchange } = $props();

  let sources = $state(null);
  let dir = $state(null);
  let browser;
  let error = $state('');
  let busy = $state(false);

  async function loadSources() {
    try {
      sources = await fetchSources();
    } catch (e) {
      error = e.message;
    }
  }

  // Load now, and again as indexing progresses (photo counts change).
  $effect(() => {
    status?.running;
    status?.finished_at;
    loadSources();
  });

  async function act(fn) {
    busy = true;
    error = '';
    try {
      await fn();
      await loadSources();
      if (dir) await browser.open(dir.path);
      onchange();
    } catch (e) {
      error = e.message;
    } finally {
      busy = false;
    }
  }

  const add = () => act(() => addSource(dir.path));
  const remove = (path) => {
    if (confirm(`Remove ${path} from the library?\n\nThe files are not touched; its photos are hidden after re-indexing.`))
      act(() => removeSource(path));
  };
  const reindex = () => act(startIndex);

  const close = () => (view.library = false);
  const pct = $derived(status?.total ? Math.min(100, (100 * status.done) / status.total) : null);
</script>

<div class="fixed inset-0 z-30 flex items-center justify-center bg-black/70 p-6" role="dialog" aria-modal="true" aria-label="Library">
  <button class="absolute inset-0 cursor-default" aria-label="Close" onclick={close}></button>
  <div class="relative flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-lg border border-neutral-800 bg-neutral-900 text-sm">
    <div class="flex items-center justify-between border-b border-neutral-800 px-4 py-2">
      <h2 class="font-semibold">Library</h2>
      <button class="text-neutral-400 hover:text-white" aria-label="Close" onclick={close}>✕</button>
    </div>

    <div class="space-y-5 overflow-y-auto p-4">
      {#if error}
        <p class="rounded border border-red-900 bg-red-950/50 px-3 py-2 text-red-300">{error}</p>
      {/if}

      <!-- Configured folders -->
      <section>
        <h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-neutral-500">Photo folders</h3>
        {#if !sources}
          <p class="text-neutral-500">Loading…</p>
        {:else if !sources.sources.length}
          <p class="text-neutral-400">No folders yet. Pick one below and add it.</p>
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
        {#if sources?.config}
          <p class="mt-1 text-xs text-neutral-500">Saved in <span class="font-mono">{sources.config}</span>. Originals are only read, never modified.</p>
        {/if}
      </section>

      <!-- Folder browser -->
      <section>
        <h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-neutral-500">Add a folder</h3>
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
      </section>

      <!-- Indexing -->
      <section>
        <div class="mb-2 flex items-center justify-between">
          <h3 class="text-xs font-semibold uppercase tracking-wider text-neutral-500">Indexing</h3>
          <button
            class="rounded border border-neutral-700 px-3 py-1 text-xs hover:bg-neutral-800 disabled:opacity-40"
            disabled={busy || status?.running}
            onclick={reindex}>Index now</button
          >
        </div>
        {#if status?.running}
          <p class="text-neutral-200">{status.step}{status.pending ? ' (another run queued)' : ''}</p>
          <div class="mt-1 h-1.5 overflow-hidden rounded bg-neutral-800">
            {#if pct !== null}
              <div class="h-full bg-sky-600 transition-[width]" style="width: {pct}%"></div>
            {:else}
              <div class="h-full w-1/3 animate-pulse bg-sky-700"></div>
            {/if}
          </div>
          {#if status.total}
            <p class="mt-1 text-xs tabular-nums text-neutral-500">{status.done} / {status.total}</p>
          {/if}
        {:else if status?.error}
          <p class="text-red-400">Indexing failed: {status.error}</p>
        {:else if status?.finished_at}
          <p class="text-neutral-400">Last run finished {new Date(status.finished_at * 1000).toLocaleTimeString()}.</p>
        {:else}
          <p class="text-neutral-500">Not run since the server started. Use <span class="font-mono">archive index</span> or the button above.</p>
        {/if}
        {#if status?.lines?.length}
          <pre class="mt-2 max-h-40 overflow-y-auto rounded bg-neutral-950 p-2 text-[11px] leading-relaxed text-neutral-400">{status.lines.join('\n')}</pre>
        {/if}
      </section>
    </div>
  </div>
</div>
