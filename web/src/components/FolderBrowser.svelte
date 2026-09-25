<script>
  // Server-side folder browser: the browser cannot reveal real paths, so the
  // server lists folders. `dir` (bindable) is the folder currently shown.
  import { untrack } from 'svelte';
  import { browse } from '../lib/api.js';

  let { dir = $bindable(null), start = null, footer } = $props();

  let pathInput = $state('');
  let error = $state('');

  export async function open(path) {
    try {
      dir = await browse(path);
      pathInput = dir.path;
      error = '';
    } catch (e) {
      error = e.message;
    }
  }

  open(untrack(() => start)); // only the initial folder; later navigation is the user's
</script>

<form
  class="flex gap-2"
  onsubmit={(e) => {
    e.preventDefault();
    open(pathInput);
  }}
>
  <button
    type="button"
    class="rounded border border-neutral-700 px-2 text-neutral-300 hover:bg-neutral-800 disabled:opacity-40"
    disabled={!dir?.parent}
    title="Up one level"
    onclick={() => open(dir.parent)}>↑</button
  >
  <input
    bind:value={pathInput}
    class="min-w-0 flex-1 rounded border border-neutral-700 bg-neutral-950 px-2 py-1 font-mono text-xs outline-none focus:border-sky-600"
    placeholder="/path/to/folder"
    spellcheck="false"
  />
  <button type="submit" class="rounded border border-neutral-700 px-3 text-xs hover:bg-neutral-800">Go</button>
</form>

{#if error}
  <p class="mt-2 text-xs text-red-400">{error}</p>
{/if}

{#if dir}
  <div class="mt-2 max-h-56 overflow-y-auto rounded border border-neutral-800">
    {#each dir.dirs as d (d.path)}
      <button class="flex w-full items-center gap-2 px-3 py-1 text-left hover:bg-neutral-800" onclick={() => open(d.path)}>
        <span class="text-neutral-500">▸</span><span class="truncate">{d.name}</span>
      </button>
    {:else}
      <p class="px-3 py-2 text-xs text-neutral-500">No subfolders</p>
    {/each}
  </div>
  {@render footer?.(dir)}
{/if}
