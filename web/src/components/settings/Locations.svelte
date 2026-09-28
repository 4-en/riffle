<script>
  // Settings → Locations: phone location histories that place photos without GPS.
  import { addLocationHistory, removeLocationHistory } from '../../lib/api.js';
  import FolderBrowser from '../FolderBrowser.svelte';

  // history: /api/location-history; placed: photos with a location; reload(): fetch it again.
  let { history, placed, busy, act, reload } = $props();

  let picking = $state(false);
  const fromTimeline = $derived(history ? (history.placed.visit ?? 0) + (history.placed.route ?? 0) + (history.placed.nearby ?? 0) : 0);

  async function add(file) {
    if (await act(() => addLocationHistory(file.path))) picking = false;
    await reload();
  }
  async function remove(path) {
    if (!confirm(`Stop using ${path}?\n\nThe file is not touched; photos placed only from it lose their location on the next index.`)) return;
    await act(() => removeLocationHistory(path));
    await reload();
  }
</script>

<h3 class="text-base font-semibold text-neutral-100">Locations</h3>
<p class="mt-1 text-xs text-neutral-400">
  Camera photos rarely have GPS, but your phone usually knows where you were. Add an exported location history and photos are
  placed by their capture time. The file is only read here, never uploaded, and nothing is written into your originals.
</p>

{#if history}
  <p class="mt-3 text-xs text-neutral-300">
    {placed.toLocaleString()} of {history.photos.toLocaleString()} photos have a location{placed
      ? ` (${fromTimeline.toLocaleString()} from a location history${history.placed.exif ? `, ${history.placed.exif.toLocaleString()} from camera GPS` : ''})`
      : ''}.
  </p>
{/if}

{#if history?.files.length}
  <ul class="mt-3 divide-y divide-neutral-800 rounded border border-neutral-800">
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
          onclick={() => remove(file.path)}>Remove</button
        >
      </li>
    {/each}
  </ul>
{/if}

{#if picking}
  <div class="mt-3 rounded border border-neutral-800 p-2">
    <p class="mb-2 text-xs text-neutral-400">Pick the export file (◆): Timeline.json, Records.json, or a .gpx track.</p>
    <FolderBrowser files="history" onpick={add} />
    <button class="mt-2 text-xs text-neutral-400 hover:text-white" onclick={() => (picking = false)}>Cancel</button>
  </div>
{:else}
  <button
    class="mt-3 rounded border border-neutral-700 px-3 py-1 text-xs text-neutral-300 hover:bg-neutral-800 disabled:opacity-40"
    disabled={busy || !history?.editable}
    onclick={() => (picking = true)}>Add location history…</button
  >
{/if}

<details class="mt-3 text-xs text-neutral-500">
  <summary class="cursor-pointer hover:text-neutral-300">How to export it from your phone</summary>
  <p class="mt-1">
    On an Android phone: <em>Settings</em> → <em>Location</em> → <em>Timeline</em> → <em>Export Timeline data</em>, and copy the
    file to this computer. (Depending on the version, Google Maps' Timeline settings may offer the same export.) Older Google
    Takeout exports (Records.json) and GPX tracks from logging apps work too.
  </p>
</details>
