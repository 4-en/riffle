<script>
  import { untrack } from 'svelte';
  import { view, readUrl, urlFor, clearSearch } from './lib/state.svelte.js';
  import { fetchResults, fetchTags, fetchIndexStatus, fetchSources } from './lib/api.js';
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

  let tagsToken = 0;
  function loadTags() {
    const mine = ++tagsToken;
    fetchTags(view.tags)
      .then((t) => {
        if (mine === tagsToken) tags = t;
      })
      .catch((e) => (error = e.message));
  }

  // Tag counts are relative to the current tag filter.
  $effect(() => {
    view.tags;
    untrack(loadTags);
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
          loadTags();
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

  async function loadMore() {
    if (loading || (loaded && items.length >= total)) return;
    const mine = token;
    loading = true;
    try {
      const page = await fetchResults(view, items.length, PAGE);
      if (mine !== token) return;
      items = [...items, ...page.items];
      total = page.total;
      loaded = true;
      error = '';
    } catch (e) {
      if (mine === token) error = e.message;
    } finally {
      if (mine === token) loading = false;
    }
  }

  function reset() {
    token++;
    items = [];
    total = 0;
    loaded = false;
    loading = false;
    loadMore();
  }

  // Re-query whenever the search or tag filter changes.
  $effect(() => {
    view.q, view.similar, view.tags;
    untrack(reset);
  });

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
    <Sidebar {tags} />
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
        <Grid {items} {loading} {hasMore} onmore={loadMore} />
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
  />
{/if}

{#if view.raws}
  <UnmatchedRaws />
{/if}

{#if view.library}
  <Library status={indexStatus} onchange={() => pollIndex(true)} />
{/if}
