<script>
  // Create or edit a custom tag: a name, example photos, and how strict it is. The
  // counts and the "edge" strip (members just inside the threshold) update as the
  // examples or the strictness change, so you see what the tag will contain. Two strips
  // show the photos either side of the tag's edge (custom_tags.py): the members it is
  // least sure of (click the ones that don't belong) and the photos just outside (click
  // the ones that do: they become examples). The tag learns from both, and the next
  // photos at the edge show.
  import { untrack } from 'svelte';
  import { view } from '../lib/state.svelte.js';
  import { createCustomTag, editCustomTag, deleteCustomTag, previewCustomTag } from '../lib/api.js';

  // dialog: {mode: 'create', photoIds} | {mode: 'edit', tag}; onchange({id, deleted?}) after saving.
  let { dialog, onchange = () => {} } = $props();

  const start = untrack(() => dialog);
  const editing = start.mode === 'edit';
  let name = $state(editing ? start.tag.name : '');
  // How like its examples a photo must be (custom_tags.py): 1.0 Strict … 0.5 Loose.
  let cut = $state(editing ? (start.tag.cut ?? 0.75) : 0.75);
  let examples = $state(editing ? [...start.tag.examples] : [...start.photoIds]);
  let negatives = $state(editing ? [...(start.tag.negatives ?? [])] : []);
  let preview = $state(null);
  let error = $state('');
  let saving = $state(false);
  let confirmDelete = $state(false);

  const PRESETS = [
    ['loose', 'Loose', 0.5],
    ['normal', 'Normal', 0.75],
    ['strict', 'Strict', 1.0],
  ];
  const CUT_MIN = 0.2, CUT_MAX = 1.3;

  // Ask again when the examples, the marks or the cut change. The slider after a short
  // pause; picking photos from the strips after a longer one (each pick restarts it), so
  // several can be picked in a row without the tag retraining after every click.
  const SLIDER_WAIT = 150, PICK_WAIT = 1500;
  let token = 0;
  let timer;
  let lastCut = untrack(() => cut);
  $effect(() => {
    const ids = [...examples];
    const at = cut;
    const nots = [...negatives];
    const mine = ++token;
    clearTimeout(timer);
    if (!ids.length) {
      preview = null;
      return;
    }
    const wait = !untrack(() => preview) ? 0 : at !== lastCut ? SLIDER_WAIT : PICK_WAIT;
    lastCut = at;
    timer = setTimeout(() => {
      previewCustomTag(ids, at, nots)
        .then((p) => {
          if (mine === token) preview = p;
        })
        .catch((e) => (error = e.message));
    }, wait);
  });
  // The strips without the photos picked since (they leave at once; the rest stay put).
  const picked = $derived(new Set([...examples, ...negatives]));
  const edgeShown = $derived((preview?.edge ?? []).filter((it) => !picked.has(it.id)));
  const outsideShown = $derived((preview?.outside ?? []).filter((it) => !picked.has(it.id)));

  const close = () => (view.tagDialog = null);

  async function save() {
    error = '';
    saving = true;
    try {
      if (editing) {
        const before = new Set(start.tag.examples);
        const now = new Set(examples);
        const negBefore = new Set(start.tag.negatives ?? []);
        const negNow = new Set(negatives);
        await editCustomTag(start.tag.id, {
          name,
          cut,
          add: examples.filter((id) => !before.has(id)),
          remove: start.tag.examples.filter((id) => !now.has(id)),
          add_negatives: negatives.filter((id) => !negBefore.has(id)),
          remove_negatives: [...negBefore].filter((id) => !negNow.has(id)),
        });
        onchange({ id: start.tag.id });
      } else {
        const { id } = await createCustomTag(name, examples, cut, negatives);
        onchange({ id });
      }
      close();
    } catch (e) {
      error = e.message;
    } finally {
      saving = false;
    }
  }

  async function remove() {
    if (!confirmDelete) {
      confirmDelete = true;
      return;
    }
    try {
      await deleteCustomTag(start.tag.id);
      onchange({ id: start.tag.id, deleted: true });
      close();
    } catch (e) {
      error = e.message;
    }
  }

  function onsubmit(e) {
    e.preventDefault();
    if (name.trim() && examples.length && !saving) save();
  }
