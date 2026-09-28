<script>
  // Settings: one page per section, chosen in the nav on the left (or 1–8). Other parts of
  // the app open a page directly (view.settings = 'taste'); 'last' reopens the last one.
  import { untrack } from 'svelte';
  import { view } from '../lib/state.svelte.js';
  import { fetchSources, fetchLocationHistory } from '../lib/api.js';
  import Folders from './settings/Folders.svelte';
  import Indexing from './settings/Indexing.svelte';
  import Model from './settings/Model.svelte';
  import Locations from './settings/Locations.svelte';
  import Profiles from './settings/Profiles.svelte';
  import Taste from './settings/Taste.svelte';
  import Flags from './settings/Flags.svelte';
  import Files from './settings/Files.svelte';

  // status: indexing status; onchange(): refresh the app's counts (and poll indexing).
  // tags / facets: the app's current counts (global picks and rejects; flags within the filters).
  // ontaste(status): the taste model was (re)calibrated.
  // profiles: /api/profiles list; onprofiles(): refresh it after a change.
  let { status, onchange, tags = null, facets = null, taste = null, ontaste = () => {}, profiles = [], onprofiles = () => {} } = $props();

  const PAGES = [
    ['folders', 'Photo folders'],
    ['indexing', 'Indexing'],
    ['model', 'AI model'],
    ['locations', 'Locations'],
    ['profiles', 'Profiles'],
    ['taste', 'Your taste'],
    ['flags', 'Flags & exports'],
    ['files', 'Files'],
  ];
  const KEY = 'riffle.settings.page';
  const known = (p) => PAGES.some(([id]) => id === p);

  function remembered() {
    try {
      const p = localStorage.getItem(KEY);
      return known(p) ? p : 'folders';
    } catch {
      return 'folders';
    }
  }

  // The page shown: the one asked for, else the last one used.
  const page = $derived(known(view.settings) ? view.settings : remembered());
  $effect(() => {
    const p = page;
    untrack(() => {
      if (view.settings !== p) view.settings = p;
      try {
        localStorage.setItem(KEY, p);
      } catch {}
    });
  });
  const go = (p) => (view.settings = p);
  const close = () => (view.settings = null);

  // Shared by the pages: the configured folders (with counts), the location history, errors.
  let sources = $state(null);
  let history = $state(null);
  let error = $state('');
  let busy = $state(false);

  async function loadSources() {
    try {
      sources = await fetchSources();
    } catch (e) {
      error = e.message;
    }
  }
  async function loadHistory() {
    try {
      history = await fetchLocationHistory();
    } catch (e) {
      error = e.message;
    }
  }
  // Now, and again as indexing progresses (photo counts and placements change).
  $effect(() => {
    status?.running;
    status?.finished_at;
    loadSources();
    loadHistory();
  });
  untrack(() => onprofiles()); // fresh profile counts whenever Settings opens

  /** Run a change (add a folder, index…), then refresh the folders and the app. */
  async function act(fn) {
    busy = true;
    error = '';
    try {
      await fn();
      await loadSources();
      onchange();
      return true;
    } catch (e) {
      error = e.message;
      return false;
    } finally {
      busy = false;
    }
  }
  const onerror = (message) => (error = message);

  const placed = $derived(history ? Object.values(history.placed).reduce((a, b) => a + b, 0) : 0);
  const pct = $derived(status?.total ? Math.min(100, (100 * status.done) / status.total) : null);

  function onkeydown(e) {
    const t = e.target;
    if (t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement || t?.isContentEditable) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const k = Number(e.key);
    if (k >= 1 && k <= PAGES.length) {
      e.preventDefault();
      go(PAGES[k - 1][0]);
    }
  }
</script>

<svelte:window {onkeydown} />

