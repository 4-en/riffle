<script>
  import { view, toggleTag, toggleExcludeTag, toggleCtag, toggleExcludeCtag, activeCount, clearAll } from '../lib/state.svelte.js';
  import Filters from './Filters.svelte';
  import Section from './Section.svelte';

  let { tags, facets } = $props();

  const titles = { subject: 'Subject', scene: 'Scene', look: 'Look' };
  // Server order follows vocabulary.yaml; only tags present in the current filter are listed.
  const families = $derived(tags ? Object.entries(tags.families) : []);
</script>

<aside class="w-60 shrink-0 overflow-y-auto border-r border-neutral-800 bg-neutral-900/50 px-2 py-3 text-sm">
  {#if !tags}
    <p class="px-2 text-neutral-500">Loading tags…</p>
  {:else}
    {#if activeCount()}
      <div class="mb-3 flex items-center justify-between rounded bg-neutral-800/60 px-2 py-1.5 text-xs">
        <span class="text-neutral-400">{activeCount()} active</span>
        <button class="font-medium text-sky-400 hover:underline" title="Clear the search, tags, and filters" onclick={clearAll}>Clear all</button>
      </div>
    {/if}
    <Filters {facets} />
    <Section
      id="tags-custom"
      title="Your tags"
      defaultOpen
      active={(tags.custom ?? []).filter((t) => view.ctags.includes(t.id) || view.excludeCtags.includes(t.id)).length}
    >
      {#if tags.custom?.length}
        <ul>
          {#each tags.custom as tag (tag.id)}
            {@const on = view.ctags.includes(tag.id)}
            {@const off = view.excludeCtags.includes(tag.id)}
            <li class="group/tag relative">
              <button
                class="flex w-full items-center justify-between rounded px-2 py-1 text-left transition-colors
                  {on ? 'bg-sky-700 text-white' : off ? 'bg-red-950 text-red-300' : 'text-neutral-300 hover:bg-neutral-800'}"
                aria-pressed={on || off}
                title={off ? 'Excluded: click to show these photos again' : on ? 'Included: click to remove' : 'Click: only photos like its examples · Alt+click: hide them'}
                onclick={(e) => (off || e.altKey ? toggleExcludeCtag(tag.id) : toggleCtag(tag.id))}
              >
                <span class="truncate {off ? 'line-through decoration-red-400/70' : ''}">{off ? '−\u2009' : ''}{tag.name}</span>
                <span class="ml-2 text-xs tabular-nums {on ? 'text-sky-100' : off ? 'text-red-400' : 'text-neutral-500'} {off ? '' : 'group-hover/tag:invisible'}">
                  {off ? 'hidden' : tag.count}
                </span>
              </button>
              <div class="absolute right-1 top-1/2 hidden -translate-y-1/2 gap-0.5 group-hover/tag:flex">
                <button
                  class="rounded px-1.5 text-xs leading-5 {on ? 'text-sky-100 hover:bg-sky-800' : 'text-neutral-400 hover:bg-neutral-700 hover:text-white'}"
                  title="Edit the tag: name, examples, strictness"
                  aria-label="Edit {tag.name}"
                  onclick={() => (view.tagDialog = { mode: 'edit', tag })}>✎</button
                >
                {#if !off}
                  <button
                    class="rounded px-1.5 text-xs font-bold leading-5 {on ? 'text-sky-100 hover:bg-sky-800' : 'text-neutral-400 hover:bg-red-900 hover:text-white'}"
                    title="Exclude: hide photos with this tag (Alt+click)"
                    aria-label="Exclude {tag.name}"
                    onclick={() => toggleExcludeCtag(tag.id)}>−</button
                  >
                {/if}
              </div>
            </li>
          {/each}
        </ul>
      {:else}
        <p class="px-2 text-xs text-neutral-500">Select photos, right-click → <em>Create tag</em>: photos like them get the tag.</p>
      {/if}
    </Section>
    {#each families as [family, list] (family)}
      <Section
        id="tags-{family}"
        title={titles[family] ?? family}
        defaultOpen
        active={list.filter((t) => view.tags.includes(t.id) || view.excludeTags.includes(t.id)).length}
      >
      <ul>
        {#each list as tag (tag.id)}
          {@const on = view.tags.includes(tag.id)}
          {@const off = view.excludeTags.includes(tag.id)}
          <!-- Click: include (photos must have it). Hover shows "−" to exclude instead
               (photos must not have it); Alt+click does the same. Click again to clear. -->
          <li class="group/tag relative">
            <button
              class="flex w-full items-center justify-between rounded px-2 py-1 text-left transition-colors
                {on ? 'bg-sky-700 text-white' : off ? 'bg-red-950 text-red-300' : 'text-neutral-300 hover:bg-neutral-800'}"
              aria-pressed={on || off}
              title={off ? 'Excluded: click to show these photos again' : on ? 'Included: click to remove' : 'Click: only photos with this tag · Alt+click: hide them'}
              onclick={(e) => (off || e.altKey ? toggleExcludeTag(tag.id) : toggleTag(tag.id))}
            >
              <span class="truncate {off ? 'line-through decoration-red-400/70' : ''}">{off ? '−\u2009' : ''}{tag.name}</span>
              <span class="ml-2 text-xs tabular-nums {on ? 'text-sky-100' : off ? 'text-red-400' : 'text-neutral-500'} {off ? '' : 'group-hover/tag:invisible'}">
                {off ? 'hidden' : tag.count}
              </span>
            </button>
            {#if !off}
              <button
                class="absolute right-1 top-1/2 hidden -translate-y-1/2 rounded px-1.5 text-xs font-bold leading-5
                  group-hover/tag:block {on ? 'text-sky-100 hover:bg-sky-800' : 'text-neutral-400 hover:bg-red-900 hover:text-white'}"
                title="Exclude: hide photos with this tag (Alt+click)"
                aria-label="Exclude {tag.name}"
                onclick={() => toggleExcludeTag(tag.id)}>−</button
              >
            {/if}
          </li>
        {/each}
      </ul>
      </Section>
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
