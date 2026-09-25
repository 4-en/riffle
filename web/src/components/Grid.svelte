<script>
  import { view } from '../lib/state.svelte.js';

  let { items, loading, hasMore, onmore } = $props();

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
</script>

<div class="grid grid-cols-[repeat(auto-fill,minmax(168px,1fr))] gap-1 p-1">
  {#each items as item (item.id)}
    <button
      id="tile-{item.id}"
      class="group relative aspect-square overflow-hidden bg-neutral-900 outline-none focus-visible:ring-2 focus-visible:ring-sky-500
        {view.photo === item.id ? 'ring-2 ring-sky-500' : ''}"
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

<div bind:this={sentinel} class="h-12 py-4 text-center text-xs text-neutral-500">
  {#if loading}Loading…{:else if !items.length}No photos{/if}
</div>