</script>

<div class="fixed inset-0 z-40 flex items-center justify-center bg-black/70 p-6" role="dialog" aria-modal="true" aria-label={editing ? 'Edit tag' : 'New tag'}>
  <button class="absolute inset-0 cursor-default" aria-label="Close" onclick={close}></button>
  <form class="relative flex max-h-full w-full max-w-4xl flex-col overflow-hidden rounded-lg border border-neutral-800 bg-neutral-900 text-sm" {onsubmit}>
    <div class="flex items-center justify-between border-b border-neutral-800 px-4 py-2">
      <h2 class="font-semibold">{editing ? 'Edit tag' : 'New tag from example photos'}</h2>
      <button type="button" class="text-neutral-400 hover:text-white" aria-label="Close (Esc)" onclick={close}>✕</button>
    </div>

    <div class="space-y-4 overflow-y-auto p-4">
      <label class="block text-xs text-neutral-400">
        Name
        <!-- svelte-ignore a11y_autofocus -->
        <input
          bind:value={name}
          autofocus
          placeholder="e.g. Our dog"
          class="mt-1 w-full rounded border border-neutral-700 bg-neutral-950 px-2 py-1.5 text-sm text-neutral-100 outline-none focus:border-sky-600"
        />
      </label>

      <section>
        <h3 class="mb-1.5 text-xs text-neutral-400">
          Examples ({examples.length}) <span class="text-neutral-500"
            >· {examples.length >= 4 ? 'the tag learns what these share, and what sets them apart from the photos marked below' : 'photos like any of these belong to the tag (from four examples on, it learns what they share)'}</span
          >
        </h3>
        <div class="flex flex-wrap gap-1.5">
          {#each examples as id (id)}
            <div class="group relative">
              <img src="/thumbs/{id}.jpg" alt="" class="h-20 w-auto rounded-sm" />
              {#if examples.length > 1}
                <button
                  type="button"
                  class="absolute right-0.5 top-0.5 hidden rounded bg-black/70 px-1 text-[11px] text-white hover:bg-black group-hover:block"
                  title="Remove this example"
                  onclick={() => (examples = examples.filter((x) => x !== id))}>✕</button
                >
              {/if}
            </div>
          {/each}
        </div>
        {#if editing}
          <p class="mt-1.5 text-[11px] text-neutral-500">To add examples, select photos in the grid and right-click → Add to tag.</p>
        {/if}
      </section>

      <section>
        <h3 class="mb-1 flex items-baseline justify-between text-xs text-neutral-400">
          <span>How like the examples photos must be</span>
          <span class="tabular-nums text-neutral-200">{preview ? `${preview.count.toLocaleString()} photos` : '…'}</span>
        </h3>
        <input
          type="range"
          min={CUT_MIN}
          max={CUT_MAX}
          step="0.05"
          bind:value={cut}
          class="w-full accent-sky-600"
          aria-label="How like the examples photos must be"
          title="Left: more photos, including less certain ones. Right: fewer, surer ones. Watch the photos either side of the edge below."
        />
        <div class="relative h-9 text-[11px]">
          <span class="absolute left-0 top-0 text-neutral-500">More, broader</span>
          <span class="absolute right-0 top-0 text-neutral-500">Fewer, surer</span>
          {#each PRESETS as [key, label, value] (key)}
            <button
              type="button"
              class="absolute top-3.5 -translate-x-1/2 rounded px-1.5 py-0.5 {Math.abs(cut - value) < 0.001 ? 'bg-sky-700 text-white' : 'text-neutral-400 hover:bg-neutral-800'}"
              style="left: {((value - CUT_MIN) / (CUT_MAX - CUT_MIN)) * 100}%"
              onclick={() => (cut = value)}
              >{label}{preview ? ` · ${preview.counts[key]}` : ''}</button
            >
          {/each}
        </div>
      </section>

      {#snippet strip(items, title, hint, verb, mark, symbol, onpick)}
        <section>
          <h3 class="mb-1.5 text-xs text-neutral-400">{title} <span class="text-neutral-500">· {hint}</span></h3>
          <div class="flex flex-wrap gap-2">
            {#each items as it (it.id)}
              <button type="button" class="group relative shrink-0 overflow-hidden rounded" title={verb} onclick={() => onpick(it.id)}>
                <img src="/previews/{it.id}.jpg" alt="" loading="lazy" class="h-40 w-auto max-w-[16rem] object-cover" onerror={(e) => (e.currentTarget.src = it.thumb)} />
                <span class="absolute inset-0 hidden items-center justify-center text-2xl {mark} group-hover:flex">{symbol}</span>
              </button>
            {/each}
          </div>
        </section>
      {/snippet}

      {#if edgeShown.length}
        {@render strip(
          edgeShown,
          'Just inside the tag',
          "the members it is least sure of. Click the ones that don't belong.",
          "Doesn't belong",
          'bg-red-950/60 text-red-200',
          '✕',
          (id) => (negatives = [...negatives, id]),
        )}
      {/if}
      {#if outsideShown.length}
        {@render strip(
          outsideShown,
          'Just outside',
          'the closest photos not in the tag. Click the ones that belong: they become examples.',
          'Belongs: add as an example',
          'bg-emerald-950/60 text-emerald-200',
          '+',
          (id) => (examples = [...examples, id]),
        )}
      {/if}

      {#if negatives.length}
        <section>
          <h3 class="mb-1.5 text-xs text-neutral-400">
            Not in the tag ({negatives.length})
            <span class="text-neutral-500"
              >· {preview?.learned
                ? 'the tag learns what sets these apart from the examples'
                : preview?.left_out
                  ? `leaves out ${preview.left_out} photo${preview.left_out === 1 ? '' : 's'} like these`
                  : 'photos more like these than like the examples are left out'}</span
            >
          </h3>
          <div class="flex flex-wrap gap-1.5">
            {#each negatives as id (id)}
              <div class="group relative">
                <img src="/thumbs/{id}.jpg" alt="" class="h-12 w-auto rounded-sm opacity-60" />
                <button
                  type="button"
                  class="absolute right-0.5 top-0.5 hidden rounded bg-black/70 px-1 text-[11px] text-white hover:bg-black group-hover:block"
                  title="It belongs after all"
                  onclick={() => (negatives = negatives.filter((x) => x !== id))}>↺</button
                >
              </div>
            {/each}
          </div>
        </section>
      {/if}

      {#if error}
        <p class="rounded border border-red-900 bg-red-950/50 px-3 py-2 text-red-300">{error}</p>
      {/if}
    </div>

    <div class="flex items-center gap-2 border-t border-neutral-800 px-4 py-2">
      {#if editing}
        <button type="button" class="rounded px-3 py-1.5 text-xs {confirmDelete ? 'bg-red-700 text-white' : 'text-red-300 hover:bg-red-950'}" onclick={remove}>
          {confirmDelete ? 'Click again to delete' : 'Delete tag'}
        </button>
      {/if}
      <button type="button" class="ml-auto rounded px-3 py-1.5 text-xs text-neutral-300 hover:bg-neutral-800" onclick={close}>Cancel</button>
      <button type="submit" class="rounded bg-sky-700 px-4 py-1.5 text-xs font-medium text-white hover:bg-sky-600 disabled:opacity-40" disabled={!name.trim() || !examples.length || saving}>
        {editing ? 'Save' : 'Create tag'}
      </button>
    </div>
  </form>
</div>
