<script>
  // Settings → Photo folders: this profile's folders, the other profiles' folders it can
  // add at once (already indexed), and a browser to add a new one.
  import { addSource, removeSource } from '../../lib/api.js';
  import FolderBrowser from '../FolderBrowser.svelte';

  let { sources, busy, act } = $props();

  let adding = $state(false);
  let dir = $state(null);
  let browser = $state();
  // With no folders yet, adding one is the point of the page.
  const empty = $derived(sources && !sources.sources.length);
  const browsing = $derived(adding || (empty && !sources.others?.length));

  async function add(path) {
    adding = adding || browsing; // stays open after the first folder, for more
    if (await act(() => addSource(path))) await browser?.open(path); // shows it as "In this profile"
  }
  function remove(path) {
    if (confirm(`Remove ${path} from this profile?\n\nThe files are not touched, and other profiles keep showing it. Its indexed data stays, so adding it back is instant.`))
      act(() => removeSource(path));
  }
  // For an offline folder that is not coming back: deleted, or moved (add the new place too).
  function forget(s) {
    if (confirm(`Forget ${s.path} for good?\n\nUse this when the folder was deleted or moved, not when its drive is only unplugged. It leaves every profile, and the ${s.photos} photos catalogued in it are dropped from Riffle (thumbnails and AI data). Your flags, tags and captions are kept by content, so photos you add again from their new place get them back.`))
      act(() => removeSource(s.path, true));
  }
  const btn = 'shrink-0 rounded px-2 py-0.5 text-xs disabled:opacity-40';
</script>

<h3 class="text-base font-semibold text-neutral-100">Photo folders</h3>
<p class="mb-3 mt-1 text-xs text-neutral-400">
  The folders this profile shows, with their subfolders. Other profiles can show other folders; everything is indexed once and
  shared. Originals are only read, never modified.
</p>

{#if !sources}
  <p class="text-neutral-500">Loading…</p>
{:else if empty}
  <p class="text-neutral-400">This profile has no folders yet. Add one of your other profiles' folders, or browse to a new one.</p>
{:else}
  <ul class="divide-y divide-neutral-800 rounded border border-neutral-800">
    {#each sources.sources as s (s.path)}
      <li class="flex items-center gap-3 px-3 py-2">
        <span class="min-w-0 flex-1">
          <span class="block break-all font-mono text-xs {s.offline ? 'text-amber-300' : s.exists ? 'text-neutral-200' : 'text-red-400'}">{s.path}</span>
          {#if s.offline}
            <span class="text-[11px] text-amber-300/80">Offline: not reachable (an unplugged drive?). Its photos stay as they are; connect it and index again.</span>
          {:else if !s.exists}
            <span class="text-[11px] text-red-400">Not found</span>
          {/if}
        </span>
        <span class="shrink-0 text-xs tabular-nums text-neutral-400">{s.photos.toLocaleString()} photos</span>
        {#if s.offline}
          <button
            class="{btn} text-neutral-400 hover:bg-neutral-800 hover:text-red-300"
            disabled={busy || !sources.editable}
            title="The folder was deleted or moved: drop its photos from Riffle"
            onclick={() => forget(s)}>Forget…</button
          >
        {/if}
        <button
          class="{btn} text-neutral-400 hover:bg-neutral-800 hover:text-red-300"
          disabled={busy || !sources.editable}
          title="Hide it in this profile (other profiles keep it)"
          onclick={() => remove(s.path)}>Remove</button
        >
      </li>
    {/each}
  </ul>
{/if}

{#if sources?.others?.length}
  <h4 class="mb-2 mt-5 text-xs font-semibold uppercase tracking-wider text-neutral-500">From your other profiles</h4>
  <ul class="divide-y divide-neutral-800 rounded border border-neutral-800">
    {#each sources.others as s (s.path)}
      <li class="flex items-center gap-3 px-3 py-2">
        <span class="min-w-0 flex-1">
          <span class="block break-all font-mono text-xs text-neutral-300">{s.path}</span>
          <span class="text-[11px] {s.offline ? 'text-amber-300/80' : 'text-neutral-500'}">in {s.profiles.join(', ')} · {s.offline ? 'offline now' : 'already indexed'}</span>
        </span>
        <span class="shrink-0 text-xs tabular-nums text-neutral-400">{s.photos.toLocaleString()} photos</span>
        <button class="{btn} bg-sky-700 font-medium text-white hover:bg-sky-600" disabled={busy || !sources.editable} onclick={() => add(s.path)}>Add</button>
      </li>
    {/each}
  </ul>
{/if}

{#if browsing}
  <div class="mt-5">
    <div class="mb-2 flex items-center justify-between">
      <h4 class="text-xs font-semibold uppercase tracking-wider text-neutral-500">Add a folder</h4>
      {#if !empty || sources?.others?.length}
        <button class="text-xs text-neutral-400 hover:text-white" onclick={() => (adding = false)}>Done</button>
      {/if}
    </div>
    <FolderBrowser bind:this={browser} bind:dir>
      {#snippet footer(dir)}
        <div class="mt-2 flex items-center justify-between gap-3">
          <span class="text-xs text-neutral-400">
            {dir.images} image{dir.images === 1 ? '' : 's'} directly in this folder; subfolders are included too.
          </span>
          {#if dir.source}
            <span class="shrink-0 text-xs text-emerald-400">In this profile{dir.source !== dir.path ? ' (via parent)' : ''}</span>
          {:else}
            <button
              class="shrink-0 rounded bg-sky-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-600 disabled:opacity-50"
              disabled={busy || !sources?.editable}
              title={dir.indexed ? 'Indexed already (another profile has it): added at once' : 'Its photos are indexed now'}
              onclick={() => add(dir.path)}>{dir.indexed ? 'Add this folder' : 'Add this folder and index'}</button
            >
          {/if}
        </div>
      {/snippet}
    </FolderBrowser>
  </div>
{:else}
  <button
    class="mt-4 rounded border border-neutral-700 px-3 py-1.5 text-xs text-neutral-300 hover:bg-neutral-800 disabled:opacity-40"
    disabled={!sources?.editable}
    onclick={() => (adding = true)}>Browse for a folder…</button
  >
{/if}
