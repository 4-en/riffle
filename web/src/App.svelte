<script>
  import { tick, untrack } from 'svelte';
  import { view, prefs, readUrl, urlFor, clearSearch, groupKey, toggleHideRejected, curateKey, DATE_GROUPS, LOCATION_GROUPS, isLocationGroup, hasOverview } from './lib/state.svelte.js';
  import { fetchTags, fetchFacets, fetchIndexStatus, fetchSources, fetchIds, fetchTaste, fetchProfiles } from './lib/api.js';
  import { culling, selection, cursor, flagOf, setFlag, undo, clearSelection } from './lib/culling.svelte.js';
  import { connection, connect } from './lib/connection.svelte.js';
  import { Listing, PAGE } from './lib/listing.svelte.js';
  import TopBar from './components/TopBar.svelte';
  import ViewBar from './components/ViewBar.svelte';
  import Sidebar from './components/Sidebar.svelte';
  import Grid from './components/Grid.svelte';
  import Detail from './components/Detail.svelte';
  import UnmatchedRaws from './components/UnmatchedRaws.svelte';
  import Library from './components/Library.svelte';
  import SelectionBar from './components/SelectionBar.svelte';
  import Compare from './components/Compare.svelte';
  import ExportDialog from './components/ExportDialog.svelte';
  import Calendar from './components/Calendar.svelte';
  import ContextMenu from './components/ContextMenu.svelte';
  import Help from './components/Help.svelte';
  import Curate from './components/Curate.svelte';
  import TagDialog from './components/TagDialog.svelte';
  import CaptionView from './components/CaptionView.svelte';
  // The map (d3 + country outlines) loads only when it is first shown.
  const loadMap = () => import('./components/MapView.svelte');
  const loadSimilarMap = () => import('./components/SimilarMap.svelte');

  readUrl();
  connect();

  let tags = $state(null);
  const listing = new Listing(); // the grid's photos, loaded in pages as needed
  let error = $state('');
  let topBar;
  let grid = $state(); // bound inside an {#if}

  let facets = $state(null);
  // Profiles (for the top bar switcher and the Library); refreshed on renames and switches.
  let profiles = $state([]);
  function loadProfiles() {
    fetchProfiles()
      .then((p) => (profiles = p.profiles))
      .catch(() => {});
  }
  $effect(() => {
    connection.profileVersion;
    untrack(loadProfiles);
  });
  // The taste model's status; refreshed with the sidebar (it retrains in the background).
  let taste = $state(null);
  function loadTaste() {
    fetchTaste()
      .then((t) => (taste = t))
      .catch(() => {});
  }
  let sidebarToken = 0;
  function loadSidebar() {
    const mine = ++sidebarToken;
    Promise.all([fetchTags(view), fetchFacets(view)])
      .then(([t, f]) => {
        if (mine !== sidebarToken) return;
        tags = t;
        facets = f;
        loadTaste();
      })
      .catch((e) => (error = e.message));
  }

  // Tag counts and filter options are relative to the current filter.
  $effect(() => {
    view.tags, view.excludeTags, view.ctags, view.excludeCtags, JSON.stringify(view.filters);
    untrack(loadSidebar);
  });

  // Once the AI model is ready, search (and the sidebar's search state) turns on.
  let modelWas = connection.model;
  $effect(() => {
    const m = connection.model;
    if (m !== modelWas && m === 'ready') untrack(loadSidebar);
    modelWas = m;
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

  async function reset() {
    clearSelection();
    await listing.reset();
    if (pendingJump) {
      const { key, id } = pendingJump;
      pendingJump = null;
      jumpToGroup(key, id);
    }
  }

  // Re-query whenever the search, filters or grouping change.
  $effect(() => {
    view.q, view.similar, view.tags, view.excludeTags, view.ctags, view.excludeCtags, JSON.stringify(view.filters), view.group, view.level, view.collapse, view.sort, prefs.nameMatch;
    untrack(reset);
  });

  // ---- jumping to a date group --------------------------------------------------

  let highlight = $state(null); // photo id briefly marked after "Show in timeline"
  let pendingJump = null; // applied once the re-query triggered by the jump has loaded

  const grouped = $derived(!!view.group && !view.q && !view.similar);

  /** Scroll to a group, and to one of its photos (loading the group's pages until it turns up). */
  async function jumpToGroup(key, photoId = null) {
    const g = listing.group(key);
    if (!g) return;
    await tick(); // the grid may just have come back
    grid?.scrollToGroup(key);
    if (photoId == null) return;
    let offset = listing.offsetOf(photoId);
    for (let p = g.start; offset === undefined && p < g.start + g.count; p += PAGE) {
      await listing.ensure(p);
      offset = listing.offsetOf(photoId);
    }
    if (offset === undefined) return;
    await tick();
    grid?.scrollToOffset(offset, 'center');
    highlight = photoId;
    setTimeout(() => {
      if (highlight === photoId) highlight = null;
    }, 2500);
  }

  /** From an overview (calendar or map): show that group in the grouped grid. */
  async function openGroup(mode, key) {
    const requery = view.q || view.similar || view.group !== mode;
    view.overview = false;
    if (requery) {
      pendingJump = { key, id: null };
      view.q = '';
      view.similar = null;
      view.group = mode;
    } else {
      await tick(); // the grid is back
      jumpToGroup(key);
    }
  }

  // Curate takes the text search with it (as a score), but not "Find similar", which ends it.
  $effect(() => {
    if (view.curate && view.similar) view.curate = false;
  });

  /** Captions or fixed tags changed: refresh the sidebar's Fixed tags, and the grid
   * when fixed tags filter it or a search may match them. */
  let captionTimer;
  function captionsChanged() {
    clearTimeout(captionTimer);
    captionTimer = setTimeout(() => {
      loadSidebar();
      if (view.filters.ftags.length || view.filters.exclude_ftags.length || (view.q && prefs.nameMatch)) reset();
    }, 400);
  }

  /** A custom tag was created, edited, or deleted: refresh the sidebar, and the grid
   * if the tag is filtering it (a deleted one leaves the filters). */
  function customTagChanged({ id, deleted = false }) {
    const inUse = view.ctags.includes(id) || view.excludeCtags.includes(id);
    if (deleted) {
      view.ctags = view.ctags.filter((t) => t !== id);
      view.excludeCtags = view.excludeCtags.filter((t) => t !== id);
    } else if (inUse) {
      reset();
    }
    loadSidebar();
  }

  // An overview needs a grouping and makes no sense for search results.
  $effect(() => {
    if (view.overview && (!hasOverview(view.group) || view.q || view.similar)) view.overview = false;
  });

  /** From the detail view: browse the photo's date group ('date') or place ('location')
   * in the grouped grid, keeping the current level of that kind if one is active. */
  function showInTimeline(photo, kind = 'date') {
    const levels = kind === 'location' ? LOCATION_GROUPS : DATE_GROUPS;
    const mode = levels.includes(view.group) ? view.group : levels[0];
    const key = groupKey(photo, mode);
    const requery = view.q || view.similar || view.group !== mode;
    view.photo = null;
    view.curate = false;
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

  // Compare looks photos up here before asking the server.
  const itemsById = { get: (id) => listing.byId(id) };
  // While Curate is open, the photo view steps through the draft instead of the grid.
  let curateOrder = $state([]);
  const curateIndex = $derived(view.curate ? curateOrder.indexOf(view.photo) : -1);
  // The open photo's place in the grid's listing (-1: not in it, e.g. opened from a map).
  const photoOffset = $derived(view.photo == null ? -1 : (listing.offsetOf(view.photo) ?? -1));
  const neighbour = (dir) => (photoOffset < 0 ? -1 : listing.nextVisible(photoOffset, dir));
  const hasPrev = $derived(view.curate ? curateIndex > 0 : neighbour(-1) >= 0);
  const hasNext = $derived(view.curate ? curateIndex >= 0 && curateIndex < curateOrder.length - 1 : neighbour(1) >= 0);
  const shows = (flag) => !view.filters.flag.length || view.filters.flag.includes(flag ?? 'none');

  /** Show the photo at a listing offset in the photo view (if it is still on `from`). */
  async function openAt(offset, from) {
    const item = offset >= 0 ? await listing.load(offset) : null;
    if (item && view.photo === from) view.photo = item.id;
  }

  async function step(delta) {
    if (view.photo == null) return;
    if (view.curate) {
      const i = curateIndex + delta;
      if (curateIndex >= 0 && i >= 0 && i < curateOrder.length) view.photo = curateOrder[i];
      return;
    }
    await openAt(neighbour(delta), view.photo);
  }

  /** P / X / U in the loupe: flag, then go on if auto-advance is on (or the photo just got hidden). */
  async function flagInLoupe(flag) {
    const id = view.photo;
    if (view.curate) return setFlag([id], flag); // the draft does not change when flagging
    const next = neighbour(1);
    const prev = neighbour(-1);
    setFlag([id], flag);
    if (!culling.autoAdvance && shows(flag)) return;
    if (next >= 0) await openAt(next, id);
    else if (!shows(flag)) await openAt(prev, id);
  }

  // Right-click menu on a grid photo. Like a file manager: right-clicking a photo
  // outside the selection selects just it; inside, the selection is kept.
  let menuAt = $state(null); // {x, y, id}
  function openMenu(e, id) {
    e.preventDefault();
    if (!selection.has(id)) {
      selection.clear();
      selection.add(id);
      cursor.anchor = id;
    }
    cursor.focus = id;
    menuAt = { x: e.clientX, y: e.clientY, id };
  }

  /** The first shown photo from `offset` in direction dir that is not in `skip`. */
  async function nextShown(offset, dir, skip) {
    for (let o = listing.nextVisible(offset, dir); o >= 0; o = listing.nextVisible(o, dir)) {
      const item = await listing.load(o);
      if (!item) return null;
      if (!skip.has(item.id)) return item;
    }
    return null;
  }

  /** P / X / U in the grid: flag the selection (or the focused photo). If that hides
   * them, move the selection to the next visible photo so you can keep going. */
  async function flagInGrid(flag) {
    if (view.overview) {
      // On a map there is no grid to move through: flag the selection, that's all.
      if (selection.size) setFlag([...selection], flag);
      return;
    }
    const targets = selection.size ? [...selection] : cursor.focus != null ? [cursor.focus] : [];
    if (!targets.length) return;
    const set = new Set(targets);
    setFlag(targets, flag);
    if (shows(flag)) return;
    selection.clear();
    const offsets = targets.map((id) => listing.offsetOf(id)).filter((o) => o !== undefined);
    if (!offsets.length) return;
    const last = Math.max(...offsets);
    const next = (await nextShown(last, 1, set)) ?? (await nextShown(last, -1, set));
    if (next && !selection.size) {
      selection.add(next.id);
      cursor.focus = cursor.anchor = next.id;
    }
  }

  async function selectAll() {
    let ids;
    if (view.q || view.similar) {
      // Search results: one ranked list, loaded in full only on request.
      ids = listing.total ? await listing.idsBetween(0, listing.total - 1) : [];
    } else ids = (await fetchIds(view)).ids;
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
    if (menuAt) {
      // The menu's shortcuts work while it is open; other keys are ignored.
      const id = menuAt.id;
      if (key in FLAG_KEYS && !mod) flagInGrid(FLAG_KEYS[key]);
      else if (key === 'Enter') view.photo = id;
      else if (key === 'c' && selection.size >= 2 && selection.size <= 30) view.compare = { kind: 'ids', ids: [...selection] };
      else if (key !== 'Escape') return;
      e.preventDefault();
      menuAt = null;
      return;
    }
    if (view.tagDialog) {
      if (key === 'Escape') view.tagDialog = null;
      return; // the dialog has the keyboard
    }
    if (key === 'Escape') {
      if (view.help) view.help = false;
      else if (view.exporting) view.exporting = false;
      else if (view.library) view.library = false;
      else if (view.raws) view.raws = false;
      else if (view.photo != null) view.photo = null;
      else if (view.captioning) typing ? e.target.blur() : (view.captioning = null);
      else if (view.curate) view.curate = false;
      else if (typing) e.target.blur();
      else clearSelection();
      return;
    }
    if (typing || view.exporting || view.library || view.raws || view.help) return;
    if (view.captioning && view.photo == null) return; // the Captions & tags view has the keyboard
    if (key === '?') {
      view.help = true;
      return;
    }

    if (mod && key === 'z') {
      e.preventDefault();
      undo();
    } else if (view.curate && view.photo == null) {
      return; // the grid's shortcuts do not apply to the Curate view
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
    } else if (key === 'o' && hasOverview(view.group) && !view.q && !view.similar) {
      view.overview = !view.overview;
    } else if (key === 'r') {
      view.compare = { kind: 'review' };
    }
  }
</script>

<svelte:window {onkeydown} {onpopstate} />

{#if connection.down}
  <div class="fixed inset-x-0 top-0 z-50 border-b border-amber-800 bg-amber-950 px-4 py-2 text-center text-sm text-amber-100">
    {#if connection.autoExit}
      Riffle has stopped (its tabs were closed). Start Riffle again to continue; this page reconnects by itself.
    {:else}
      The Riffle server is not reachable. This page reconnects by itself once it is back.
    {/if}
  </div>
{/if}

<div class="flex h-full flex-col">
  {#if connection.model === 'loading' && !connection.down}
    <div class="border-b border-sky-900 bg-sky-950 px-4 py-1.5 text-center text-xs text-sky-100">
      Preparing the AI model… The first start downloads it once, which can take a few minutes. You can browse meanwhile;
      search and indexing work once it is ready.
    </div>
  {:else if connection.model === 'failed' && !connection.down}
    <div class="border-b border-red-900 bg-red-950/70 px-4 py-1.5 text-center text-xs text-red-200">
      The AI model could not be loaded, so search is off: {connection.modelError}.
      {connection.log ? `Details: ${connection.log}` : 'See the terminal for details.'} Restart Riffle to try again (it needs an internet connection the first time).
    </div>
  {/if}
  <TopBar bind:this={topBar} {indexStatus} textSearch={tags?.text_search ?? true} picks={tags?.picks ?? 0} {profiles} />
  <div class="flex min-h-0 flex-1">
    <Sidebar {tags} {facets} profile={profiles.find((p) => p.active && p.slug !== 'default')?.name} />
    <div class="flex min-w-0 flex-1 flex-col">
      {#if !(tags && tags.photos === 0 && !listing.loading)}
        <ViewBar total={listing.total} loading={listing.loading} {taste} groups={grouped && !view.overview ? listing.groups : null} onjump={jumpToGroup} />
      {/if}
      <main class="min-h-0 flex-1 overflow-y-auto">
        {#if error || listing.error || culling.error}
          <div class="m-4 rounded border border-red-900 bg-red-950/50 p-3 text-sm text-red-300">
            {error || listing.error || `Could not save flags: ${culling.error}`}
            {#if view.q || view.similar}
              <button class="ml-2 underline" onclick={clearSearch}>Clear search</button>
            {/if}
          </div>
        {/if}
        {#if tags && tags.photos === 0 && !listing.loading}
          <div class="flex h-full flex-col items-center justify-center gap-3 text-neutral-400">
            {#if indexStatus?.running}
              <p>Indexing… photos appear here when it finishes.</p>
            {:else}
              <p>No photos in the library yet.</p>
              <button class="text-xs text-sky-400 hover:underline" onclick={() => (view.help = true)}>How does this work?</button>
              <button class="rounded bg-sky-700 px-4 py-2 text-sm font-medium text-white hover:bg-sky-600" onclick={() => (view.library = true)}>
                Add a photo folder
              </button>
            {/if}
          </div>
        {:else if view.overview && view.group === 'similar'}
          {#await loadSimilarMap()}
            <p class="p-4 text-sm text-neutral-500">Loading map…</p>
          {:then { default: SimilarMap }}
            <SimilarMap onopen={openGroup} />
          {/await}
        {:else if view.overview && isLocationGroup(view.group)}
          {#await loadMap()}
            <p class="p-4 text-sm text-neutral-500">Loading map…</p>
          {:then { default: MapView }}
            <MapView onopen={openGroup} />
          {/await}
        {:else if view.overview}
          <Calendar onopen={openGroup} />
        {:else}
          <Grid bind:this={grid} {listing} {highlight} oncontext={openMenu} />
          <SelectionBar onselectall={selectAll} />
        {/if}
      </main>
    </div>
  </div>
</div>

{#if view.captioning}
  <CaptionView onchange={captionsChanged} />
{/if}

{#if view.curate}
  <!-- A new filter set (e.g. a tag clicked in the photo view) starts its own draft. -->
  {#key JSON.stringify(curateKey())}
    <Curate onorder={(ids) => (curateOrder = ids)} />
  {/key}
{/if}

{#if view.photo != null}
  <Detail
    id={view.photo}
    {hasPrev}
    {hasNext}
    onstep={step}
    ontimeline={showInTimeline}
    onflag={flagInLoupe}
  />
{/if}

{#if menuAt}
  {#key menuAt}
    <ContextMenu
      at={menuAt}
      onflag={flagInGrid}
      ontimeline={showInTimeline}
      onclose={() => (menuAt = null)}
      customTags={tags?.custom ?? []}
      ontagchange={customTagChanged}
    />
  {/key}
{/if}

{#if view.compare}
  {#key view.compare}
    <Compare context={view.compare} {itemsById} />
  {/key}
{/if}

{#if view.help}
  <Help />
{/if}

{#if view.tagDialog}
  {#key view.tagDialog}
    <TagDialog dialog={view.tagDialog} onchange={customTagChanged} />
  {/key}
{/if}

{#if view.exporting}
  <ExportDialog
    picksTotal={tags?.picks ?? 0}
    picksFiltered={facets?.flag?.pick ?? 0}
    hasHistory={tags?.location_history ?? false}
    draft={typeof view.exporting === 'object' ? view.exporting : null}
    ondone={() => {
      loadSidebar();
      reset();
    }}
  />
{/if}

{#if view.raws}
  <UnmatchedRaws />
{/if}

{#if view.library}
  <Library
    status={indexStatus}
    onchange={() => pollIndex(true)}
    {tags}
    {facets}
    {taste}
    ontaste={(t) => {
      taste = t;
      if (view.sort === 'taste' || view.sort === '-taste') reset(); // new scores: new order
    }}
    {profiles}
    onprofiles={loadProfiles}
  />
{/if}
