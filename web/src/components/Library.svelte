<script>
  import { untrack } from 'svelte';
  import { view, activeFilterCount, tagFilterActive } from '../lib/state.svelte.js';
  import { resetFlags } from '../lib/culling.svelte.js';
  import { postResetExported, calibrateTaste, createProfile, activateProfile, renameProfile, deleteProfile } from '../lib/api.js';
  import { reloadForProfile } from '../lib/connection.svelte.js';
  import { fetchSources, addSource, removeSource, startIndex, fetchLocationHistory, addLocationHistory, removeLocationHistory } from '../lib/api.js';
  import FolderBrowser from './FolderBrowser.svelte';

  // tags / facets: the app's current counts (global picks and rejects; flags within the filters).
  // ontaste(status): the taste model was (re)calibrated.
  // profiles: /api/profiles list; onprofiles(): refresh it after a change.
  let { status, onchange, tags = null, facets = null, taste = null, ontaste = () => {}, profiles = [], onprofiles = () => {} } = $props();

  // ---- profiles -------------------------------------------------------------------
  let newName = $state('');
  let copyCurrent = $state(false);
  let copyParts = $state({ flags: true, exported: true, tags: true, captions: true });
  let renaming = $state(null); // {slug, name}
  let confirmDelete = $state(null); // slug
  let profileError = $state('');
  const activeProfile = $derived(profiles.find((p) => p.active));
  untrack(() => onprofiles()); // fresh counts whenever the Library opens

  async function profileAction(fn) {
    profileError = '';
    try {
      await fn();
    } catch (e) {
      profileError = e.message;
    }
  }

  const switchTo = (slug) =>
    profileAction(async () => {
      await activateProfile(slug);
      reloadForProfile();
    });

  const createAndSwitch = () =>
    profileAction(async () => {
      const parts = Object.keys(copyParts).filter((k) => copyParts[k]);
      const { slug } = await createProfile(newName, copyCurrent ? activeProfile?.slug : null, copyCurrent ? parts : null);
      newName = '';
      await activateProfile(slug);
      reloadForProfile();
    });

  const saveRename = () =>
    profileAction(async () => {
      await renameProfile(renaming.slug, renaming.name);
      renaming = null;
      onprofiles();
    });

  const removeProfile = (slug) =>
    profileAction(async () => {
      if (confirmDelete !== slug) {
        confirmDelete = slug;
        return;
      }
      await deleteProfile(slug);
      confirmDelete = null;
      onprofiles();
    });
  let calibrating = $state(false);

  async function calibrate() {
    calibrating = true;
    try {
      ontaste(await calibrateTaste());
      error = '';
    } catch (e) {
      error = e.message;
    } finally {
      calibrating = false;
    }
  }

  // Location history files (phone exports) and how many photos they placed.
  let history = $state(null);
  let pickingHistory = $state(false);
  async function loadHistory() {
    try {
      history = await fetchLocationHistory();
    } catch (e) {
      error = e.message;
    }
  }
  $effect(() => {
    status?.finished_at; // placements change after indexing
    loadHistory();
  });
  async function addHistory(file) {
    await act(() => addLocationHistory(file.path));
    if (!error) pickingHistory = false;
    await loadHistory();
  }
  async function removeHistory(path) {
    if (!confirm(`Stop using ${path}?\n\nThe file is not touched; photos placed only from it lose their location on the next index.`)) return;
    await act(() => removeLocationHistory(path));
    await loadHistory();
  }
  const placed = $derived(history ? Object.values(history.placed).reduce((a, b) => a + b, 0) : 0);
  const fromTimeline = $derived(history ? (history.placed.visit ?? 0) + (history.placed.route ?? 0) + (history.placed.nearby ?? 0) : 0);

  const flagged = $derived((tags?.picks ?? 0) + (tags?.rejects ?? 0));
  const filtering = $derived(tagFilterActive() || activeFilterCount(view.filters) > 0);
  const flaggedInFilters = $derived((facets?.flag?.pick ?? 0) + (facets?.flag?.reject ?? 0));
  let resetNote = $state('');

  async function forgetExports() {
    if (!confirm(`Forget that ${tags.exported} photos were exported?\n\nThe exported files are not touched. Riffle stops marking these photos as exported, and "only new" exports include them again. This cannot be undone.`)) return;
    try {
      const { cleared } = await postResetExported(view, 'all');
      resetNote = `Forgot the export history of ${cleared} photo${cleared === 1 ? '' : 's'}.`;
      onchange(); // refresh counts and the grid badges
      error = '';
    } catch (e) {
      error = e.message;
    }
  }

  async function reset(scope) {
    const what = scope === 'all'
      ? `all ${flagged} flagged photos (${tags.picks} picked, ${tags.rejects} rejected)`
      : `the ${flaggedInFilters} flagged photos in the current filters`;
    if (!confirm(`Unflag ${what}?\n\nThey become unflagged again. You can undo this with Ctrl+Z until you reload the page.`)) return;
    try {
      const n = await resetFlags(view, scope);
      resetNote = `Unflagged ${n} photo${n === 1 ? '' : 's'}. Ctrl+Z undoes it.`;
      error = '';
    } catch (e) {
      error = e.message;
    }
  }

  let sources = $state(null);
  let dir = $state(null);
  let browser;
  let error = $state('');
  let busy = $state(false);

  async function loadSources() {
    try {
      sources = await fetchSources();
    } catch (e) {
      error = e.message;
    }
  }

  // Load now, and again as indexing progresses (photo counts change).
  $effect(() => {
    status?.running;
    status?.finished_at;
    loadSources();
  });

  async function act(fn) {
    busy = true;
    error = '';
    try {
      await fn();
      await loadSources();
      if (dir) await browser.open(dir.path);
      onchange();
    } catch (e) {
      error = e.message;
    } finally {
      busy = false;
    }
  }

  const add = () => act(() => addSource(dir.path));
  const remove = (path) => {
    if (confirm(`Remove ${path} from the library?\n\nThe files are not touched; its photos are hidden after re-indexing.`))
      act(() => removeSource(path));
  };
  const reindex = () => act(startIndex);

  const close = () => (view.library = false);
  const pct = $derived(status?.total ? Math.min(100, (100 * status.done) / status.total) : null);
