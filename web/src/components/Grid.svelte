<script>
  import { view, groupLabel, filterToGroup } from '../lib/state.svelte.js';

  // `groups`: the result's date groups (key, count) when the grid is grouped, else null.
  let { items, loading, hasMore, onmore, groups = null, highlight = null, onjump } = $props();

  let sentinel;

  function nearBottom() {
    return sentinel && sentinel.getBoundingClientRect().top < window.innerHeight + 800;
  }

  // Infinite scroll: fetch the next page as the sentinel approaches the viewport.
  $effect(() => {
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) onmore();
      },
      { root: sentinel.closest('main'), rootMargin: '800px' }
    );
    io.observe(sentinel);
    return () => io.disconnect();
  });

  // If a page did not fill the screen, the observer will not fire again; keep loading.
  $effect(() => {
    items.length;
    if (!loading && hasMore && nearBottom()) onmore();
  });

  // Keep the open photo in view while stepping through the detail panel.
  $effect(() => {
    const id = view.photo;
    if (id != null) document.getElementById(`tile-${id}`)?.scrollIntoView({ block: 'nearest' });
  });

  // Consecutive items with the same group key form a section (items arrive in group order).
  const sections = $derived.by(() => {
    if (!groups) return [{ key: null, items }];
    const out = [];
    for (const item of items) {
      const last = out.at(-1);
      if (last && last.key === item.group) last.items.push(item);
      else out.push({ key: item.group, items: [item] });
    }
    return out;
  });
  const counts = $derived(new Map((groups ?? []).map((g) => [g.key, g.count])));
  const mode = $derived(view.group);
</script>

{#if groups?.length > 1}
  <div class="flex items-center justify-end gap-2 px-2 pt-2 text-xs text-neutral-400">
    <label for="jump">{groups.length} {mode === 'day' ? 'days' : mode === 'month' ? 'months' : 'years'}</label>
    <select
      id="jump"
      class="rounded border border-neutral-700 bg-neutral-900 px-1 py-0.5 text-neutral-200"
      onchange={(e) => {
        onjump(e.currentTarget.value);
        e.currentTarget.selectedIndex = 0;
      }}
    >
      <option value="" disabled selected>Jump to…</option>
      {#each groups as g (g.key)}
        <option value={g.key}>{groupLabel(g.key, mode)} ({g.count})</option>
      {/each}
    </select>
  </div>
{/if}

{#each sections as section (section.key ?? 'all')}
  {#if section.key !== null}
    <h2
      id="group-{section.key || 'undated'}"
      class="sticky top-0 z-10 flex items-baseline gap-2 bg-neutral-950/90 px-2 pb-1.5 pt-3 backdrop-blur"
    >
      <span class="text-sm font-medium text-neutral-100">{groupLabel(section.key, mode)}</span>
      <span class="text-xs tabular-nums text-neutral-500">{counts.get(section.key) ?? section.items.length}</span>
      {#if section.key}
        <button
          class="ml-auto text-xs text-neutral-500 hover:text-sky-400"
          title="Set the date filter to this {mode}"
          onclick={() => filterToGroup(section.key, mode)}>Only this {mode}</button
        >
      {/if}
    </h2>
  {/if}
  <div class="grid grid-cols-[repeat(auto-fill,minmax(168px,1fr))] gap-1 p-1">
    {#each section.items as item (item.id)}
      <button
        id="tile-{item.id}"
        class="group relative aspect-square overflow-hidden bg-neutral-900 outline-none focus-visible:ring-2 focus-visible:ring-sky-500
          {view.photo === item.id ? 'ring-2 ring-sky-500' : ''}
          {highlight === item.id ? 'ring-4 ring-amber-400 transition-shadow' : ''}"
        onclick={() => (view.photo = item.id)}
        title={item.rel_path}
      >
        <img
          src={item.thumb}
          alt={item.rel_path}
          loading="lazy"
          decoding="async"
          class="h-full w-full object-cover transition-transform duration-200 group-hover:scale-[1.03]"
        />
        <div class="pointer-events-none absolute left-1 top-1 flex gap-1">
          {#if item.has_raw}
            <span class="rounded bg-black/70 px-1 text-[10px] font-semibold tracking-wide text-amber-300">RAW</span>
          {/if}
          {#if item.dupe_count > 1}
            <span class="rounded bg-black/70 px-1 text-[10px] font-semibold text-neutral-200">×{item.dupe_count}</span>
          {/if}
        </div>
        {#if item.score !== undefined}
          <span class="pointer-events-none absolute bottom-1 right-1 rounded bg-black/60 px-1 text-[10px] tabular-nums text-neutral-300 opacity-0 group-hover:opacity-100">
            {item.score.toFixed(3)}
          </span>
        {/if}
      </button>
    {/each}
  </div>
{/each}

<div bind:this={sentinel} class="h-12 py-4 text-center text-xs text-neutral-500">
  {#if loading}Loading…{:else if !items.length}No photos{/if}
</div>