<div class="fixed inset-0 z-30 flex items-center justify-center bg-black/70 p-6" role="dialog" aria-modal="true" aria-label="Settings">
  <button class="absolute inset-0 cursor-default" aria-label="Close" onclick={close}></button>
  <div class="relative flex h-[min(44rem,100%)] w-full max-w-4xl flex-col overflow-hidden rounded-lg border border-neutral-800 bg-neutral-900 text-sm">
    <div class="flex items-center justify-between border-b border-neutral-800 px-4 py-2">
      <h2 class="font-semibold">Settings</h2>
      <button class="text-neutral-400 hover:text-white" aria-label="Close (Esc)" onclick={close}>✕</button>
    </div>

    {#if status?.running || status?.error}
      <div class="flex items-center gap-3 border-b border-neutral-800 bg-neutral-950/60 px-4 py-1.5 text-xs">
        {#if status.running}
          <span class="h-2 w-2 shrink-0 animate-pulse rounded-full bg-sky-500"></span>
          <span class="min-w-0 truncate text-neutral-300">{status.step}</span>
          <div class="h-1 w-32 shrink-0 overflow-hidden rounded bg-neutral-800">
            {#if pct !== null}
              <div class="h-full bg-sky-600 transition-[width]" style="width: {pct}%"></div>
            {:else}
              <div class="h-full w-1/3 animate-pulse bg-sky-700"></div>
            {/if}
          </div>
        {:else}
          <span class="min-w-0 truncate text-red-400">Indexing failed: {status.error}</span>
        {/if}
        {#if page !== 'indexing'}
          <button class="ml-auto shrink-0 text-sky-400 hover:underline" onclick={() => go('indexing')}>Show</button>
        {/if}
      </div>
    {/if}

    <div class="flex min-h-0 flex-1">
      <nav class="w-44 shrink-0 space-y-0.5 overflow-y-auto border-r border-neutral-800 p-2" aria-label="Settings pages">
        {#each PAGES as [id, label], k (id)}
          <button
            class="flex w-full items-center gap-2 rounded px-2.5 py-1.5 text-left text-xs {page === id
              ? 'bg-neutral-800 font-medium text-white'
              : 'text-neutral-400 hover:bg-neutral-800/60 hover:text-neutral-200'}"
            aria-current={page === id ? 'page' : undefined}
            title="{label} ({k + 1})"
            onclick={() => go(id)}
          >
            <span class="min-w-0 flex-1 truncate">{label}</span>
            {#if id === 'folders' && sources?.sources.length}
              <span class="tabular-nums text-neutral-500">{sources.sources.length}</span>
            {:else if id === 'indexing' && status?.running}
              <span class="h-2 w-2 animate-pulse rounded-full bg-sky-500" title="Indexing"></span>
            {:else if id === 'indexing' && status?.error}
              <span class="h-2 w-2 rounded-full bg-red-500" title="Indexing failed"></span>
            {:else if id === 'locations' && placed}
              <span class="tabular-nums text-neutral-500" title="Photos with a location">{placed.toLocaleString()}</span>
            {:else if id === 'profiles' && profiles.length > 1}
              <span class="tabular-nums text-neutral-500">{profiles.length}</span>
            {:else if id === 'taste' && taste?.calibrated && taste.changed_since >= 25}
              <span class="h-2 w-2 rounded-full bg-amber-400" title="Worth recalibrating"></span>
            {/if}
          </button>
        {/each}
      </nav>

      <div class="min-w-0 flex-1 overflow-y-auto p-5">
        {#if error}
          <p class="mb-4 rounded border border-red-900 bg-red-950/50 px-3 py-2 text-red-300">{error}</p>
        {/if}
        {#if page === 'folders'}
          <Folders {sources} {busy} {act} />
        {:else if page === 'indexing'}
          <Indexing {status} {busy} {act} />
        {:else if page === 'model'}
          <Model {status} {busy} {act} />
        {:else if page === 'locations'}
          <Locations {history} {placed} {busy} {act} reload={loadHistory} />
        {:else if page === 'profiles'}
          <Profiles {profiles} {onprofiles} />
        {:else if page === 'taste'}
          <Taste {taste} {ontaste} {onerror} />
        {:else if page === 'flags'}
          <Flags {tags} {facets} {onchange} {onerror} />
        {:else if page === 'files'}
          <Files {sources} {busy} {act} />
        {/if}
      </div>
    </div>
  </div>
</div>