</script>

<div class="fixed inset-0 z-30 flex items-center justify-center bg-black/70 p-6" role="dialog" aria-modal="true" aria-label="Library">
  <button class="absolute inset-0 cursor-default" aria-label="Close" onclick={close}></button>
  <div class="relative flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-lg border border-neutral-800 bg-neutral-900 text-sm">
    <div class="flex items-center justify-between border-b border-neutral-800 px-4 py-2">
      <h2 class="font-semibold">Library</h2>
      <button class="text-neutral-400 hover:text-white" aria-label="Close" onclick={close}>✕</button>
    </div>

    <div class="space-y-5 overflow-y-auto p-4">
      {#if error}
        <p class="rounded border border-red-900 bg-red-950/50 px-3 py-2 text-red-300">{error}</p>
      {/if}

      <!-- Configured folders -->
      <section>
        <h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-neutral-500">Photo folders</h3>
        {#if !sources}
          <p class="text-neutral-500">Loading…</p>
        {:else if !sources.sources.length}
          <p class="text-neutral-400">No folders yet. Pick one below and add it.</p>
        {:else}
          <ul class="divide-y divide-neutral-800 rounded border border-neutral-800">
            {#each sources.sources as s (s.path)}
              <li class="flex items-center gap-3 px-3 py-2">
                <span class="min-w-0 flex-1 break-all font-mono text-xs {s.exists ? 'text-neutral-200' : 'text-red-400'}">
                  {s.path}
                  {#if !s.exists}<span class="ml-1 font-sans">(not found)</span>{/if}
                </span>
                <span class="shrink-0 text-xs tabular-nums text-neutral-400">{s.photos.toLocaleString()} photos</span>
                <button
                  class="shrink-0 rounded px-2 py-0.5 text-xs text-neutral-400 hover:bg-neutral-800 hover:text-red-300 disabled:opacity-40"
                  disabled={busy || !sources.editable}
                  onclick={() => remove(s.path)}>Remove</button
                >
              </li>
            {/each}
          </ul>
        {/if}
        {#if sources?.config}
          <p class="mt-1 text-xs text-neutral-500">Saved in <span class="font-mono">{sources.config}</span>. Originals are only read, never modified.</p>
        {/if}
      </section>

      <!-- Folder browser -->
      <section>
        <h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-neutral-500">Add a folder</h3>
        <FolderBrowser bind:this={browser} bind:dir>
          {#snippet footer(dir)}
          <div class="mt-2 flex items-center justify-between gap-3">
            <span class="text-xs text-neutral-400">
              {dir.images} image{dir.images === 1 ? '' : 's'} directly in this folder; subfolders are included too.
            </span>
            {#if dir.source}
              <span class="shrink-0 text-xs text-emerald-400">In library{dir.source !== dir.path ? ' (via parent)' : ''}</span>
            {:else}
              <button
                class="shrink-0 rounded bg-sky-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-600 disabled:opacity-50"
                disabled={busy || !sources?.editable}
                onclick={add}>Add this folder and index</button
              >
            {/if}
          </div>
          {/snippet}
        </FolderBrowser>
      </section>

      <!-- Location history -->
      <section>
        <h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-neutral-500">Location history</h3>
        <p class="text-xs text-neutral-400">
          Camera photos rarely have GPS, but your phone usually knows where you were. Add an exported location history and
          photos are placed by their capture time. The file is only read here, never uploaded, and nothing is written into your originals.
        </p>
        {#if history?.files.length}
          <ul class="mt-2 divide-y divide-neutral-800 rounded border border-neutral-800">
            {#each history.files as file (file.path)}
              <li class="flex items-center gap-3 px-3 py-2">
                <div class="min-w-0 flex-1">
                  <p class="break-all font-mono text-xs {file.exists && !file.error ? 'text-neutral-200' : 'text-red-400'}">{file.path}</p>
                  <p class="text-[11px] text-neutral-500">
                    {#if !file.exists}not found{:else if file.error}{file.error}{:else}
                      {file.format} · {file.from} – {file.to} · {file.visits.toLocaleString()} visits, {file.points.toLocaleString()} points
                    {/if}
                  </p>
                </div>
                <button
                  class="shrink-0 rounded px-2 py-0.5 text-xs text-neutral-400 hover:bg-neutral-800 hover:text-red-300 disabled:opacity-40"
                  disabled={busy || !history.editable}
                  onclick={() => removeHistory(file.path)}>Remove</button
                >
              </li>
            {/each}
          </ul>
          <p class="mt-1 text-xs text-neutral-400">
            {placed} of {history.photos} photos have a location ({fromTimeline} from the timeline{history.placed.exif ? `, ${history.placed.exif} from camera GPS` : ''}).
          </p>
        {/if}
        {#if pickingHistory}
          <div class="mt-2 rounded border border-neutral-800 p-2">
            <p class="mb-2 text-xs text-neutral-400">Pick the export file (◆): Timeline.json, Records.json, or a .gpx track.</p>
            <FolderBrowser files="history" onpick={addHistory} />
            <button class="mt-2 text-xs text-neutral-400 hover:text-white" onclick={() => (pickingHistory = false)}>Cancel</button>
          </div>
        {:else}
          <button
            class="mt-2 rounded border border-neutral-700 px-3 py-1 text-xs text-neutral-300 hover:bg-neutral-800 disabled:opacity-40"
            disabled={busy || !history?.editable}
            onclick={() => (pickingHistory = true)}>Add location history…</button
          >
        {/if}
        <details class="mt-2 text-xs text-neutral-500">
          <summary class="cursor-pointer hover:text-neutral-300">How to export it from your phone</summary>
          <p class="mt-1">
            On an Android phone: <em>Settings</em> → <em>Location</em> → <em>Timeline</em> → <em>Export Timeline data</em>, and copy
            the file to this computer. (Depending on the version, Google Maps' Timeline settings may offer the same export.) Older
            Google Takeout exports (Records.json) and GPX
            tracks from logging apps work too.
          </p>
        </details>
      </section>

      <!-- Profiles: separate sets of flags, export history, custom tags, captions and fixed tags -->
      <section>
        <h3 class="mb-1 text-xs font-semibold uppercase tracking-wider text-neutral-500">Profiles</h3>
        <p class="mb-2 text-xs text-neutral-400">
          Each profile has its own picks and rejects, export history, custom tags, and taste model: one per project, for example.
          The photos, their index, and the settings are shared.
        </p>
        <ul class="divide-y divide-neutral-800 rounded border border-neutral-800">
          {#each profiles as p (p.slug)}
            <li class="flex flex-wrap items-center gap-2 px-3 py-1.5 text-xs">
              {#if renaming?.slug === p.slug}
                <input
                  bind:value={renaming.name}
                  onkeydown={(e) => e.key === 'Enter' && saveRename()}
                  class="min-w-0 flex-1 rounded border border-neutral-700 bg-neutral-950 px-2 py-0.5 text-neutral-100 outline-none focus:border-sky-600"
                />
                <button class="text-sky-400 hover:underline" onclick={saveRename}>Save</button>
                <button class="text-neutral-400 hover:underline" onclick={() => (renaming = null)}>Cancel</button>
              {:else}
                <span class="min-w-0 flex-1 truncate {p.active ? 'font-medium text-sky-300' : 'text-neutral-200'}">{p.name}{p.active ? ' · active' : ''}</span>
                <span class="tabular-nums text-neutral-500">{p.picks} picks · {p.rejects} rejects · {p.tags} tags</span>
                {#if !p.active}
                  <button class="text-sky-400 hover:underline" onclick={() => switchTo(p.slug)}>Switch</button>
                {/if}
                <button class="text-neutral-400 hover:underline" onclick={() => (renaming = { slug: p.slug, name: p.name })}>Rename</button>
                {#if p.slug !== 'default' && !p.active}
                  <button class="{confirmDelete === p.slug ? 'rounded bg-red-800 px-1.5 text-white' : 'text-red-400 hover:underline'}" onclick={() => removeProfile(p.slug)}>
                    {confirmDelete === p.slug ? 'Click again to delete' : 'Delete'}
                  </button>
                {/if}
              {/if}
            </li>
          {/each}
        </ul>
        <div class="mt-2 space-y-1.5 rounded border border-neutral-800 p-2 text-xs">
          <div class="flex items-center gap-2">
            <input
              bind:value={newName}
              placeholder="New profile, e.g. Photo book 2026"
              class="min-w-0 flex-1 rounded border border-neutral-700 bg-neutral-950 px-2 py-1 text-neutral-100 outline-none focus:border-sky-600"
            />
            <button
              class="rounded bg-sky-700 px-3 py-1 font-medium text-white hover:bg-sky-600 disabled:opacity-40"
              disabled={!newName.trim()}
              onclick={createAndSwitch}>Create and switch</button
            >
          </div>
          <div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-neutral-400">
            <label class="flex items-center gap-1"><input type="radio" bind:group={copyCurrent} value={false} /> Start empty</label>
            <label class="flex items-center gap-1"><input type="radio" bind:group={copyCurrent} value={true} /> Copy from {activeProfile?.name ?? 'the current profile'}:</label>
            {#if copyCurrent}
              <label class="flex items-center gap-1"><input type="checkbox" bind:checked={copyParts.flags} /> flags</label>
              <label class="flex items-center gap-1"><input type="checkbox" bind:checked={copyParts.exported} /> export history</label>
              <label class="flex items-center gap-1"><input type="checkbox" bind:checked={copyParts.tags} /> your tags</label>
              <label class="flex items-center gap-1"><input type="checkbox" bind:checked={copyParts.captions} /> captions and fixed tags</label>
            {/if}
          </div>
        </div>
        {#if profileError}<p class="mt-1 text-xs text-red-300">{profileError}</p>{/if}
      </section>

      <!-- Flags (deliberately low-key: a reset is rarely wanted) -->
      <section>
        <h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-neutral-500">Flags</h3>
        <p class="text-xs text-neutral-400">
          {tags?.picks ?? 0} picked, {tags?.rejects ?? 0} rejected. Flags are saved in <span class="break-all font-mono">{sources?.selections ?? 'selections.sqlite3'}</span>; back it up to keep your selection.
        </p>
        <div class="mt-2 flex flex-wrap gap-2">
          {#if filtering}
            <button
              class="rounded border border-neutral-700 px-3 py-1 text-xs text-neutral-300 hover:border-red-800 hover:bg-red-950 disabled:opacity-40"
              disabled={!flaggedInFilters}
              onclick={() => reset('filtered')}>Unflag photos in the current filters ({flaggedInFilters})…</button
            >
          {/if}
          <button
            class="rounded border border-neutral-700 px-3 py-1 text-xs text-neutral-300 hover:border-red-800 hover:bg-red-950 disabled:opacity-40"
            disabled={!flagged}
            onclick={() => reset('all')}>Unflag all photos ({flagged})…</button
          >
          <button
            class="rounded border border-neutral-700 px-3 py-1 text-xs text-neutral-300 hover:border-red-800 hover:bg-red-950 disabled:opacity-40"
            disabled={!tags?.exported}
            title="Photos stay exported on disk; Riffle just stops marking them (↗) and 'only new' exports include them again"
            onclick={forgetExports}>Forget export history ({tags?.exported ?? 0})…</button
          >
        </div>
        {#if resetNote}<p class="mt-1 text-xs text-emerald-400">{resetNote}</p>{/if}
      </section>

      <!-- Taste model -->
      <section>
        <div class="mb-2 flex items-center justify-between">
          <h3 class="text-xs font-semibold uppercase tracking-wider text-neutral-500">Your taste</h3>
          <button
            class="rounded border border-neutral-700 px-3 py-1 text-xs hover:bg-neutral-800 disabled:opacity-40"
            disabled={calibrating}
            title="Learn from your current picks, rejects and exports (a second or two)"
            onclick={calibrate}>{calibrating ? 'Calibrating…' : taste?.calibrated ? 'Recalibrate' : 'Calibrate'}</button
          >
        </div>
        {#if !taste}
          <p class="text-xs text-neutral-500">Checking…</p>
        {:else if !taste.calibrated}
          <p class="text-xs text-neutral-400">
            Riffle can learn what you tend to keep from your picks and rejects, and sort by it. Flag some photos, then press
            <em>Calibrate</em>.
          </p>
        {:else if taste.enabled}
          <p class="text-xs text-neutral-300">
            Learned from {taste.keeper_scenes} keeper scenes and {taste.reject_scenes} rejected ones. Checked on photos it did
            not learn from, <strong>{Math.round(taste.top20_recall * 100)}%</strong> of your keepers are in its top fifth
            (quality {taste.auc.toFixed(2)}, where 0.5 is chance).
          </p>
          <p class="mt-1 text-xs text-neutral-500">
            Use <em>Sort → Likely keepers first</em> to review the promising photos first, or <em>Likely rejects first</em> to clear
            out misses quickly. It only orders photos; it never flags anything.
          </p>
        {:else}
          <p class="text-xs text-neutral-400">Not active. {taste.reason}</p>
        {/if}
        {#if taste?.calibrated}
          <p class="mt-1 text-xs {taste.changed_since >= 25 ? 'text-amber-300' : 'text-neutral-500'}">
            Calibrated {new Date(taste.calibrated_at * 1000).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}{taste.changed_since
              ? ` · ${taste.changed_since} flag${taste.changed_since === 1 ? '' : 's'} changed since${taste.changed_since >= 25 ? ': worth recalibrating' : ''}`
              : ' · up to date'}
          </p>
        {/if}
      </section>

      <!-- Indexing -->
      <section>
        <div class="mb-2 flex items-center justify-between">
          <h3 class="text-xs font-semibold uppercase tracking-wider text-neutral-500">Indexing</h3>
          <button
            class="rounded border border-neutral-700 px-3 py-1 text-xs hover:bg-neutral-800 disabled:opacity-40"
            disabled={busy || status?.running}
            onclick={reindex}>Index now</button
          >
        </div>
        {#if status?.running}
          <p class="text-neutral-200">{status.step}{status.pending ? ' (another run queued)' : ''}</p>
          <div class="mt-1 h-1.5 overflow-hidden rounded bg-neutral-800">
            {#if pct !== null}
              <div class="h-full bg-sky-600 transition-[width]" style="width: {pct}%"></div>
            {:else}
              <div class="h-full w-1/3 animate-pulse bg-sky-700"></div>
            {/if}
          </div>
          {#if status.total}
            <p class="mt-1 text-xs tabular-nums text-neutral-500">{status.done} / {status.total}</p>
          {/if}
        {:else if status?.error}
          <p class="text-red-400">Indexing failed: {status.error}</p>
        {:else if status?.finished_at}
          <p class="text-neutral-400">Last run finished {new Date(status.finished_at * 1000).toLocaleTimeString()}.</p>
        {:else}
          <p class="text-neutral-500">Not run since the server started. Use <span class="font-mono">riffle index</span> or the button above.</p>
        {/if}
        {#if status?.lines?.length}
          <pre class="mt-2 max-h-40 overflow-y-auto rounded bg-neutral-950 p-2 text-[11px] leading-relaxed text-neutral-400">{status.lines.join('\n')}</pre>
        {/if}
      </section>
    </div>
  </div>
</div>
