<script>
  import { untrack } from 'svelte';
  import { view, activeFilterCount, tagFilterActive } from '../lib/state.svelte.js';
  import { startExport, fetchExportStatus, fetchPickExportCounts } from '../lib/api.js';
  import { saveSetting } from '../lib/culling.svelte.js';
  import FolderBrowser from './FolderBrowser.svelte';

  // picksTotal: all picks; picksFiltered: picks within the current filters.
  // hasHistory: a location history is configured (enables adding GPS to the copies).
  // draft: {ids, fresh} exports exactly these photos (a Curate draft; fresh = not exported before).
  // ondone(): an export finished (refresh the grid's "exported" badges).
  let { picksTotal = 0, picksFiltered = 0, hasHistory = false, draft = null, ondone = () => {} } = $props();

  function setting(key, fallback) {
    try {
      const v = localStorage.getItem(`riffle.export.${key}`) ?? localStorage.getItem(`archive.export.${key}`);
      return v === null ? fallback : JSON.parse(v);
    } catch {
      return fallback;
    }
  }

  const today = new Date().toISOString().slice(0, 10);
  let dir = $state(null);
  // The dialog is opened for one purpose; draft does not change while it is open.
  let name = $state(`${untrack(() => draft) ? 'Curated' : 'Selection'} ${today}`);
  let content = $state(setting('content', 'images'));
  let rawFallback = $state(setting('rawFallback', true));
  let structure = $state(setting('structure', 'flat'));
  let addLocation = $state(setting('addLocation', true));
  let onlyNew = $state(setting('onlyNew', false));
  // Captions and fixed tags: off by default; the format is remembered.
  let withCaptions = $state(setting('withCaptions', false));
  let captionFormat = $state(setting('captionFormat', 'embed')); // embed | xmp | txt | jsonl
  let captionText = $state(setting('captionText', 'tags')); // for txt: tags | caption | both
  let underscores = $state(setting('underscores', false));
  // Picks in the chosen scope that were not exported before.
  let newCount = $state(null);
  const filtered = tagFilterActive() || activeFilterCount(view.filters) > 0;
  let scope = $state(filtered ? 'filtered' : 'all');
  let status = $state(null);
  let error = $state('');
  let copied = $state(false);

  $effect(() => {
    const s = scope;
    if (draft) return;
    fetchPickExportCounts(view, s)
      .then((c) => (newCount = c.no))
      .catch(() => (newCount = null));
  });
  const scopeCount = $derived(draft ? draft.ids.length : scope === 'filtered' ? picksFiltered : picksTotal);
  const fresh = $derived(draft ? draft.fresh : newCount);
  const count = $derived(onlyNew && fresh != null ? fresh : scopeCount);
  const running = $derived(status?.running);
  const done = $derived(status && !status.running && status.finished_at && status.result);

  async function start() {
    error = '';
    for (const [k, v] of Object.entries({ content, rawFallback, structure, addLocation, onlyNew, withCaptions, captionFormat, captionText, underscores, folder: dir.path }))
      saveSetting(`export.${k}`, v);
    try {
      status = await startExport(view, {
        folder: dir.path,
        name,
        content,
        raw_fallback: rawFallback,
        structure,
        scope,
        add_location: hasHistory && addLocation,
        only_new: onlyNew,
        captions: withCaptions ? captionFormat : null,
        caption_text: captionText,
        underscores,
        ...(draft ? { photo_ids: draft.ids } : {}),
      });
      while (status.running) {
        await new Promise((r) => setTimeout(r, 500));
        status = await fetchExportStatus();
      }
      if (status.error) error = status.error;
      else ondone();
    } catch (e) {
      error = e.message;
    }
  }

  async function copyPath() {
    await navigator.clipboard?.writeText(status.result.folder);
    copied = true;
    setTimeout(() => (copied = false), 1200);
  }

  const close = () => {
    if (!running) view.exporting = false;
  };
  const pct = $derived(status?.total ? Math.min(100, (100 * status.done) / status.total) : null);
  const radio = 'flex cursor-pointer items-start gap-2 rounded px-2 py-1 hover:bg-neutral-800';
</script>

