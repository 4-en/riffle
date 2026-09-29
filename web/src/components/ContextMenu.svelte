<script>
  // Right-click menu for a photo in the grid: the photo view's actions without
  // opening it. Flag actions apply to the whole selection when the photo is part
  // of a multi-selection; the rest act on the photo that was right-clicked.
  import { untrack } from 'svelte';
  import { view, findSimilar, toggleTag, toggleExcludeTag, toggleCtag, toggleExcludeCtag, DATE_GROUPS, LOCATION_GROUPS } from '../lib/state.svelte.js';
  import { fetchPhoto, editCustomTag } from '../lib/api.js';
  import { selection } from '../lib/culling.svelte.js';
  import { copyText } from '../lib/clipboard.js';

  // at: {x, y, id}; onflag(flag): flag the selection; ontimeline(photo, kind); onclose()
  // customTags: the user's tags [{id, name}]; ontagchange({id}): after adding photos to one.
  let { at, onflag, ontimeline, onclose, customTags = [], ontagchange = () => {} } = $props();

  // A new menu is created for every right-click, so the position and photo are read once.
  const { x, y, id } = untrack(() => at);

  let photo = $state(null);
  let menu;
  let pos = $state({ left: x, top: y });

  fetchPhoto(id)
    .then((p) => (photo = p))
    .catch(() => {});

  const n = selection.size > 1 && selection.has(id) ? selection.size : 1;
  const targets = n > 1 ? [...selection] : [id];

  async function addToTag(tag) {
    await editCustomTag(tag.id, { add: targets });
    ontagchange({ id: tag.id });
  }
  const dateMode = DATE_GROUPS.includes(view.group) ? view.group : 'day';
  const placeMode = LOCATION_GROUPS.includes(view.group) ? view.group : 'place';

  // Keep the menu inside the window (measure once it is rendered, and when it grows).
  $effect(() => {
    photo;
    if (!menu) return;
    const r = menu.getBoundingClientRect();
    pos = {
      left: Math.max(4, Math.min(x, window.innerWidth - r.width - 4)),
      top: Math.max(4, Math.min(y, window.innerHeight - r.height - 4)),
    };
  });

  function run(action) {
    onclose();
    action();
  }

  // Close on a click outside, scrolling, resizing, or leaving the window.
  $effect(() => {
    const outside = (e) => {
      if (!menu?.contains(e.target)) onclose();
    };
    const close = () => onclose();
    window.addEventListener('pointerdown', outside, true);
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    window.addEventListener('blur', close);
    return () => {
      window.removeEventListener('pointerdown', outside, true);
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
      window.removeEventListener('blur', close);
    };
  });

  const item = 'flex w-full items-center justify-between gap-6 px-3 py-1.5 text-left hover:bg-neutral-700 disabled:opacity-40 disabled:hover:bg-transparent';
  const key = 'text-[11px] text-neutral-500';
</script>

<div
  bind:this={menu}
  class="fixed z-50 min-w-56 overflow-hidden rounded-md border border-neutral-700 bg-neutral-800 py-1 text-sm text-neutral-200 shadow-2xl"
  style="left: {pos.left}px; top: {pos.top}px"
  role="menu"
  tabindex="-1"
  oncontextmenu={(e) => e.preventDefault()}
