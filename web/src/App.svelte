<script>
  import { tick, untrack } from 'svelte';
  import { view, readUrl, urlFor, clearSearch, groupKey } from './lib/state.svelte.js';
  import { fetchResults, fetchTags, fetchFacets, fetchIndexStatus, fetchSources } from './lib/api.js';
  import TopBar from './components/TopBar.svelte';
  import Sidebar from './components/Sidebar.svelte';
  import Grid from './components/Grid.svelte';
  import Detail from './components/Detail.svelte';
  import UnmatchedRaws from './components/UnmatchedRaws.svelte';
  import Library from './components/Library.svelte';

  const PAGE = 120;

  readUrl();

  let tags = $state(null);
  let items = $state([]);
  let total = $state(0);
  let loading = $state(false);
  let error = $state('');
  let topBar;
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
    view.q, view.similar, view.tags, JSON.stringify(view.filters), view.group;
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
  const index = $derived(items.findIndex((i) => i.id === view.photo));

  async function step(delta) {
    if (view.photo == null) return;
    let i = index + delta;
    if (i >= items.length && hasMore) await loadMore();
    if (i >= 0 && i < items.length) view.photo = items[i].id;
  }

  function onkeydown(e) {
    const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement;
    if (e.key === '/' && !typing) {
      e.preventDefault();
      topBar.focus();
    } else if (e.key === 'Escape') {
      if (view.library) view.library = false;
      else if (view.photo != null) view.photo = null;
      else if (view.raws) view.raws = false;
      else if (typing) e.target.blur();
    } else if (!typing && view.photo != null && (e.key === 'ArrowRight' || e.key === 'ArrowDown')) {
      e.preventDefault();
      step(1);
    } else if (!typing && view.photo != null && (e.key === 'ArrowLeft' || e.key === 'ArrowUp')) {
      e.preventDefault();
      step(-1);
    }
  }
</script>

<svelte:window {onkeydown} {onpopstate} />

<div class="flex h-full flex-col">
  <TopBar bind:this={topBar} {total} {loading} {indexStatus} textSearch={tags?.text_search ?? true} />
  <div class="flex min-h-0 flex-1">
    <Sidebar {tags} {facets} />
    <main class="min-w-0 flex-1 overflow-y-auto">
      {#if error}
        <div class="m-4 rounded border border-red-900 bg-red-950/50 p-3 text-sm text-red-300">
          {error}
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
        <Grid {items} {loading} {hasMore} onmore={loadMore} groups={grouped ? groups : null} {highlight} onjump={jumpToGroup} />
      {/if}
    </main>
  </div>
</div>

{#if view.photo != null}
  <Detail
    id={view.photo}
    hasPrev={index > 0}
    hasNext={index >= 0 && (index < items.length - 1 || hasMore)}
    onstep={step}
    ontimeline={showInTimeline}
  />
{/if}

{#if view.raws}
  <UnmatchedRaws />
{/if}

{#if view.library}
  <Library status={indexStatus} onchange={() => pollIndex(true)} />
{/if}