<div class="fixed inset-0 z-30 flex items-center justify-center bg-black/70 p-6" role="dialog" aria-modal="true" aria-label={draft ? 'Export the draft' : 'Export picks'}>
  <button class="absolute inset-0 cursor-default" aria-label="Close" onclick={close}></button>
  <div class="relative flex max-h-full w-full max-w-2xl flex-col overflow-hidden rounded-lg border border-neutral-800 bg-neutral-900 text-sm">
    <div class="flex items-center justify-between border-b border-neutral-800 px-4 py-2">
      <h2 class="font-semibold">{draft ? 'Export the draft' : 'Export picks'}</h2>
      <button class="text-neutral-400 hover:text-white disabled:opacity-30" aria-label="Close" disabled={running} onclick={close}>✕</button>
    </div>

    <div class="space-y-4 overflow-y-auto p-4">
      <p class="text-xs text-neutral-400">
        Copies files into a new folder. Your originals are only read, and nothing in the destination is overwritten.
      </p>

      {#if draft}
        <p class="text-neutral-300">The {draft.ids.length} photos of the Curate draft, whether they are picked or not.</p>
      {:else}
        <fieldset class="grid gap-1 sm:grid-cols-2" disabled={running}>
          <legend class="mb-1 text-xs font-semibold uppercase tracking-wider text-neutral-500">Which photos</legend>
          <label class={radio}><input type="radio" bind:group={scope} value="all" class="mt-0.5" /> All picks ({picksTotal})</label>
          <label class="{radio} {filtered ? '' : 'opacity-40'}">
            <input type="radio" bind:group={scope} value="filtered" disabled={!filtered} class="mt-0.5" />
            Picks in the current filters ({picksFiltered})
          </label>
        </fieldset>
      {/if}

      <label class="{radio} -mt-2">
        <input type="checkbox" bind:checked={onlyNew} disabled={running} class="mt-0.5" />
        <span>
          Only photos not exported before{fresh != null ? ` (${fresh} of ${scopeCount})` : ''}
          <span class="block text-xs text-neutral-500">Exported photos are marked ↗ in the grid.</span>
        </span>
      </label>

      <fieldset class="grid gap-1 sm:grid-cols-3" disabled={running}>
        <legend class="mb-1 text-xs font-semibold uppercase tracking-wider text-neutral-500">Files</legend>
        <label class={radio}><input type="radio" bind:group={content} value="images" class="mt-0.5" /> Images</label>
        <label class={radio}><input type="radio" bind:group={content} value="images_raws" class="mt-0.5" /> Images + RAWs</label>
        <label class={radio}><input type="radio" bind:group={content} value="raws" class="mt-0.5" /> RAWs only</label>
        {#if content === 'raws'}
          <label class="{radio} sm:col-span-3">
            <input type="checkbox" bind:checked={rawFallback} class="mt-0.5" />
            Copy the image instead when a photo has no RAW
          </label>
        {/if}
      </fieldset>

      <fieldset class="grid gap-1 sm:grid-cols-2" disabled={running}>
        <legend class="mb-1 text-xs font-semibold uppercase tracking-wider text-neutral-500">Layout</legend>
        <label class={radio}><input type="radio" bind:group={structure} value="flat" class="mt-0.5" /> All files in one folder</label>
        <label class={radio}><input type="radio" bind:group={structure} value="folders" class="mt-0.5" /> Keep the source folders</label>
      </fieldset>

      {#if hasHistory}
        <fieldset disabled={running}>
          <legend class="mb-1 text-xs font-semibold uppercase tracking-wider text-neutral-500">Location</legend>
          <label class={radio}>
            <input type="checkbox" bind:checked={addLocation} class="mt-0.5" />
            <span>
              Add the location from your timeline to photos without GPS
              <span class="block text-xs text-neutral-500">
                Written into the exported JPEG/PNG copies (nothing else in them changes); RAWs and other files get an .xmp
                sidecar. Photos that already have GPS are left as they are.
              </span>
            </span>
          </label>
        </fieldset>
      {/if}

      <fieldset disabled={running}>
        <legend class="mb-1 text-xs font-semibold uppercase tracking-wider text-neutral-500">Captions & tags</legend>
        <label class={radio}>
          <input type="checkbox" bind:checked={withCaptions} class="mt-0.5" />
          <span>
            Include each photo's caption and fixed tags
            <span class="block text-xs text-neutral-500">Photos without either get nothing extra.</span>
          </span>
        </label>
        {#if withCaptions}
          <div class="ml-6 grid gap-1 sm:grid-cols-2">
            <label class={radio}>
              <input type="radio" bind:group={captionFormat} value="embed" class="mt-0.5" />
              <span>In the copies<span class="block text-xs text-neutral-500">XMP description and keywords in JPEG/PNG; a sidecar for others</span></span>
            </label>
            <label class={radio}>
              <input type="radio" bind:group={captionFormat} value="xmp" class="mt-0.5" />
              <span>.xmp sidecars<span class="block text-xs text-neutral-500">The copies stay identical to the originals</span></span>
            </label>
            <label class={radio}>
              <input type="radio" bind:group={captionFormat} value="txt" class="mt-0.5" />
              <span>.txt next to each image<span class="block text-xs text-neutral-500">Same name as the image, e.g. for training data</span></span>
            </label>
            <label class={radio}>
              <input type="radio" bind:group={captionFormat} value="jsonl" class="mt-0.5" />
              <span>metadata.jsonl<span class="block text-xs text-neutral-500">One file for the folder (Hugging Face imagefolder)</span></span>
            </label>
          </div>
          {#if captionFormat === 'txt' || captionFormat === 'jsonl'}
            <div class="ml-6 mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 px-2 text-xs text-neutral-300">
              {#if captionFormat === 'txt'}
                <label class="flex items-center gap-1.5">
                  Text
                  <select bind:value={captionText} class="rounded border border-neutral-700 bg-neutral-950 px-1 py-0.5">
                    <option value="tags">tags, comma-separated</option>
                    <option value="caption">the caption</option>
                    <option value="both">caption, then tags</option>
                  </select>
                </label>
              {/if}
              <label class="flex items-center gap-1.5"><input type="checkbox" bind:checked={underscores} /> Spaces instead of _ in tags</label>
            </div>
          {/if}
        {/if}
      </fieldset>

      <section>
        <h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-neutral-500">Destination</h3>
        <FolderBrowser bind:dir start={setting('folder', null)} />
        <label class="mt-2 flex items-center gap-2 text-xs text-neutral-400">
          New folder
          <input
            bind:value={name}
            disabled={running}
            placeholder="(export directly into the folder above)"
            class="min-w-0 flex-1 rounded border border-neutral-700 bg-neutral-950 px-2 py-1 text-neutral-200 outline-none focus:border-sky-600"
          />
        </label>
        {#if dir}
          <p class="mt-1 break-all font-mono text-[11px] text-neutral-500">→ {dir.path}{name.trim() ? `/${name.trim()}` : ''}</p>
        {/if}
      </section>

      {#if error}
        <p class="rounded border border-red-900 bg-red-950/50 px-3 py-2 text-red-300">{error}</p>
      {/if}

      {#if running}
        <div>
          <p class="text-neutral-200">{status.step}</p>
          <div class="mt-1 h-1.5 overflow-hidden rounded bg-neutral-800">
            {#if pct !== null}
              <div class="h-full bg-sky-600 transition-[width]" style="width: {pct}%"></div>
            {:else}
              <div class="h-full w-1/3 animate-pulse bg-sky-700"></div>
            {/if}
          </div>
        </div>
      {:else if done}
        {@const r = status.result}
        <div class="rounded border border-emerald-900 bg-emerald-950/40 px-3 py-2 text-emerald-200">
          <p>
            Exported {r.photos} photos: {r.copied} files copied{r.skipped ? `, ${r.skipped} already there` : ''}{r.without_raw
              ? `, ${r.without_raw} without RAW`
              : ''}{r.geotagged ? `, location added to ${r.geotagged}${r.sidecars ? ` (${r.sidecars} as .xmp sidecars)` : ''}` : ''}{r.captioned
              ? `, captions and tags for ${r.captioned}${r.without_caption ? ` (${r.without_caption} had none)` : ''}`
              : ''}.
          </p>
          <p class="mt-1 flex items-center gap-2 break-all font-mono text-[11px] text-emerald-300/80">
            {r.folder}
            <button class="shrink-0 rounded border border-emerald-800 px-1.5 font-sans hover:bg-emerald-900" onclick={copyPath}>{copied ? 'Copied' : 'Copy path'}</button>
          </p>
        </div>
      {/if}
    </div>

    <div class="flex items-center justify-end gap-2 border-t border-neutral-800 px-4 py-2">
      <button class="rounded px-3 py-1.5 text-xs text-neutral-300 hover:bg-neutral-800 disabled:opacity-40" disabled={running} onclick={close}>
        {done ? 'Close' : 'Cancel'}
      </button>
      <button
        class="rounded bg-sky-700 px-4 py-1.5 text-xs font-medium text-white hover:bg-sky-600 disabled:opacity-40"
        disabled={running || !dir || !count}
        onclick={start}
      >
        {running ? 'Exporting…' : `Export ${count} photo${count === 1 ? '' : 's'}`}
      </button>
    </div>
  </div>
</div>
