<script>
  import { tick, untrack } from 'svelte';
  import { view, readUrl, urlFor, clearSearch, groupKey, toggleHideRejected } from './lib/state.svelte.js';
  import { fetchResults, fetchTags, fetchFacets, fetchIndexStatus, fetchSources, fetchIds } from './lib/api.js';
  import { culling, selection, cursor, flagOf, setFlag, undo, clearSelection } from './lib/culling.svelte.js';
  import TopBar from './components/TopBar.svelte';
  import Sidebar from './components/Sidebar.svelte';
  import Grid from './components/Grid.svelte';
  import Detail from './components/Detail.svelte';
  import UnmatchedRaws from './components/UnmatchedRaws.svelte';
  import Library from './components/Library.svelte';
  import SelectionBar from './components/SelectionBar.svelte';
  import Compare from './components/Compare.svelte';
  import ExportDialog from './components/ExportDialog.svelte';

  const PAGE = 120;

  readUrl();

  let tags = $state(null);
  let items = $state([]);
  let total = $state(0);
  let loading = $state(false);
  let error = $state('');
  let topBar;
  let grid = $state(); // bound inside an {#if}
  let token = 0;
  let loaded = false; // first page of the current query has arrived

  let facets = $state(null);
  let sidebarToken = 0;
  function loadSidebar() {
    const mine = ++sidebarToken;
    Promise.all([fetchTags(view), fetchFacets(view)])
      .then(([t, f]) => {
        if (mine !== sidebarToken) return;
        tags = t;
        facets = f;
      })
      .catch((e) => (error = e.message));
  }

  // Tag counts and filter options are relative to the current filter.
  $effect(() => {
    view.tags, JSON.stringify(view.filters);
    untrack(loadSidebar);
  });

  // Flag changes alter the flag counts and the Export total; refresh shortly after a burst of them.
  let sidebarTimer;
  $effect(() => {
    if (!culling.version) return;
    clearTimeout(sidebarTimer);
    sidebarTimer = setTimeout(loadSidebar, 400);
  });

  // Background indexing: poll while a run is active, then refresh everything when it ends.
  let indexStatus = $state(null);
  let polling = false;
  // `started`: the caller just kicked off a run, so refresh even if it finished before the first poll.
  let refreshPending = false;
  async function pollIndex(started = false) {
    refreshPending ||= started;
    if (polling) return;
    polling = true;
    try {
      while (true) {
        const s = await fetchIndexStatus();
        refreshPending ||= indexStatus?.running;
        indexStatus = s;
        if (refreshPending && !s.running) {
          refreshPending = false;
          loadSidebar();
          reset();
        }
        if (!s.running) break;
        await new Promise((r) => setTimeout(r, 1000));
      }
    } catch (e) {
      error = e.message;
    } finally {
      polling = false;
    }
  }
  pollIndex();

  // First run: open the library so there is an obvious way to add photos.
  fetchSources()
    .then((s) => {
      if (!s.sources.length) view.library = true;
    })
    .catch(() => {});

  // Date groups of the whole result set (only when browsing with grouping on).
  let groups = $state([]);
  let inflight = null; // the page request in progress, so callers can await it

  function loadMore() {
    if (inflight) return inflight;
    if (loaded && items.length >= total) return Promise.resolve();
    const mine = token;
    loading = true;
    inflight = (async () => {
      try {
        const page = await fetchResults(view, items.length, PAGE);
        if (mine !== token) return;
        items = [...items, ...page.items];
        total = page.total;
        groups = page.groups ?? [];
        loaded = true;
        error = '';
      } catch (e) {
        if (mine === token) error = e.message;
      } finally {
        if (mine === token) {
          loading = false;
          inflight = null;
        }
      }
    })();
    return inflight;
  }

  async function reset() {
    token++;
    clearSelection();
    items = [];
    total = 0;
    groups = [];
    loaded = false;
    loading = false;
    inflight = null;
    await loadMore();
    if (pendingJump) {
      const { key, id } = pendingJump;
      pendingJump = null;
      jumpToGroup(key, id);
    }
  }

  // Re-query whenever the search, filters or grouping change.
  $effect(() => {
    view.q, view.similar, view.tags, JSON.stringify(view.filters), view.group, view.collapse;
    untrack(reset);
  });

  // ---- jumping to a date group --------------------------------------------------

  let highlight = $state(null); // photo id briefly marked after "Show in timeline"
  let pendingJump = null; // applied once the re-query triggered by the jump has loaded

  const grouped = $derived(!!view.group && !view.q && !view.similar);

  /** Load pages until the group is in the grid, then scroll to it (and its photo). */
  async function jumpToGroup(key, photoId = null) {
    const i = groups.findIndex((g) => g.key === key);
    if (i < 0) return;
    const end = groups.slice(0, i + 1).reduce((n, g) => n + g.count, 0);
    while (items.length < end && items.length < total) {
      const before = items.length;
      await loadMore();
      if (items.length === before) break; // failed or superseded
    }
    await tick();
    const tile = photoId != null && document.getElementById(`tile-${photoId}`);
    const target = tile || document.getElementById(`group-${key || 'undated'}`);
    target?.scrollIntoView({ block: tile ? 'center' : 'start' });
    if (tile) {
      highlight = photoId;
      setTimeout(() => {
        if (highlight === photoId) highlight = null;
      }, 2500);
    }
  }

  /** From the detail view: browse the photo's date group in the grouped grid. */
  function showInTimeline(photo) {
    const mode = view.group || 'day';
    const key = groupKey(photo.taken_at, mode);
    const requery = view.q || view.similar || view.group !== mode;
    view.photo = null;
    if (requery) {
      pendingJump = { key, id: photo.id };
      view.q = '';
      view.similar = null;
      view.group = mode;
    } else {
      jumpToGroup(key, photo.id);
    }
  }

  // Mirror state into the URL. New searches get a history entry; opening photos does not.
  let lastSearchKey = urlFor({ ...view, photo: null, raws: false });
  $effect(() => {
    const url = urlFor(view);
    const searchKey = urlFor({ ...view, photo: null, raws: false });
    untrack(() => {
      if (url === location.search || (url === location.pathname && !location.search)) return;
      if (searchKey !== lastSearchKey) history.pushState(null, '', url);
      else history.replaceState(null, '', url);
      lastSearchKey = searchKey;
    });
  });

  function onpopstate() {
    readUrl();
    lastSearchKey = urlFor({ ...view, photo: null, raws: false });
  }

  const hasMore = $derived(items.length < total);

  // Flags change locally before the list is re-queried; with a flag filter active
  // (e.g. Hide rejected), photos whose new flag no longer matches disappear at once.
  const visibleItems = $derived.by(() => {
    const wanted = view.filters.flag;
    if (!wanted.length) return items;
    return items.filter((item) => wanted.includes(flagOf(item) ?? 'none'));
  });
  const itemsById = $derived(new Map(items.map((i) => [i.id, i])));
  const index = $derived(visibleItems.findIndex((i) => i.id === view.photo));
  const shows = (flag) => !view.filters.flag.length || view.filters.flag.includes(flag ?? 'none');

  async function step(delta) {
    if (view.photo == null) return;
    let i = index + delta;
    if (i >= visibleItems.length && hasMore) await loadMore();
    if (i >= 0 && i < visibleItems.length) view.photo = visibleItems[i].id;
  }

  /** P / X / U in the loupe: flag, then go on if auto-advance is on (or the photo just got hidden). */
  async function flagInLoupe(flag) {
    const id = view.photo;
    const nextId = visibleItems[index + 1]?.id ?? null;
    const prevId = visibleItems[index - 1]?.id ?? null;
    setFlag([id], flag);
    if (!culling.autoAdvance && shows(flag)) return;
    if (nextId != null) view.photo = nextId;
    else if (hasMore) {
      await loadMore();
      view.photo = visibleItems[visibleItems.findIndex((i) => i.id === prevId) + 1]?.id ?? prevId;
    } else if (!shows(flag)) view.photo = prevId;
  }

  /** P / X / U in the grid: flag the selection (or the focused photo). If that hides
   * them, move the selection to the next visible photo so you can keep going. */
  function flagInGrid(flag) {
    const targets = selection.size ? [...selection] : cursor.focus != null ? [cursor.focus] : [];
    if (!targets.length) return;
    const set = new Set(targets);
    const last = Math.max(...targets.map((id) => visibleItems.findIndex((i) => i.id === id)));
    const next = visibleItems.slice(last + 1).find((i) => !set.has(i.id)) ?? visibleItems.slice(0, last).reverse().find((i) => !set.has(i.id));
    setFlag(targets, flag);
    if (!shows(flag)) {
      selection.clear();
      if (next) {
        selection.add(next.id);
        cursor.focus = cursor.anchor = next.id;
      }
    }
  }

  async function selectAll() {
    const ids = view.q || view.similar ? visibleItems.map((i) => i.id) : (await fetchIds(view)).ids;
    selection.clear();
    for (const id of ids) selection.add(id);
  }

  const ARROWS = { ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down' };
  const FLAG_KEYS = { p: 'pick', x: 'reject', u: null };

  function onkeydown(e) {
    const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement;
    const mod = e.ctrlKey || e.metaKey;
    const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;

    if (view.compare) return; // the compare view handles its own keys
    if (key === 'Escape') {
      if (view.exporting) view.exporting = false;
      else if (view.library) view.library = false;
      else if (view.raws) view.raws = false;
      else if (view.photo != null) view.photo = null;
      else if (typing) e.target.blur();
      else clearSelection();
      return;
    }
    if (typing || view.exporting || view.library || view.raws) return;

    if (mod && key === 'z') {
      e.preventDefault();
      undo();
    } else if (mod && key === 'a' && view.photo == null) {
      e.preventDefault();
      selectAll();
    } else if (mod || e.altKey) {
      return;
    } else if (key === '/') {
      e.preventDefault();
      topBar.focus();
    } else if (key in FLAG_KEYS) {
      if (view.photo != null) flagInLoupe(FLAG_KEYS[key]);
      else flagInGrid(FLAG_KEYS[key]);
    } else if (view.photo != null) {
      if (key === 'ArrowRight' || key === 'ArrowDown') {
        e.preventDefault();
        step(1);
      } else if (key === 'ArrowLeft' || key === 'ArrowUp') {
        e.preventDefault();
        step(-1);
      }
    } else if (ARROWS[key]) {
      e.preventDefault();
      grid?.moveFocus(ARROWS[key], e.shiftKey);
    } else if ((key === 'Enter' || key === ' ') && cursor.focus != null && !(e.target instanceof HTMLButtonElement)) {
      e.preventDefault();
      view.photo = cursor.focus;
    } else if (key === 'c' && selection.size >= 2 && selection.size <= 30) {
      view.compare = { kind: 'ids', ids: [...selection] };
    } else if (key === 's') {
      view.collapse = view.collapse === 'stacks' ? 'dupes' : 'stacks';
    } else if (key === 'h') {
      toggleHideRejected();
    } else if (key === 'r') {
      view.compare = { kind: 'review' };
    }
  }
</script>

<svelte:window {onkeydown} {onpopstate} />

<div class="flex h-full flex-col">
  <TopBar bind:this={topBar} {total} {loading} {indexStatus} textSearch={tags?.text_search ?? true} picks={tags?.picks ?? 0} />
  <div class="flex min-h-0 flex-1">
    <Sidebar {tags} {facets} />
    <main class="min-w-0 flex-1 overflow-y-auto">
      {#if error || culling.error}
        <div class="m-4 rounded border border-red-900 bg-red-950/50 p-3 text-sm text-red-300">
          {error || `Could not save flags: ${culling.error}`}
          {#if view.q || view.similar}
            <button class="ml-2 underline" onclick={clearSearch}>Clear search</button>
          {/if}
        </div>
      {/if}
      {#if tags && tags.photos === 0 && !loading}
        <div class="flex h-full flex-col items-center justify-center gap-3 text-neutral-400">
          {#if indexStatus?.running}
            <p>Indexing… photos appear here when it finishes.</p>
          {:else}
            <p>No photos in the library yet.</p>
            <button class="rounded bg-sky-700 px-4 py-2 text-sm font-medium text-white hover:bg-sky-600" onclick={() => (view.library = true)}>
              Add a photo folder
            </button>
          {/if}
        </div>
      {:else}
        <Grid
          bind:this={grid}
          items={visibleItems}
          {loading}
          {hasMore}
          onmore={loadMore}
          groups={grouped ? groups : null}
          {highlight}
          onjump={jumpToGroup}
        />
        <SelectionBar onselectall={selectAll} />
      {/if}
    </main>
  </div>
</div>

{#if view.photo != null}
  <Detail
    id={view.photo}
    hasPrev={index > 0}
    hasNext={index >= 0 && (index < visibleItems.length - 1 || hasMore)}
    onstep={step}
    ontimeline={showInTimeline}
    onflag={flagInLoupe}
  />
{/if}

{#if view.compare}
  {#key view.compare}
    <Compare context={view.compare} {itemsById} />
  {/key}
{/if}

{#if view.exporting}
  <ExportDialog picksTotal={tags?.picks ?? 0} picksFiltered={facets?.flag?.pick ?? 0} />
{/if}

{#if view.raws}
  <UnmatchedRaws />
{/if}

{#if view.library}
  <Library status={indexStatus} onchange={() => pollIndex(true)} />
{/if}
