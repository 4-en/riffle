<script>
  import { view, toggleTag } from '../lib/state.svelte.js';
  import Filters from './Filters.svelte';

  let { tags, facets } = $props();

  const titles = { subject: 'Subject', scene: 'Scene', look: 'Look' };
  // Server order follows vocabulary.yaml; only tags present in the current filter are listed.
  const families = $derived(tags ? Object.entries(tags.families) : []);
</script>

<aside class="w-60 shrink-0 overflow-y-auto border-r border-neutral-800 bg-neutral-900/50 px-2 py-3 text-sm">
  {#if !tags}
    <p class="px-2 text-neutral-500">Loading tags…</p>
  {:else}
    <Filters {facets} />
    {#each families as [family, list] (family)}
      <h2 class="mb-1 mt-3 px-2 text-xs font-semibold uppercase tracking-wider text-neutral-500 first:mt-0">
        {titles[family] ?? family}
      </h2>
      <ul>
        {#each list as tag (tag.id)}
          {@const on = view.tags.includes(tag.id)}
          <li>
            <button
              class="flex w-full items-center justify-between rounded px-2 py-1 text-left transition-colors
                {on ? 'bg-sky-700 text-white' : 'text-neutral-300 hover:bg-neutral-800'}"
              aria-pressed={on}
              onclick={() => toggleTag(tag.id)}
            >
              <span class="truncate">{tag.name}</span>
              <span class="ml-2 text-xs tabular-nums {on ? 'text-sky-100' : 'text-neutral-500'}">{tag.count}</span>
            </button>
          </li>
        {/each}
      </ul>
    {/each}

    <div class="mt-4 border-t border-neutral-800 px-2 pt-3 text-xs text-neutral-500">
      <p>{tags.photos.toLocaleString()} photos</p>
      {#if tags.unmatched_raws}
        <button class="mt-1 text-sky-400 hover:underline" onclick={() => (view.raws = true)}>
          {tags.unmatched_raws} unmatched RAWs
        </button>
      {/if}
      <p class="mt-1 truncate" title={tags.model_id}>{tags.model_id}</p>
    </div>
  {/if}
</aside>
