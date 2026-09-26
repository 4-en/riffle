<script>
  import { view, groupLabel, filterToGroup, isLocationGroup, dateSpan } from '../lib/state.svelte.js';
  import { selection, cursor, flagOf } from '../lib/culling.svelte.js';

  // `groups`: the result's date groups (key, count) when the grid is grouped, else null.
  let { items, loading, hasMore, onmore, groups = null, highlight = null, onjump } = $props();

  let sentinel;
  let container;

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

  // Keep the open photo in view (and focused) while stepping through the loupe.
  $effect(() => {
    const id = view.photo;
    if (id != null) {
      cursor.focus = id;
      tileEl(id)?.scrollIntoView({ block: 'nearest' });
    }
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
  const info = $derived(new Map((groups ?? []).map((g) => [g.key, g])));
  const plural = { day: 'days', month: 'months', year: 'years', place: 'places', region: 'regions', country: 'countries' };
  const labelOf = (key) => groupLabel(key, mode, info.get(key)?.label);
  const mode = $derived(view.group);
  const order = $derived(new Map(items.map((item, i) => [item.id, i])));

  const tileEl = (id) => document.getElementById(`tile-${id}`);

  // ---- selection -----------------------------------------------------------------

  function selectRange(fromId, toId, add = false) {
    const a = order.get(fromId) ?? order.get(toId);
    const b = order.get(toId);
    if (b === undefined) return;
    if (!add) selection.clear();
    for (let i = Math.min(a, b); i <= Math.max(a, b); i++) selection.add(items[i].id);
  }

  let suppressClick = false;

  function onTileClick(e, id) {
    if (suppressClick) {
      suppressClick = false;
      return;
    }
    if (e.shiftKey) {
      selectRange(cursor.anchor ?? id, id, e.ctrlKey || e.metaKey);
    } else if (e.ctrlKey || e.metaKey) {
      selection.has(id) ? selection.delete(id) : selection.add(id);
      cursor.anchor = id;
    } else {
      selection.clear();
      selection.add(id);
      cursor.anchor = id;
    }
    cursor.focus = id;
  }

  /** Arrow-key navigation, called by App. Left/right follow the listing order;
   * up/down go to the nearest tile in the row above or below (across sections). */
  export function moveFocus(dir, extend = false) {
    if (!items.length) return;
    let next;
    const current = cursor.focus != null && order.has(cursor.focus) ? cursor.focus : null;
    if (current === null) {
      next = items[0].id;
    } else if (dir === 'left' || dir === 'right') {
      const i = order.get(current) + (dir === 'right' ? 1 : -1);
      if (i < 0 || i >= items.length) return;
      next = items[i].id;
    } else {
      const r = tileEl(current)?.getBoundingClientRect();
      if (!r) return;
      const cx = r.left + r.width / 2;
      const tiles = [...container.querySelectorAll('[data-tile]')].map((el) => ({ id: Number(el.dataset.tile), r: el.getBoundingClientRect() }));
      const rows = tiles.filter((t) => (dir === 'down' ? t.r.top > r.top + 5 : t.r.top < r.top - 5));
      if (!rows.length) return;
      const rowTop = dir === 'down' ? Math.min(...rows.map((t) => t.r.top)) : Math.max(...rows.map((t) => t.r.top));
      const row = rows.filter((t) => Math.abs(t.r.top - rowTop) < 5);
      next = row.reduce((best, t) => (Math.abs(t.r.left + t.r.width / 2 - cx) < Math.abs(best.r.left + best.r.width / 2 - cx) ? t : best)).id;
    }
    if (extend) {
      selectRange(cursor.anchor ?? current ?? next, next);
    } else {
      selection.clear();
      selection.add(next);
      cursor.anchor = next;
    }
    cursor.focus = next;
    tileEl(next)?.scrollIntoView({ block: 'nearest' });
  }

  // ---- marquee (drag to select) -------------------------------------------------

  let marquee = $state(null); // {x0, y0, x1, y1} in client coordinates
  let dragStart = null;
  let baseSelection = [];

  function onpointerdown(e) {
    if (e.button !== 0 || e.target.closest('button, a, select, input')) return;
    dragStart = { x: e.clientX, y: e.clientY, add: e.shiftKey || e.ctrlKey || e.metaKey };
  }

  function onpointermove(e) {
    if (!dragStart) return;
    if (!marquee) {
      if (Math.hypot(e.clientX - dragStart.x, e.clientY - dragStart.y) < 6) return;
      baseSelection = dragStart.add ? [...selection] : [];
      e.currentTarget.setPointerCapture?.(e.pointerId);
    }
    marquee = { x0: dragStart.x, y0: dragStart.y, x1: e.clientX, y1: e.clientY };
    const [l, r] = [Math.min(marquee.x0, marquee.x1), Math.max(marquee.x0, marquee.x1)];
    const [t, b] = [Math.min(marquee.y0, marquee.y1), Math.max(marquee.y0, marquee.y1)];
    selection.clear();
    for (const id of baseSelection) selection.add(id);
    for (const el of container.querySelectorAll('[data-tile]')) {
      const rect = el.getBoundingClientRect();
      if (rect.right > l && rect.left < r && rect.bottom > t && rect.top < b) selection.add(Number(el.dataset.tile));
    }
  }

  function onpointerup() {
    if (marquee) {
      suppressClick = true; // the click that ends a drag must not reset the selection
      setTimeout(() => (suppressClick = false), 0);
      const last = [...selection].at(-1);
      if (last != null) cursor.focus = cursor.anchor = last;
    }
    marquee = null;
    dragStart = null;
  }
</script>

{#if groups?.length > 1}
  <div class="flex items-center justify-end gap-2 px-2 pt-2 text-xs text-neutral-400">
    <label for="jump">{groups.length} {plural[mode]}</label>
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
        <option value={g.key}>{labelOf(g.key)} ({g.count})</option>
      {/each}
    </select>
  </div>
{/if}

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div bind:this={container} class="min-h-full select-none" {onpointerdown} {onpointermove} {onpointerup} onpointercancel={onpointerup}>
  {#each sections as section (section.key ?? 'all')}
    {#if section.key !== null}
      <h2
        id="group-{section.key || 'undated'}"
        class="sticky top-0 z-10 flex items-baseline gap-2 bg-neutral-950/90 px-2 pb-1.5 pt-3 backdrop-blur"
      >
        <span class="text-sm font-medium text-neutral-100">{labelOf(section.key)}</span>
        {#if isLocationGroup(mode) && info.get(section.key)?.first}
          <span class="text-xs text-neutral-400">{dateSpan(info.get(section.key).first, info.get(section.key).last)}</span>
        {/if}
        <span class="text-xs tabular-nums text-neutral-500">{info.get(section.key)?.count ?? section.items.length}</span>
        {#if section.key}
          <button
            class="ml-auto text-xs text-neutral-500 hover:text-sky-400"
            title={isLocationGroup(mode) ? `Show only this ${mode}` : `Set the date filter to this ${mode}`}
            onclick={() => filterToGroup(section.key, mode)}>Only this {mode}</button
          >
        {/if}
      </h2>
    {/if}
    <div class="grid grid-cols-[repeat(auto-fill,minmax(168px,1fr))] gap-1 p-1">
      {#each section.items as item (item.id)}
        {@const flag = flagOf(item)}
        {@const selected = selection.has(item.id)}
        <div
          id="tile-{item.id}"
          data-tile={item.id}
          role="button"
          tabindex="-1"
          aria-pressed={selected}
          class="group relative aspect-square cursor-pointer overflow-hidden bg-neutral-900 outline-none
            {selected ? 'ring-2 ring-sky-500' : ''}
            {cursor.focus === item.id ? 'outline-2 outline-offset-2 outline-white/70' : ''}
            {highlight === item.id ? 'ring-4 ring-amber-400' : ''}"
          onclick={(e) => onTileClick(e, item.id)}
          ondblclick={() => (view.photo = item.id)}
          onkeydown={(e) => e.key === 'Enter' && (view.photo = item.id)}
          title={item.rel_path}
        >
          <img
            src={item.thumb}
            alt={item.rel_path}
            loading="lazy"
            decoding="async"
            draggable="false"
            class="h-full w-full object-cover transition duration-200 group-hover:scale-[1.03] {flag === 'reject' ? 'opacity-30 grayscale' : ''}"
          />
          {#if selected}
            <div class="pointer-events-none absolute inset-0 bg-sky-500/15"></div>
          {/if}
          <div class="pointer-events-none absolute left-1 top-1 flex gap-1">
            {#if item.has_raw}
              <span class="rounded bg-black/70 px-1 text-[10px] font-semibold tracking-wide text-amber-300">RAW</span>
            {/if}
            {#if item.dupe_count > 1 && view.collapse !== 'stacks'}
              <span class="rounded bg-black/70 px-1 text-[10px] font-semibold text-neutral-200">×{item.dupe_count}</span>
            {/if}
          </div>
          <div class="pointer-events-none absolute right-1 top-1 flex gap-1">
            {#if item.exported}
              <span class="rounded-full bg-sky-600 px-1.5 text-[11px] font-bold text-white" title="Exported before">↗</span>
            {/if}
            {#if flag === 'pick'}
              <span class="rounded-full bg-emerald-500 px-1.5 text-[11px] font-bold text-black" title="Picked">✓</span>
            {:else if flag === 'reject'}
              <span class="rounded-full bg-red-600 px-1.5 text-[11px] font-bold text-white" title="Rejected">✕</span>
            {/if}
          </div>
          {#if item.stack_count > 1}
            <button
              class="absolute bottom-1 left-1 rounded bg-black/70 px-1.5 text-[10px] font-semibold text-sky-200 hover:bg-sky-700 hover:text-white"
              title="Compare the {item.stack_count} photos in this stack"
              onclick={(e) => {
                e.stopPropagation();
                view.compare = { kind: 'stack', id: item.stack_id };
              }}>▦ {item.stack_count}</button
            >
          {/if}
          {#if item.score !== undefined}
            <span class="pointer-events-none absolute bottom-1 right-1 rounded bg-black/60 px-1 text-[10px] tabular-nums text-neutral-300 opacity-0 group-hover:opacity-100">
              {item.score.toFixed(3)}
            </span>
          {/if}
        </div>
      {/each}
    </div>
  {/each}

  <div bind:this={sentinel} class="h-12 py-4 text-center text-xs text-neutral-500">
    {#if loading}Loading…{:else if !items.length}No photos{/if}
  </div>
</div>

{#if marquee}
  <div
    class="pointer-events-none fixed z-20 border border-sky-400 bg-sky-400/10"
    style="left: {Math.min(marquee.x0, marquee.x1)}px; top: {Math.min(marquee.y0, marquee.y1)}px;
           width: {Math.abs(marquee.x1 - marquee.x0)}px; height: {Math.abs(marquee.y1 - marquee.y0)}px"
  ></div>
{/if}
