<script>
  import { view } from '../lib/state.svelte.js';
  import { fetchUnmatchedRaws } from '../lib/api.js';

  const raws = fetchUnmatchedRaws();
</script>

<div class="fixed inset-0 z-30 flex items-center justify-center bg-black/70 p-6" role="dialog" aria-modal="true">
  <button class="absolute inset-0 cursor-default" aria-label="Close" onclick={() => (view.raws = false)}></button>
  <div class="relative flex max-h-full w-full max-w-3xl flex-col rounded-lg border border-neutral-800 bg-neutral-900">
    <div class="flex items-center justify-between border-b border-neutral-800 px-4 py-2">
      <h2 class="text-sm font-semibold">Unmatched RAWs</h2>
      <button class="text-neutral-400 hover:text-white" aria-label="Close" onclick={() => (view.raws = false)}>✕</button>
    </div>
    <div class="overflow-y-auto p-4">
      {#await raws}
        <p class="text-sm text-neutral-500">Loading…</p>
      {:then list}
        <ul class="space-y-0.5 font-mono text-xs text-neutral-300">
          {#each list as raw}
            <li class="flex justify-between gap-4">
              <span class="break-all">{raw.path}</span>
              <span class="shrink-0 text-neutral-500">{(raw.size_bytes / 1e6).toFixed(1)} MB</span>
            </li>
          {:else}
            <li class="text-neutral-500">None</li>
          {/each}
        </ul>
      {:catch e}
        <p class="text-sm text-red-400">{e.message}</p>
      {/await}
    </div>
  </div>
</div>
