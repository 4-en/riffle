<script>
  // Settings → Files: where the settings, your own work, and the derived data live.
  let { sources } = $props();

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
