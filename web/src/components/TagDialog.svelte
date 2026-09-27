<script>
  // Create or edit a custom tag: a name, example photos, and how strict it is. The
  // counts and the "edge" strip (members just inside the threshold) update as the
  // examples or the strictness change, so you see what the tag will contain.
  import { untrack } from 'svelte';
  import { view } from '../lib/state.svelte.js';
  import { createCustomTag, editCustomTag, deleteCustomTag, previewCustomTag } from '../lib/api.js';

  // dialog: {mode: 'create', photoIds} | {mode: 'edit', tag}; onchange({id, deleted?}) after saving.
  let { dialog, onchange = () => {} } = $props();

  const start = untrack(() => dialog);
  const editing = start.mode === 'edit';
  let name = $state(editing ? start.tag.name : '');
  let strictness = $state(editing ? start.tag.strictness : 'normal');
  let examples = $state(editing ? [...start.tag.examples] : [...start.photoIds]);
  let preview = $state(null);
  let error = $state('');
  let saving = $state(false);
  let confirmDelete = $state(false);

  const LEVELS = [
    ['strict', 'Strict'],
    ['normal', 'Normal'],
    ['loose', 'Loose'],
  ];

  let token = 0;
  $effect(() => {
    const ids = [...examples];
    const level = strictness;
    const mine = ++token;
    if (!ids.length) {
      preview = null;
      return;
    }
    previewCustomTag(ids, level)
      .then((p) => {
        if (mine === token) preview = p;
      })
      .catch((e) => (error = e.message));
  });

  const close = () => (view.tagDialog = null);

  async function save() {
    error = '';
    saving = true;
    try {
      if (editing) {
        const before = new Set(start.tag.examples);
        const now = new Set(examples);
        await editCustomTag(start.tag.id, {
          name,
          strictness,
          add: examples.filter((id) => !before.has(id)),
          remove: start.tag.examples.filter((id) => !now.has(id)),
        });
        onchange({ id: start.tag.id });
      } else {
        const { id } = await createCustomTag(name, examples, strictness);
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
  <form class="relative flex max-h-full w-full max-w-xl flex-col overflow-hidden rounded-lg border border-neutral-800 bg-neutral-900 text-sm" {onsubmit}>
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
          Examples ({examples.length}) <span class="text-neutral-500">· photos like any of these belong to the tag</span>
        </h3>
        <div class="flex flex-wrap gap-1.5">
          {#each examples as id (id)}
            <div class="group relative">
              <img src="/thumbs/{id}.jpg" alt="" class="h-16 w-auto rounded-sm" />
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
        <h3 class="mb-1.5 text-xs text-neutral-400">How close photos must be</h3>
        <div class="grid grid-cols-3 overflow-hidden rounded border border-neutral-700" role="group" aria-label="Strictness">
          {#each LEVELS as [value, label] (value)}
            <button
              type="button"
              class="py-1.5 text-xs {value === 'normal' ? 'border-x border-neutral-700' : ''} {strictness === value
                ? 'bg-sky-700 text-white'
                : 'text-neutral-300 hover:bg-neutral-800'}"
              aria-pressed={strictness === value}
              onclick={() => (strictness = value)}
            >
              {label}
              <span class="block text-[11px] tabular-nums {strictness === value ? 'text-sky-100' : 'text-neutral-500'}">
                {preview ? `${preview.counts[value]} photos` : '…'}
              </span>
            </button>
          {/each}
        </div>
      </section>

      {#if preview?.edge?.length}
        <section>
          <h3 class="mb-1.5 text-xs text-neutral-400">At the edge <span class="text-neutral-500">· the least similar photos still in the tag</span></h3>
          <div class="flex gap-1.5 overflow-x-auto">
            {#each preview.edge as it (it.id)}
              <img src={it.thumb} alt="" class="h-16 w-auto shrink-0 rounded-sm" />
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
