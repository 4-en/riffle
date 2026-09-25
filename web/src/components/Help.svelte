<script>
  // In-app guide: the workflow in a few steps, plus the keyboard shortcuts.
  import { view } from '../lib/state.svelte.js';

  const close = () => (view.help = false);

  const shortcuts = [
    ['Anywhere', [
      ['/', 'Search'],
      ['?', 'This guide'],
      ['O', 'Calendar or map overview (with a grouping)'],
      ['Ctrl+Z', 'Undo the last flag change'],
      ['Esc', 'Close the current view / clear the selection'],
    ]],
    ['Grid', [
      ['Click · Ctrl/Shift+click · drag', 'Select one · add / range · box'],
      ['Arrows (Shift extends)', 'Move the selection'],
      ['Ctrl+A', 'Select everything in the view'],
      ['Enter · double-click', 'Open the photo'],
      ['P · X · U', 'Pick · reject · unflag the selection'],
      ['C', 'Compare the selection side by side'],
      ['S · H · R', 'Stacks · hide rejected · review stacks'],
    ]],
    ['Photo view', [
      ['← →', 'Previous / next'],
      ['P · X · U', 'Flag, then go to the next photo'],
    ]],
    ['Compare / review', [
      ['Click · 1–9', 'Mark the keeper(s)'],
      ['A', 'Keep the suggested one (★)'],
      ['Enter', 'Pick the kept, reject the rest (nothing kept: press twice to reject all)'],
      ['Shift+X · Shift+U', 'Reject all · unflag all'],
      ['Z', 'Zoom all photos to the same spot'],
      ['N · B', 'Next / back (review)'],
    ]],
  ];
</script>

<div class="fixed inset-0 z-40 flex items-center justify-center bg-black/70 p-6" role="dialog" aria-modal="true" aria-label="How it works">
  <button class="absolute inset-0 cursor-default" aria-label="Close" onclick={close}></button>
  <div class="relative flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-lg border border-neutral-800 bg-neutral-900 text-sm">
    <div class="flex items-center justify-between border-b border-neutral-800 px-4 py-2">
      <h2 class="font-semibold">How it works</h2>
      <button class="text-neutral-400 hover:text-white" aria-label="Close (Esc)" onclick={close}>✕</button>
    </div>

    <div class="space-y-5 overflow-y-auto p-5 leading-relaxed text-neutral-300">
      <p>
        Find the best photos in your library and copy them out for editing, sharing, or printing. Your originals are only
        ever read; nothing here modifies or moves them.
      </p>

      <ol class="list-decimal space-y-3 pl-5 marker:text-neutral-500">
        <li>
          <strong class="text-neutral-100">Add your photos.</strong> Open <em>Library</em> (top right), browse to a photo folder and
          add it. Indexing runs in the background: thumbnails, search, tags, duplicates, and stacks of similar shots.
          Optionally add your phone's <em>location history</em> there to place photos that have no GPS.
        </li>
        <li>
          <strong class="text-neutral-100">Find what you want.</strong> Search by what is in the picture ("boats at sunset"), click
          tags and filters on the left, or group by day or place (<em>Group</em>) and use the <em>Calendar</em> / <em>Map</em>
          overview to jump around. <em>Find similar</em> in the photo view shows related shots.
        </li>
        <li>
          <strong class="text-neutral-100">Cull.</strong> Mark photos with <kbd>P</kbd> (pick) or <kbd>X</kbd> (reject), one by one in
          the photo view or many at once in the grid. For bursts, turn on <em>Stacks</em> and use <em>Review stacks</em>: the
          similar shots appear side by side, with the sharpest and a ★ suggested keeper marked; choose the keeper(s) and press
          <kbd>Enter</kbd>. <em>Hide rejected</em> keeps the grid tidy, and <kbd>Ctrl+Z</kbd> undoes any flag change.
        </li>
        <li>
          <strong class="text-neutral-100">Export.</strong> <em>Export</em> (top right) copies your picks into a new folder: images,
          images with their RAWs, or only the RAWs. With a location history, the copies can get the position added. Nothing
          already in the destination is overwritten.
        </li>
      </ol>

      <div class="rounded border border-neutral-800 bg-neutral-950/50 p-3 text-xs text-neutral-400">
        Your picks and rejects are saved in <span class="font-mono">selections.sqlite3</span> in your user data folder
        (<span class="font-mono">~/.local/share/photo-archive</span> on Linux): back it up. The derived data (thumbnails,
        embeddings, tags) is in your cache folder (<span class="font-mono">~/.cache/photo-archive</span>) and can be deleted;
        indexing rebuilds it. Settings: <span class="font-mono">~/.config/photo-archive</span>. The Library shows the exact
        places, and so does <span class="font-mono">archive paths</span>.
      </div>

      <section>
        <h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-neutral-500">Keyboard</h3>
        <div class="grid gap-4 sm:grid-cols-2">
          {#each shortcuts as [where, keys] (where)}
            <div>
              <p class="mb-1 text-xs font-medium text-neutral-400">{where}</p>
              <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
                {#each keys as [key, what] (key)}
                  <dt class="whitespace-nowrap font-mono text-neutral-200">{key}</dt>
                  <dd class="text-neutral-400">{what}</dd>
                {/each}
              </dl>
            </div>
          {/each}
        </div>
      </section>
    </div>
  </div>
</div>

<style>
  kbd {
    border: 1px solid rgb(64 64 64);
    border-radius: 3px;
    padding: 0 4px;
    font-family: ui-monospace, monospace;
    font-size: 0.85em;
    color: rgb(229 229 229);
  }
</style>
