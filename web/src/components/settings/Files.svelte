<script>
  // Settings → Files: where the settings, your own work, and the derived data live; and
  // a clean-up of the indexed data no profile needs any more.
  import { cleanUp } from '../../lib/api.js';

  let { sources, busy = false, act = async () => {} } = $props();
  let note = $state('');

  async function clean() {
    const n = sources.missing;
    if (!confirm(`Delete the indexed data of ${n} photo${n === 1 ? '' : 's'} that no profile shows (their folders were removed, or the files are gone)?\n\nThumbnails, previews and the AI model's data are deleted; the files themselves are not touched, and your flags, tags and captions are kept. Adding such a folder again indexes it anew.`)) return;
    let removed = 0;
    if (await act(async () => ({ removed } = await cleanUp()))) note = `Deleted the data of ${removed} photo${removed === 1 ? '' : 's'}.`;
  }

  const FILES = [
    ['config', 'Settings', 'config.yaml: the photo folders, the AI model, and the other options.'],
    ['selections', 'Your flags, tags and captions', 'Everything you made by hand, in this profile. Back it up.'],
    ['data_dir', 'Derived data', 'Thumbnails, previews, the index, and caches. Safe to delete: indexing rebuilds it.'],
  ];
</script>

<h3 class="text-base font-semibold text-neutral-100">Files</h3>
<p class="mb-3 mt-1 text-xs text-neutral-400">Where Riffle keeps things. Your originals are only read; nothing is written next to them.</p>

{#if !sources}
  <p class="text-neutral-500">Loading…</p>
{:else}
  <dl class="divide-y divide-neutral-800 rounded border border-neutral-800">
    {#each FILES as [key, title, hint] (key)}
      <div class="px-3 py-2">
        <dt class="text-xs font-medium text-neutral-200">{title}</dt>
        <dd class="mt-0.5 select-all break-all font-mono text-xs text-neutral-300">{sources[key] ?? 'none (the built-in defaults)'}</dd>
        <dd class="mt-0.5 text-[11px] text-neutral-500">{hint}</dd>
      </div>
    {/each}
  </dl>
{/if}
<p class="mt-3 text-xs text-neutral-500"><span class="font-mono">riffle paths</span> prints the same in a terminal.</p>

<h4 class="mb-1 mt-6 text-xs font-semibold uppercase tracking-wider text-neutral-500">Clean up</h4>
<p class="text-xs text-neutral-400">
  A folder removed from every profile keeps its indexed data, so adding it back is instant.
  {#if sources?.missing}
    {sources.missing.toLocaleString()} photo{sources.missing === 1 ? ' is' : 's are'} in no profile's folders now, or {sources.missing === 1 ? 'its file is' : 'their files are'} gone.
  {:else}
    Nothing to clean up.
  {/if}
</p>
{#if sources?.missing}
  <button
    class="mt-2 rounded border border-neutral-700 px-3 py-1 text-xs text-neutral-300 hover:border-red-800 hover:bg-red-950 disabled:opacity-40"
    disabled={busy}
    onclick={clean}>Delete their data ({sources.missing.toLocaleString()})…</button
  >
{/if}
{#if note}<p class="mt-2 text-xs text-emerald-400">{note}</p>{/if}
