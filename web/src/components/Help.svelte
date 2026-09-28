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
      ['Ctrl+,', 'Settings (1–7 switch pages)'],
    ]],
    ['Curate', [
      ['Esc', 'Close the draft'],
      ['← / → (photo view)', 'Step through the draft'],
    ]],
    ['Discover', [
      ['1–9', 'Follow a branch'],
      ['Backspace', 'Back one step'],
      ['R', 'Other photos for the same branches'],
      ['← → · Space (replay)', 'Step · pause the replay'],
      ['Esc', 'Close'],
    ]],
    ['Grid', [
      ['Right-click', 'Menu: flag, compare, similar, show day / place, copy path, its tags'],
      ['Click · Ctrl/Shift+click · drag', 'Select one · add / range · box'],
      ['Arrows (Shift extends)', 'Move the selection'],
      ['Ctrl+A', 'Select everything in the view'],
      ['Enter · double-click', 'Open the photo'],
      ['P · X · U', 'Pick · reject · unflag the selection'],
      ['C', 'Compare the selection side by side'],
      ['S · H · R', 'Stacks · hide rejected (Flag → Picked + Unflagged) · review stacks'],
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
          <strong class="text-neutral-100">Add your photos.</strong> Open <em>Settings</em> (top right) → <em>Photo folders</em>, browse to a
          photo folder and add it. Indexing runs in the background: thumbnails, search, tags, duplicates, and stacks of similar shots.
          Optionally add your phone's location history under <em>Locations</em> to place photos that have no GPS.
        </li>
        <li>
          <strong class="text-neutral-100">Find what you want.</strong> Search by what is in the picture ("boats at sunset"; a minus leaves something out: "street -people"; | finds either: "beach | lake"), click
          tags and filters on the left (click a tag to include it, hover and click <kbd>−</kbd> to exclude it; select photos and right-click → <em>Learn a tag</em> to teach your own), or group by day or place (<em>Group</em>) and use the <em>Calendar</em> / <em>Map</em>
          overview to jump around. <em>Find similar</em> in the photo view shows related shots.
        </li>
        <li>
          <strong class="text-neutral-100">Cull.</strong> Mark photos with <kbd>P</kbd> (pick) or <kbd>X</kbd> (reject), one by one in
          the photo view or many at once in the grid. For bursts, turn on <em>Stacks</em> and use <em>Review stacks</em>: the
          similar shots appear side by side, with the sharpest and a ★ suggested keeper marked; choose the keeper(s) and press
          <kbd>Enter</kbd>. Once you have flagged enough, <em>Sort → Likely keepers first</em> puts the promising photos first,
          learned from your own picks and rejects. <em>Flag → Picked + Unflagged</em> (or <kbd>H</kbd>) hides the rejects, and <kbd>Ctrl+Z</kbd> undoes any flag change.
        </li>
        <li>
          <strong class="text-neutral-100">Curate (optional).</strong> For a photo book or an exhibition, narrow the library to a trip
          and press <em>Curate</em>: a draft of good but varied photos, steered from the left panel (how many, a search to lean towards, best ↔ varied, spread over
          time and places, colours, light and contrast, and styles like moody or colourful). Remove a photo (the next one takes its place; it is not
          rejected), lock the ones to keep, or pick an alternative. <em>Mark as picks</em> or <em>Export</em> when it's right.
        </li>
        <li>
          <strong class="text-neutral-100">Discover (optional).</strong> <em>Discover</em> (top bar, or on a photo) puts a photo in the
          middle, with branches to photos related to it in one way each: the same subject elsewhere, the same light, a colour or
          accent echo, a similar composition, the same place, a place nearby. Click one to walk on; the trail along the bottom leads
          back. Replay the walk, pick or export it, or open it in Curate.
        </li>
        <li>
          <strong class="text-neutral-100">Caption (optional).</strong> Select photos and click <em>Caption…</em>: write a caption and
          tags for each, or generate them (from Riffle's own tags, or with JoyCaption if it is installed). Rename or remove a tag in all
          of them at once. The tags appear under <em>Fixed tags</em> on the left, and search finds words in captions and tags.
        </li>
        <li>
          <strong class="text-neutral-100">Export.</strong> <em>Export</em> (top right) copies your picks into a new folder: images,
          images with their RAWs, or only the RAWs, optionally with their captions and tags (in the copies' XMP, as sidecars, or as
          .txt files). With a location history, the copies can get the position added. Nothing
          already in the destination is overwritten. Exported photos are marked ↗, and <em>Only photos not exported before</em>
          exports just the new picks next time.
        </li>
      </ol>

      <div class="rounded border border-neutral-800 bg-neutral-950/50 p-3 text-xs text-neutral-400">
        Your picks and rejects, tags, and captions are saved in <span class="font-mono">selections.sqlite3</span> in your user data folder
        (<span class="font-mono">~/.local/share/riffle</span> on Linux): back it up. The derived data (thumbnails,
        embeddings, tags) is in your cache folder (<span class="font-mono">~/.cache/riffle</span>) and can be deleted;
        indexing rebuilds it. Settings: <span class="font-mono">~/.config/riffle</span>. <em>Settings → Files</em> shows the exact
        places, and so does <span class="font-mono">riffle paths</span>.
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
