<script>
  // The photo grid, virtualised: the listing's layout (groups, rows) is computed from
  // its counts, only the rows near the viewport are in the page, and their photos'
  // pages are fetched as they come into view. Rows not loaded yet show placeholders.
  import { untrack } from 'svelte';
  import { view, prefs, TILE_SIZES, groupLabel, filterToGroup, isLocationGroup, dateSpan } from '../lib/state.svelte.js';
  import { selection, cursor, flagOf } from '../lib/culling.svelte.js';
  import { GAP, HEADER, columns, blocks, totalHeight, blockAt, rowsBetween, rowTop, move, hits } from '../lib/listing-layout.js';

  // listing: the Listing (listing.svelte.js) shown.
  // oncontext(event, id): right-click on a photo (App shows the context menu).
  let { listing, highlight = null, oncontext = null } = $props();

  let container;
  let scroller = null; // the scrolling <main> around the grid
  let width = $state(0);
  let scrollY = $state(0); // the viewport's top, in grid coordinates
  let viewH = $state(800);

  // Where the grid starts within the scroller's content (an error box may sit above it).
  const gridTop = () => container.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop;

  function measure() {
    if (!scroller || !container) return;
    width = container.clientWidth;
    viewH = scroller.clientHeight;
    scrollY = scroller.scrollTop - gridTop();
  }

  $effect(() => {
    scroller = container.closest('main');
    measure();
    let frame = 0;
    const onscroll = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(measure);
    };
    scroller.addEventListener('scroll', onscroll, { passive: true });
    const ro = new ResizeObserver(onscroll);
    ro.observe(scroller);
    ro.observe(container);
    return () => {
      cancelAnimationFrame(frame);
      scroller.removeEventListener('scroll', onscroll);
      ro.disconnect();
    };
  });

  // A new query empties the grid (the scroller snaps back to the top): measure again.
  $effect(() => {
    listing.loaded;
    untrack(measure);
  });

  // ---- layout --------------------------------------------------------------------

  // Tile size (Small / Medium / Large in the toolbar). Large tiles may use the 1600 px
  // preview on sharp screens: the 320 px thumbnail, cropped square, would look soft.
  const minTile = $derived(TILE_SIZES[prefs.tileSize] ?? TILE_SIZES.medium);
  const large = $derived(prefs.tileSize === 'large');
  const metrics = $derived(columns(width, minTile));
  const cols = $derived(metrics.cols);
  const tile = $derived(metrics.tile);
  const grouped = $derived(!!listing.groups);
  const bs = $derived(blocks(listing.sections, cols, tile, grouped));
  const height = $derived(totalHeight(bs));
  // The rows in view, plus a screen above and below.
  const rows = $derived(width ? rowsBetween(bs, cols, tile, scrollY - viewH, scrollY + 2 * viewH) : []);
  const shownBlocks = $derived(grouped ? bs.filter((b) => b.top + b.height > scrollY - viewH && b.top < scrollY + 2 * viewH) : []);
  const current = $derived(grouped && bs.length ? bs[blockAt(bs, Math.max(0, scrollY))] : null);

  // Fetch the pages of the rows in view, once scrolling pauses briefly (dragging the
  // scrollbar through the library should not request every page on the way).
  $effect(() => {
    if (!rows.length) return;
    const from = listing.offsetAt(rows[0].from);
    const to = listing.offsetAt(Math.max(rows[0].from, rows.at(-1).to - 1));
    const timer = setTimeout(() => untrack(() => listing.ensure(from, to)), 60);
    return () => clearTimeout(timer);
  });

  const mode = $derived(view.group);
  const labelOf = (key) => groupLabel(key, mode, listing.group(key)?.label);
  const range = (from, to) => Array.from({ length: to - from }, (_, i) => from + i);

  /** Scroll so that a group's header is at the top. */
  export function scrollToGroup(key) {
    const b = bs.find((b) => b.key === key);
    if (b && scroller) scroller.scrollTop = gridTop() + b.top;
  }

  /** Scroll a photo (by listing offset) into view: 'nearest' or 'center'. */
  export function scrollToOffset(offset, block = 'nearest') {
    if (!scroller) return;
    const top = gridTop() + rowTop(bs, cols, tile, listing.visibleIndex(offset));
    const h = scroller.clientHeight;
    const cover = grouped ? HEADER : 0; // the sticky header hides the top of the view
    if (block === 'center') scroller.scrollTop = top - (h - tile) / 2;
    else if (top < scroller.scrollTop + cover) scroller.scrollTop = top - cover - GAP;
    else if (top + tile > scroller.scrollTop + h) scroller.scrollTop = top + tile + GAP - h;
  }

  // Keep the open photo in view (and focused) while stepping through the loupe.
  $effect(() => {
    const id = view.photo;
    if (id == null) return;
    cursor.focus = id;
    const o = listing.offsetOf(id);
    if (o !== undefined) untrack(() => scrollToOffset(o));
  });

  // ---- selection -----------------------------------------------------------------

  async function selectRange(fromId, toId, add = false) {
    const b = listing.offsetOf(toId);
    if (b === undefined) return;
    const ids = await listing.idsBetween(listing.offsetOf(fromId) ?? b, b);
    if (!add) selection.clear();
    for (const id of ids) selection.add(id);
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
   * up/down go to the same column in the row above or below (across groups). */
  export async function moveFocus(dir, extend = false) {
    if (!listing.visibleTotal) return;
    const current = cursor.focus != null ? listing.offsetOf(cursor.focus) : undefined;
    const v = current === undefined ? 0 : move(bs, cols, listing.visibleIndex(current), dir);
    if (v < 0) return;
    const offset = listing.offsetAt(v);
    const item = await listing.load(offset);
    if (!item) return;
    const next = item.id;
    if (extend) {
      await selectRange(cursor.anchor ?? cursor.focus ?? next, next);
    } else {
      selection.clear();
      selection.add(next);
      cursor.anchor = next;
    }
    cursor.focus = next;
    scrollToOffset(offset);
  }

  // ---- marquee (drag to select) -------------------------------------------------

  let marquee = $state(null); // {x0, y0, x1, y1} in grid coordinates
  let dragStart = null;
  let baseSelection = [];
  let marked = []; // visible positions inside the marquee

  const local = (e) => {
    const r = container.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  };

  function applyMarquee() {
    selection.clear();
    for (const id of baseSelection) selection.add(id);
    for (const v of marked) {
      const item = listing.itemAt(listing.offsetAt(v));
      if (item) selection.add(item.id);
    }
  }

  function onpointerdown(e) {
    if (e.button !== 0 || e.target.closest('button, a, select, input')) return;
    dragStart = { x: e.clientX, y: e.clientY, at: local(e), add: e.shiftKey || e.ctrlKey || e.metaKey };
  }

  function onpointermove(e) {
    if (!dragStart) return;
    if (!marquee) {
      if (Math.hypot(e.clientX - dragStart.x, e.clientY - dragStart.y) < 6) return;
      baseSelection = dragStart.add ? [...selection] : [];
      e.currentTarget.setPointerCapture?.(e.pointerId);
    }
    const [x, y] = local(e);
    marquee = { x0: dragStart.at[0], y0: dragStart.at[1], x1: x, y1: y };
    marked = hits(bs, cols, tile, marquee.x0, marquee.y0, x, y);
    applyMarquee();
  }

  async function onpointerup() {
    const dragged = !!marquee;
    marquee = null;
    dragStart = null;
    if (!dragged) return;
    suppressClick = true; // the click that ends a drag must not reset the selection
    setTimeout(() => (suppressClick = false), 0);
    if (marked.length) {
      // Photos in rows not loaded yet: load them, then select them too.
      await listing.ensure(listing.offsetAt(marked[0]), listing.offsetAt(marked.at(-1)));
      applyMarquee();
    }
    const last = [...selection].at(-1);
    if (last != null) cursor.focus = cursor.anchor = last;
    marked = [];
  }
</script>

{#snippet header(b, sticky)}
  <!-- The sticky copy is decoration: the header in the flow is the real one. -->
  <svelte:element
    this={sticky ? 'div' : 'h2'}
    class="absolute inset-x-0 flex items-baseline gap-2 px-2 pt-3 {sticky ? 'z-20 bg-neutral-950/90 backdrop-blur' : 'bg-neutral-950'}"
    style="top: {sticky ? 0 : b.top}px; height: {HEADER}px"
    aria-hidden={sticky ? 'true' : undefined}
  >
    <span class="text-sm font-medium text-neutral-100">{labelOf(b.key)}</span>
    {#if isLocationGroup(mode) && listing.group(b.key)?.first}
      <span class="text-xs text-neutral-400">{dateSpan(listing.group(b.key).first, listing.group(b.key).last)}</span>
    {/if}
    <span class="text-xs tabular-nums text-neutral-500">{b.total}</span>
    {#if b.key && mode !== 'similar'}
      <button
        class="ml-auto text-xs text-neutral-500 hover:text-sky-400"
        title={isLocationGroup(mode) ? `Show only this ${mode}` : `Set the date filter to this ${mode}`}
        tabindex={sticky ? -1 : undefined}
        onclick={() => filterToGroup(b.key, mode)}>Only this {mode}</button
      >
    {/if}
  </svelte:element>
{/snippet}

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
  bind:this={container}
  class="relative select-none"
  style="height: {height + 48}px"
  {onpointerdown}
  {onpointermove}
  {onpointerup}
  onpointercancel={onpointerup}
>
  {#if current}
    <!-- The current group's header stays at the top while its photos scroll by. -->
    <div class="sticky top-0 z-20 h-0">{@render header(current, true)}</div>
  {/if}

  {#each shownBlocks as b (b.key)}
    {@render header(b, false)}
  {/each}

  {#each rows as r (`${r.block}:${r.row}`)}
    <div class="absolute inset-x-0 grid px-1" style="top: {r.top}px; gap: {GAP}px; grid-template-columns: repeat({cols}, minmax(0, 1fr))">
      {#each range(r.from, r.to) as v (listing.itemAt(listing.offsetAt(v))?.id ?? `slot-${v}`)}
        {@const item = listing.itemAt(listing.offsetAt(v))}
        {#if !item}
          <div class="aspect-square bg-neutral-900"></div>
        {:else}
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
            oncontextmenu={(e) => oncontext?.(e, item.id)}
            ondblclick={() => (view.photo = item.id)}
            onkeydown={(e) => e.key === 'Enter' && (view.photo = item.id)}
            title={item.rel_path}
          >
            <img
              src={item.thumb}
              srcset={large ? `${item.thumb} 320w, /previews/${item.id}.jpg 1600w` : undefined}
              sizes={large ? `${Math.round(tile * 1.5)}px` : undefined}
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
              {#if item.name_match}
                <!-- its file name, folder, fixed tag, or caption matches the search: why it comes first -->
                <span class="rounded bg-sky-900/80 px-1 text-[10px] font-medium text-sky-100">{item.name_match === 'file' ? 'name' : item.name_match}</span>
              {/if}
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
        {/if}
      {/each}
    </div>
  {/each}

  <div class="absolute inset-x-0 py-4 text-center text-xs text-neutral-500" style="top: {height}px">
    {#if !listing.loaded && listing.loading}
      {#if view.group === 'similar'}
        <span class="inline-flex items-center gap-2 text-sm text-neutral-400">
          <span class="h-4 w-4 animate-spin rounded-full border-2 border-neutral-700 border-t-sky-400"></span>
          Grouping the photos by similarity… (the first time with a large library can take a few seconds)
        </span>
      {:else}Loading…{/if}
    {:else if listing.loaded && !listing.visibleTotal}No photos{/if}
  </div>

  {#if marquee}
    <div
      class="pointer-events-none absolute z-30 border border-sky-400 bg-sky-400/10"
      style="left: {Math.min(marquee.x0, marquee.x1)}px; top: {Math.min(marquee.y0, marquee.y1)}px;
             width: {Math.abs(marquee.x1 - marquee.x0)}px; height: {Math.abs(marquee.y1 - marquee.y0)}px"
    ></div>
  {/if}
</div>
