<script>
  // Side-by-side comparison for choosing the best of several shots: a stack, a
  // grid selection, or every unreviewed stack in turn ("review").
  import { SvelteSet } from 'svelte/reactivity';
  import { view } from '../lib/state.svelte.js';
  import { fetchStack, fetchStacks, fetchPhoto } from '../lib/api.js';
  import { applyFlags, setFlag, flagOf, undo } from '../lib/culling.svelte.js';

  // context: {kind: 'stack', id} | {kind: 'ids', ids} | {kind: 'review'}
  let { context, itemsById } = $props();

  let members = $state([]);
  let loading = $state(true);
  let error = $state('');
  let hint = $state('');
  const keep = new SvelteSet();
  let focus = $state(0);
  let zoom = $state(false);
  let origin = $state({ x: 50, y: 50 });

  // Review mode walks the stacks that still have unflagged photos (within the filters).
  let queue = $state([]);
  let pos = $state(0);

  const close = () => (view.compare = null);

  async function loadMembers() {
    loading = true;
    error = '';
    hint = '';
    try {
      if (context.kind === 'ids') {
        members = await Promise.all(
          context.ids.map(async (id) => itemsById.get(id) ?? fetchPhoto(id))
        );
      } else {
        const id = context.kind === 'stack' ? context.id : queue[pos]?.id;
        members = id == null ? [] : (await fetchStack(id)).items;
      }
      keep.clear();
      for (const m of members) if (flagOf(m) === 'pick') keep.add(m.id);
      focus = 0;
    } catch (e) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  (async () => {
    if (context.kind === 'review') {
      try {
        queue = (await fetchStacks(view, true)).stacks;
      } catch (e) {
        error = e.message;
      }
    }
    await loadMembers();
  })();

  function next(delta = 1) {
    if (context.kind !== 'review') return close();
    const p = pos + delta;
    if (p < 0) return;
    pos = p;
    loadMembers();
  }

  async function applyKeep() {
    if (!keep.size) {
      hint = 'Choose the photo(s) to keep first (click or 1–9), or Shift+X to reject all.';
      return;
    }
    const rest = members.filter((m) => !keep.has(m.id)).map((m) => m.id);
    await applyFlags([
      { ids: [...keep], flag: 'pick' },
      { ids: rest, flag: 'reject' },
    ]);
    next();
  }

  async function rejectAll() {
    await setFlag(members.map((m) => m.id), 'reject');
    next();
  }

  function toggle(i) {
    const m = members[i];
    if (!m) return;
    keep.has(m.id) ? keep.delete(m.id) : keep.add(m.id);
    focus = i;
    hint = '';
  }

  const maxSharp = $derived(Math.max(0, ...members.map((m) => m.sharpness ?? 0)));
  const cols = $derived(members.length <= 2 ? members.length || 1 : members.length <= 4 ? 2 : members.length <= 9 ? 3 : 4);
  const rows = $derived(Math.ceil(members.length / cols));
  const fit = $derived(rows <= 3); // few photos: fill the screen; many: scroll

  function onkeydown(e) {
    if (e.target instanceof HTMLInputElement) return;
    const k = e.key;
    if (k === 'Escape') close();
    else if ((e.ctrlKey || e.metaKey) && k.toLowerCase() === 'z') {
      e.preventDefault();
      undo().then(() => {
        keep.clear();
        for (const m of members) if (flagOf(m) === 'pick') keep.add(m.id);
      });
    } else if (/^[1-9]$/.test(k)) toggle(Number(k) - 1);
    else if (k === ' ') {
      e.preventDefault();
      toggle(focus);
    } else if (k === 'Enter') {
      e.preventDefault();
      applyKeep();
    } else if (k === 'X' && e.shiftKey) rejectAll();
    else if (k === 'ArrowRight') focus = Math.min(members.length - 1, focus + 1);
    else if (k === 'ArrowLeft') focus = Math.max(0, focus - 1);
    else if (k === 'ArrowDown') focus = Math.min(members.length - 1, focus + cols);
    else if (k === 'ArrowUp') focus = Math.max(0, focus - cols);
    else if (k === 'p' || k === 'x' || k === 'u') {
      const m = members[focus];
      if (m) setFlag([m.id], { p: 'pick', x: 'reject', u: null }[k]);
    } else if (k === 'z') zoom = !zoom;
    else if (k === 'n') next(1);
    else if (k === 'b') next(-1);
    else return;
    e.stopPropagation();
  }

  function track(e) {
    const r = e.currentTarget.getBoundingClientRect();
    origin = { x: ((e.clientX - r.left) / r.width) * 100, y: ((e.clientY - r.top) / r.height) * 100 };
  }

  const title = $derived(
    context.kind === 'review'
      ? queue.length
        ? `Review stacks · ${Math.min(pos + 1, queue.length)} of ${queue.length}`
        : 'Review stacks'
      : context.kind === 'stack'
        ? 'Stack'
        : 'Compare'
  );
</script>

<svelte:window {onkeydown} />

<div class="fixed inset-0 z-30 flex flex-col bg-black" role="dialog" aria-modal="true" aria-label={title}>
  <header class="flex flex-wrap items-center gap-3 border-b border-neutral-800 bg-neutral-900 px-4 py-2 text-sm">
    <h2 class="font-semibold">{title}</h2>
    <span class="text-xs text-neutral-400">{members.length} photos · {keep.size} to keep</span>
    <span class="hidden text-xs text-neutral-500 lg:inline">
      Click or 1–9: keep · Enter: pick kept, reject rest · Shift+X: reject all · Z: zoom · P/X/U: flag focused
      {context.kind === 'review' ? '· N/B: next/back' : ''}
    </span>
    <div class="ml-auto flex items-center gap-2">
      <button
        class="rounded px-2.5 py-1 text-xs {zoom ? 'bg-sky-700 text-white' : 'border border-neutral-700 text-neutral-300 hover:bg-neutral-800'}"
        onclick={() => (zoom = !zoom)}>Zoom (Z)</button
      >
      <button class="rounded border border-neutral-700 px-2.5 py-1 text-xs text-neutral-300 hover:bg-red-900" onclick={rejectAll} disabled={!members.length}>Reject all</button>
      {#if context.kind === 'review'}
        <button class="rounded border border-neutral-700 px-2.5 py-1 text-xs text-neutral-300 hover:bg-neutral-800" onclick={() => next(1)}>Skip (N)</button>
      {/if}
      <button class="rounded bg-emerald-600 px-3 py-1 text-xs font-medium text-black hover:bg-emerald-500 disabled:opacity-40" disabled={!keep.size} onclick={applyKeep}>
        Keep {keep.size || ''} &amp; reject rest (Enter)
      </button>
      <button class="text-neutral-400 hover:text-white" aria-label="Close (Esc)" onclick={close}>✕</button>
    </div>
  </header>

  {#if hint || error}
    <p class="bg-amber-950/60 px-4 py-1.5 text-xs {error ? 'text-red-300' : 'text-amber-200'}">{error || hint}</p>
  {/if}

  {#if loading}
    <p class="m-auto text-neutral-500">Loading…</p>
  {:else if context.kind === 'review' && pos >= queue.length}
    <div class="m-auto text-center text-neutral-300">
      <p>{queue.length ? 'All stacks in this view are reviewed.' : 'No unreviewed stacks in this view.'}</p>
      <button class="mt-3 rounded border border-neutral-700 px-3 py-1 text-sm hover:bg-neutral-800" onclick={close}>Close</button>
    </div>
  {:else}
    <div
      class="grid min-h-0 flex-1 gap-1 p-1 {fit ? '' : 'overflow-y-auto'}"
      style="grid-template-columns: repeat({cols}, minmax(0, 1fr)); grid-auto-rows: {fit ? `minmax(0, calc((100vh - 60px) / ${rows}))` : '42vh'}"
    >
      {#each members as m, i (m.id)}
        {@const flag = flagOf(m)}
        {@const kept = keep.has(m.id)}
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <div
          role="button"
          tabindex="-1"
          class="relative min-h-0 cursor-pointer overflow-hidden bg-neutral-950
            {kept ? 'ring-4 ring-inset ring-emerald-500' : ''}
            {focus === i ? 'outline-2 -outline-offset-4 outline-white/70' : ''}"
          onclick={() => toggle(i)}
          onmousemove={zoom ? track : undefined}
        >
          <img
            src={m.preview ?? `/previews/${m.id}.jpg`}
            alt={m.rel_path}
            draggable="false"
            class="h-full w-full object-contain {flag === 'reject' && !kept ? 'opacity-40' : ''}"
            style={zoom ? `transform: scale(2.5); transform-origin: ${origin.x}% ${origin.y}%` : ''}
          />
          <span class="absolute left-2 top-2 rounded bg-black/70 px-1.5 text-xs font-bold tabular-nums text-white">{i < 9 ? i + 1 : ''}</span>
          {#if kept}
            <span class="absolute right-2 top-2 rounded bg-emerald-500 px-1.5 text-xs font-bold text-black">KEEP</span>
          {:else if flag === 'pick'}
            <span class="absolute right-2 top-2 rounded bg-emerald-900 px-1.5 text-xs text-emerald-200">picked</span>
          {:else if flag === 'reject'}
            <span class="absolute right-2 top-2 rounded bg-red-700 px-1.5 text-xs text-white">rejected</span>
          {/if}
          <div class="absolute inset-x-0 bottom-0 flex items-center gap-2 bg-gradient-to-t from-black/80 to-transparent px-2 pb-1.5 pt-4 text-[11px] text-neutral-300">
            {#if m.sharpness != null && maxSharp > 0}
              <span class="w-16 shrink-0">Sharpness</span>
              <div class="h-1.5 w-24 shrink-0 rounded bg-neutral-700" title="Relative to the sharpest photo here ({Math.round(m.sharpness)})">
                <div class="h-1.5 rounded {m.sharpness === maxSharp ? 'bg-emerald-400' : 'bg-neutral-300'}" style="width: {(100 * m.sharpness) / maxSharp}%"></div>
              </div>
              {#if m.sharpness === maxSharp && members.length > 1}<span class="text-emerald-300">sharpest</span>{/if}
            {/if}
            <span class="ml-auto truncate">{m.rel_path}</span>
          </div>
        </div>
      {/each}
    </div>
  {/if}
</div>