>
  <button class={item} role="menuitem" onclick={() => run(() => (view.photo = id))}>
    Open <span class={key}>Enter</span>
  </button>
  <div class="my-1 border-t border-neutral-700"></div>
  <button class={item} role="menuitem" onclick={() => run(() => onflag('pick'))}>
    <span><span class="text-emerald-400">✓</span> Pick{n > 1 ? ` ${n} photos` : ''}</span><span class={key}>P</span>
  </button>
  <button class={item} role="menuitem" onclick={() => run(() => onflag('reject'))}>
    <span><span class="text-red-400">✕</span> Reject{n > 1 ? ` ${n} photos` : ''}</span><span class={key}>X</span>
  </button>
  <button class={item} role="menuitem" onclick={() => run(() => onflag(null))}>
    <span><span class="text-neutral-500">○</span> Unflag{n > 1 ? ` ${n} photos` : ''}</span><span class={key}>U</span>
  </button>
  <div class="my-1 border-t border-neutral-700"></div>
  {#if n > 1}
    <button class={item} role="menuitem" disabled={n > 30} onclick={() => run(() => (view.compare = { kind: 'ids', ids: [...selection] }))}>
      Compare {n} photos <span class={key}>C</span>
    </button>
  {/if}
  {#if photo?.stack?.length > 1}
    <button class={item} role="menuitem" onclick={() => run(() => (view.compare = { kind: 'stack', id: photo.stack_id }))}>
      Compare its stack ({photo.stack.length})
    </button>
  {/if}
  <button class={item} role="menuitem" onclick={() => run(() => findSimilar(id))}>Find similar</button>
  <button class={item} role="menuitem" disabled={!photo} onclick={() => run(() => ontimeline(photo, 'date'))}>
    {photo && !photo.taken_at ? 'Show undated' : `Show ${dateMode}`}
  </button>
  {#if !photo || photo.location}
    <button class={item} role="menuitem" disabled={!photo} onclick={() => run(() => ontimeline(photo, 'location'))}>
      Show {placeMode}{photo?.location?.label ? ` · ${photo.location.label.split(',')[0]}` : ''}
    </button>
  {/if}
  <button class={item} role="menuitem" disabled={!photo} onclick={() => run(() => copyText(photo.path))}>Copy path</button>
  <div class="my-1 border-t border-neutral-700"></div>
  <button class={item} role="menuitem" onclick={() => run(() => (view.exporting = { ids: targets, fresh: null, kind: 'selection' }))}>
    Export {n > 1 ? `${n} photos` : 'this photo'}…
  </button>
  <button class={item} role="menuitem" onclick={() => run(() => (view.captioning = { ids: targets }))}>
    Caption {n > 1 ? `${n} photos` : 'this photo'}…
  </button>
  <button class={item} role="menuitem" onclick={() => run(() => (view.editing = { ids: targets }))}>
    Edit {n > 1 ? `${n} photos` : 'this photo'}…
  </button>
  <button class={item} role="menuitem" onclick={() => run(() => (view.tagDialog = { mode: 'create', photoIds: targets }))}>
    Learn a tag from {n > 1 ? `${n} photos` : 'this photo'}…
  </button>
  {#if customTags.length}
    <p class="px-3 pb-1 pt-0.5 text-[11px] text-neutral-500">Add {n > 1 ? `${n} photos` : 'it'} to a tag as an example</p>
    <div class="flex max-w-72 flex-wrap gap-1 px-3 pb-1.5">
      {#each customTags as tag (tag.id)}
        <button class="rounded-full border border-neutral-600 px-2 py-0.5 text-xs text-neutral-300 hover:bg-neutral-700" onclick={() => run(() => addToTag(tag))}>
          + {tag.name}
        </button>
      {/each}
    </div>
  {/if}

  {#if photo?.custom_tags?.length}
    <div class="my-1 border-t border-neutral-700"></div>
    <p class="px-3 pb-1 pt-0.5 text-[11px] text-neutral-500">Your tags · click to filter, Alt+click to hide</p>
    <div class="flex max-w-72 flex-wrap gap-1 px-3 pb-1.5">
      {#each photo.custom_tags as tag (tag.id)}
        {@const on = view.ctags.includes(tag.id)}
        <button
          class="rounded-full border px-2 py-0.5 text-xs {on ? 'border-sky-600 bg-sky-700 text-white' : 'border-neutral-600 text-neutral-300 hover:bg-neutral-700'}"
          onclick={(e) => run(() => (e.altKey ? toggleExcludeCtag(tag.id) : toggleCtag(tag.id)))}
        >
          {tag.name}
        </button>
      {/each}
    </div>
  {/if}

  {#if photo?.tags?.length}
    <div class="my-1 border-t border-neutral-700"></div>
    <p class="px-3 pb-1 pt-0.5 text-[11px] text-neutral-500">Tags · click to filter, Alt+click to hide</p>
    <div class="flex max-w-72 flex-wrap gap-1 px-3 pb-1.5">
      {#each photo.tags as tag (tag.id)}
        {@const on = view.tags.includes(tag.id)}
        {@const off = view.excludeTags.includes(tag.id)}
        <button
          class="rounded-full border px-2 py-0.5 text-xs {on
            ? 'border-sky-600 bg-sky-700 text-white'
            : off
              ? 'border-red-800 bg-red-950 text-red-300 line-through'
              : 'border-neutral-600 text-neutral-300 hover:bg-neutral-700'}"
          title="{tag.family} · {Math.round(tag.prob * 100)}%"
          onclick={(e) => run(() => (e.altKey ? toggleExcludeTag(tag.id) : toggleTag(tag.id)))}
        >
          {tag.name}
        </button>
      {/each}
    </div>
  {/if}
</div>
