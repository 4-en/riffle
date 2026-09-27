<script>
  // Captions & tags for a set of photos (opened from a selection): each photo's
  // caption and fixed tags, editable; bulk tag edits; and generating them with
  // Riffle's own tags, scene phrases, or JoyCaption (photos first). Booru tags (the
  // WD tagger, JoyCaption's Danbooru preset, a master tag list) are tucked away
  // under "Illustrations / booru tags". Ctrl+Z undoes edits made here.
  //
  // Photos checked here (checkboxes, Shift for a range) are the working set: every
  // operation (generating, the tag panel, clearing) applies to them, or to all when
  // none are checked. Each photo is a card with one field per kind of text (caption,
  // tags); text read from the photo (OCR) is meant to become one more field.
  import { untrack } from 'svelte';
  import { SvelteSet } from 'svelte/reactivity';
  import { view } from '../lib/state.svelte.js';
  import { fetchCaptions, saveCaptions, bulkTags, fetchCaptioning, startCaptioning, cancelCaptioning, fetchTaglists, downloadDanbooru } from '../lib/api.js';
  import { loadSetting, saveSetting } from '../lib/culling.svelte.js';

  // onchange(): captions or tags changed (App refreshes the sidebar and the grid).
  let { onchange = () => {} } = $props();

  const ids = untrack(() => view.captioning.ids);
  const PER_PAGE = 50;

  let data = $state({}); // id -> {caption, method, edited, tags, rel_path}
  let loading = $state(true);
  let error = $state('');
  let notice = $state('');
  let page = $state(0);
  let only = $state(null); // show only photos with this tag
  const undoStack = [];
  let undoCount = $state(0); // undoStack.length, for the button

  async function load() {
    try {
      data = await fetchCaptions(ids);
      error = '';
    } catch (e) {
      error = e.message;
    } finally {
      loading = false;
    }
  }
  load();

  const shown = $derived(only ? ids.filter((id) => data[id]?.tags.some((t) => t.toLowerCase() === only.toLowerCase())) : ids);
  const pages = $derived(Math.max(1, Math.ceil(shown.length / PER_PAGE)));
  const pageIds = $derived(shown.slice(page * PER_PAGE, (page + 1) * PER_PAGE));
  $effect(() => {
    if (page >= pages) page = pages - 1;
  });
  const isEmpty = (id) => data[id] && !data[id].caption && !data[id].tags.length;
  const empty = $derived(ids.filter(isEmpty).length);

  // ---- the working set ------------------------------------------------------------

  const checked = new SvelteSet();
  let anchor = null; // last checkbox clicked, for Shift ranges
  // The checked photos in their order, or all of them.
  const work = $derived(checked.size ? ids.filter((id) => checked.has(id)) : ids);
  const workLabel = $derived(checked.size ? `the ${checked.size} checked` : `all ${ids.length}`);

  function toggleCheck(e, id) {
    const on = !checked.has(id);
    if (e.shiftKey && anchor != null) {
      const a = shown.indexOf(anchor), b = shown.indexOf(id);
      if (a >= 0 && b >= 0) {
        for (const x of shown.slice(Math.min(a, b), Math.max(a, b) + 1)) on ? checked.add(x) : checked.delete(x);
        anchor = id;
        return;
      }
    }
    on ? checked.add(id) : checked.delete(id);
    anchor = id;
  }
  function checkAll(list) {
    for (const id of list) checked.add(id);
  }

  // Every tag of the working set with how many have it, most common first.
  const tagCounts = $derived.by(() => {
    const counts = new Map();
    for (const id of work) {
      for (const t of data[id]?.tags ?? []) {
        const k = t.toLowerCase();
        const c = counts.get(k) ?? { tag: t, n: 0 };
        c.n++;
        counts.set(k, c);
      }
    }
    return [...counts.values()].sort((a, b) => b.n - a.n || a.tag.localeCompare(b.tag));
  });

  // ---- editing ------------------------------------------------------------------

  async function change(run) {
    try {
      const { previous } = await run();
      if (previous?.length) undoCount = undoStack.push(previous);
      error = '';
      await load();
      onchange();
    } catch (e) {
      error = e.message;
    }
  }

  function saveCaption(id, text) {
    if ((data[id]?.caption ?? '') === text.trim()) return;
    change(() => saveCaptions([{ id, caption: text }]));
  }
  const setTags = (id, tags) => change(() => saveCaptions([{ id, tags }]));

  function addTags(id, input) {
    const add = input.value.split(',').map((t) => t.trim()).filter(Boolean);
    input.value = '';
    const have = new Set(data[id].tags.map((t) => t.toLowerCase()));
    const fresh = add.filter((t) => !have.has(t.toLowerCase()));
    if (fresh.length) setTags(id, [...data[id].tags, ...fresh]);
  }

  // Drag a tag onto another to move it there (the order is kept in training files).
  let dragging = null; // {id, index}
  function drop(id, index) {
    if (!dragging || dragging.id !== id || dragging.index === index) return;
    const tags = [...data[id].tags];
    const [t] = tags.splice(dragging.index, 1);
    tags.splice(index, 0, t);
    dragging = null;
    setTags(id, tags);
  }

  let renaming = $state(null); // tag being renamed
  let addAll = $state('');
  function renameTag(tag, to) {
    renaming = null;
    to = to.trim();
    if (to && to !== tag) change(() => bulkTags(work, 'rename', tag, to));
  }

  // Replace X with Y (rename, for a tag typed rather than picked from the list).
  let replaceFrom = $state('');
  let replaceTo = $state('');
  function replaceTag() {
    const [from, to] = [replaceFrom.trim(), replaceTo.trim()];
    if (!from || !to) return;
    replaceFrom = replaceTo = '';
    change(() => bulkTags(work, 'rename', from, to));
  }

  // Clear the tags or the captions of the working set (undo with Ctrl+Z).
  function clearAll(what) {
    const items = work.filter((id) => (what === 'tags' ? data[id]?.tags.length : data[id]?.caption)).map((id) => ({ id, [what === 'tags' ? 'tags' : 'caption']: what === 'tags' ? [] : '' }));
    if (items.length) change(() => saveCaptions(items));
  }

  async function undo() {
    const previous = undoStack.pop();
    undoCount = undoStack.length;
    if (!previous) return;
    try {
      await saveCaptions(previous);
      await load();
      onchange();
      notice = 'Undone.';
    } catch (e) {
      error = e.message;
    }
  }

  function onkeydown(e) {
    const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z' && !typing && view.photo == null) {
      e.preventDefault();
      undo();
    }
  }

  // ---- generating ---------------------------------------------------------------

  let methods = $state([]);
  let job = $state(null);
  let lists = $state([]);
  const setting = (k, fallback) => loadSetting(`caption.${k}`, fallback);
  let choice = $state(setting('choice', 'riffle')); // riffle | phrases | joy | wd | joy-booru
  let mode = $state(setting('mode', 'add'));
  let scope = $state(setting('scope', 'all')); // all | empty (only when adding)
  let joyCaption = $state(setting('joyCaption', 'medium')); // '' | short | medium | detailed
  let joyKeywords = $state(setting('joyKeywords', false));
  let joyPrompt = $state('');
  let wdThreshold = $state(setting('wdThreshold', 0.35));
  let wdCharacters = $state(setting('wdCharacters', true));
  let wdRating = $state(setting('wdRating', false));
  let keepPrefixed = $state(false);
  let taglist = $state(setting('taglist', ''));
  let booruOpen = $state(untrack(() => choice === 'wd' || choice === 'joy-booru')); // open if last used
  let downloading = $state(false);

  const method = (key) => methods.find((m) => m.key === key);
  const booru = $derived(choice === 'wd' || choice === 'joy-booru');
  const chosen = $derived(method({ riffle: 'riffle', phrases: 'phrases', joy: 'joycaption', wd: 'wd', 'joy-booru': 'joycaption' }[choice]));
  const makesCaption = $derived(choice === 'joy' && !!joyCaption);
  const makesTags = $derived(choice !== 'joy' || joyKeywords);
  // Photos still missing what this method makes (a choice only when adding: replacing
  // is for photos that have something).
  const missing = $derived(work.filter((id) => data[id] && ((makesCaption && !data[id].caption) || (makesTags && !data[id].tags.length))));
  const targets = $derived(mode === 'add' && scope === 'empty' ? missing : work);

  async function refreshStatus() {
    try {
      const s = await fetchCaptioning();
      methods = s.methods;
      const wasRunning = job?.running;
      job = s.job;
      if (job.running || wasRunning) load();
      if (wasRunning && !job.running) {
        onchange();
        notice = job.error ? '' : job.result ? `Done: ${job.result.changed} of ${job.result.photos} photos changed.` : '';
        if (job.error) error = job.error;
      }
    } catch (e) {
      error = e.message;
    }
  }
  refreshStatus();
  fetchTaglists()
    .then((l) => (lists = l.lists))
    .catch(() => {});
  $effect(() => {
    if (!job?.running) return;
    const timer = setInterval(refreshStatus, 1000);
    return () => clearInterval(timer);
  });

  async function generate() {
    const body = { ids: targets, mode, options: {} };
    if (choice === 'riffle' || choice === 'phrases') body.method = choice;
    else if (choice === 'joy') {
      body.method = 'joycaption';
      body.options = { caption: joyCaption || null, tags: joyKeywords ? 'keywords' : null, prompt: joyPrompt };
    } else if (choice === 'joy-booru') {
      body.method = 'joycaption';
      body.options = { caption: null, tags: 'booru', keep_prefixed: keepPrefixed };
    } else {
      body.method = 'wd';
      body.options = { threshold: Number(wdThreshold), characters: wdCharacters, rating: wdRating };
    }
    if (booru && taglist) body.taglist = taglist;
    for (const [k, v] of Object.entries({ choice, mode, scope, joyCaption, joyKeywords, wdThreshold, wdCharacters, wdRating, taglist })) saveSetting(`caption.${k}`, v);
    error = notice = '';
    try {
      const res = await startCaptioning(body);
      if (res.done) {
        notice = `Done: ${res.changed} of ${res.photos} photos changed.`;
        await load();
        onchange();
      } else {
        job = res.job;
      }
    } catch (e) {
      error = e.message;
    }
  }

  async function getDanbooru() {
    downloading = true;
    try {
      const l = await downloadDanbooru();
      lists = (await fetchTaglists()).lists;
      taglist = l.name;
    } catch (e) {
      error = e.message;
    } finally {
      downloading = false;
    }
  }

  const close = () => (view.captioning = null);
  const btn = 'rounded border border-neutral-700 px-2.5 py-1 text-xs text-neutral-300 hover:bg-neutral-800 disabled:opacity-40';
  const card = (on, ok) =>
    `block rounded border px-2.5 py-1.5 text-xs ${on ? 'border-sky-600 bg-sky-950/50' : 'border-neutral-800 hover:border-neutral-600'} ${ok ? '' : 'opacity-50'}`;
</script>

<svelte:window {onkeydown} />

{#snippet methodCard(key, value, label, hint)}
  {@const m = method(key)}
  <label class={card(choice === value, m?.available ?? true)}>
    <span class="flex items-center gap-2">
      <input type="radio" bind:group={choice} {value} disabled={m && !m.available} />
      <span class="font-medium text-neutral-200">{label}</span>
    </span>
    <span class="mt-0.5 block text-neutral-500">{hint}</span>
    {#if m && !m.available}
      <span class="mt-0.5 block text-amber-400/80">{m.reason}</span>
    {:else if m?.download}
      <span class="mt-0.5 block text-neutral-500">Downloads {m.download} on first use.</span>
    {/if}
  </label>
{/snippet}

<div class="fixed inset-0 z-20 flex flex-col bg-neutral-950" role="dialog" aria-modal="true" aria-label="Captions and tags">
  <header class="flex flex-wrap items-center gap-3 border-b border-neutral-800 bg-neutral-900 px-4 py-2 text-sm">
    <h2 class="font-semibold">Captions & tags</h2>
    <span class="text-xs text-neutral-400">
      {ids.length} photo{ids.length === 1 ? '' : 's'}{#if checked.size}<span class="text-sky-300"> · {checked.size} checked</span>{/if}{#if !loading && empty}<span class="text-amber-300/90"> · {empty} without a caption or tags</span>{/if}
    </span>
    <div class="ml-auto flex items-center gap-2">
      <button class={btn} disabled={!undoCount} title="Undo the last edit here (Ctrl+Z)" onclick={undo}>Undo</button>
      <button class="text-neutral-400 hover:text-white" aria-label="Close (Esc)" onclick={close}>✕</button>
    </div>
  </header>

  {#if error || notice}
    <p class="px-4 py-1.5 text-xs {error ? 'bg-red-950/60 text-red-300' : 'bg-emerald-950/60 text-emerald-200'}">{error || notice}</p>
  {/if}

  <div class="flex min-h-0 flex-1">
    <aside class="w-72 shrink-0 space-y-5 overflow-y-auto border-r border-neutral-800 bg-neutral-900/60 p-4 text-xs">
      <section class="space-y-2">
        <h3 class="text-[11px] font-semibold uppercase tracking-wider text-neutral-500">Generate for {workLabel}</h3>
        {@render methodCard('riffle', 'riffle', "Riffle's tags", 'Keywords from the tags in the sidebar and your learned tags. Instant.')}
        {@render methodCard('phrases', 'phrases', 'Scene phrases', 'Keywords from a list of ~500 subjects and settings that stand out for each photo. Instant.')}
        {@render methodCard('joycaption', 'joy', 'JoyCaption', 'A written caption and/or keywords from a vision language model. A few seconds per photo.')}
        {#if choice === 'joy'}
          <div class="space-y-1.5 rounded border border-neutral-800 p-2">
            <label class="flex items-center justify-between gap-2">
              Caption
              <select bind:value={joyCaption} class="rounded border border-neutral-700 bg-neutral-950 px-1 py-0.5">
                <option value="">none</option>
                <option value="short">short</option>
                <option value="medium">medium</option>
                <option value="detailed">detailed</option>
              </select>
            </label>
            <label class="flex items-center gap-2"><input type="checkbox" bind:checked={joyKeywords} /> Keywords too</label>
            <details>
              <summary class="cursor-pointer text-neutral-500">Own prompt</summary>
              <textarea
                bind:value={joyPrompt}
                rows="3"
                placeholder="Replaces the caption prompt, e.g. Describe the light in one sentence."
                class="mt-1 w-full rounded border border-neutral-700 bg-neutral-950 p-1"
              ></textarea>
            </details>
          </div>
        {/if}

        <details bind:open={booruOpen} class="rounded border border-neutral-800">
          <summary class="cursor-pointer px-2.5 py-1.5 text-neutral-400">Illustrations / booru tags</summary>
          <div class="space-y-2 p-2 pt-0">
            {@render methodCard('wd', 'wd', 'WD tagger', 'Danbooru tags for illustrations and anime art. Not reliable on photos.')}
            {#if choice === 'wd'}
              <div class="space-y-1.5 rounded border border-neutral-800 p-2">
                <label class="flex items-center justify-between gap-2">
                  Threshold
                  <input type="number" min="0.05" max="0.95" step="0.05" bind:value={wdThreshold} class="w-16 rounded border border-neutral-700 bg-neutral-950 px-1 py-0.5" />
                </label>
                <label class="flex items-center gap-2"><input type="checkbox" bind:checked={wdCharacters} /> Character names</label>
                <label class="flex items-center gap-2"><input type="checkbox" bind:checked={wdRating} /> Rating tag</label>
              </div>
            {/if}
            {@render methodCard('joycaption', 'joy-booru', 'JoyCaption, Danbooru tags', 'Booru-style tags from JoyCaption; works on photos too.')}
            {#if choice === 'joy-booru'}
              <label class="flex items-center gap-2 px-1"><input type="checkbox" bind:checked={keepPrefixed} /> Keep artist: / copyright: / meta: tags</label>
            {/if}
            <label class="block">
              <span class="text-neutral-400">Master tag list</span>
              <select bind:value={taglist} class="mt-0.5 w-full rounded border border-neutral-700 bg-neutral-950 px-1 py-0.5">
                <option value="">None: keep every tag</option>
                {#each lists as l (l.name)}
                  <option value={l.name}>{l.name}</option>
                {/each}
              </select>
              <span class="mt-0.5 block text-neutral-500">Keeps only tags on the list (aliases and spelling variants are mapped to it).</span>
            </label>
            {#if !lists.some((l) => l.name === 'danbooru')}
              <button class={btn} disabled={downloading} onclick={getDanbooru}>{downloading ? 'Downloading…' : 'Download the Danbooru list'}</button>
            {/if}
          </div>
        </details>

        <label class="flex items-center justify-between gap-2">
          Existing
          <select bind:value={mode} class="rounded border border-neutral-700 bg-neutral-950 px-1 py-0.5">
            <option value="add">keep, add new tags</option>
            <option value="replace">replace generated, keep typed</option>
            <option value="replace_all">replace everything</option>
          </select>
        </label>
        {#if mode === 'add'}
          <label class="flex items-center justify-between gap-2">
            Photos
            <select bind:value={scope} class="rounded border border-neutral-700 bg-neutral-950 px-1 py-0.5">
              <option value="all">all {work.length}</option>
              <option value="empty">only the {missing.length} missing it</option>
            </select>
          </label>
        {/if}

        {#if job?.running}
          <div class="space-y-1">
            <div class="h-1.5 overflow-hidden rounded bg-neutral-800">
              <div class="h-full bg-sky-500 transition-all" style="width: {job.total ? (100 * job.done) / job.total : 0}%"></div>
            </div>
            <div class="flex items-center justify-between text-neutral-400">
              <span>{job.total ? `${job.done} of ${job.total}` : job.step || 'starting'}{job.lines?.length ? ` · ${job.lines.at(-1)}` : ''}</span>
              <button class="text-red-300 hover:underline" onclick={() => cancelCaptioning().then(refreshStatus)}>Cancel</button>
            </div>
          </div>
        {:else}
          <button
            class="w-full rounded bg-sky-700 px-3 py-1.5 font-medium text-white hover:bg-sky-600 disabled:opacity-40"
            disabled={!targets.length || (chosen && !chosen.available) || (choice === 'joy' && !joyCaption && !joyKeywords)}
            onclick={generate}>Generate for {targets.length} photo{targets.length === 1 ? '' : 's'}</button
          >
          {#if !targets.length && work.length}
            <p class="text-neutral-500">They all have it already: choose all photos, or replace.</p>
          {/if}
        {/if}
      </section>

      <section class="space-y-2">
        <h3 class="text-[11px] font-semibold uppercase tracking-wider text-neutral-500">Tags of {workLabel}</h3>
        <form
          class="flex gap-1"
          onsubmit={(e) => {
            e.preventDefault();
            const t = addAll.trim();
            addAll = '';
            if (t) change(() => bulkTags(work, 'add', t));
          }}
        >
          <input bind:value={addAll} list="caption-tags" placeholder="Add a tag to them" class="min-w-0 flex-1 rounded border border-neutral-700 bg-neutral-950 px-1.5 py-0.5" />
          <button class={btn} disabled={!addAll.trim()}>Add</button>
        </form>
        <form
          class="flex items-center gap-1"
          onsubmit={(e) => {
            e.preventDefault();
            replaceTag();
          }}
        >
          <input bind:value={replaceFrom} list="caption-tags" placeholder="Replace" class="min-w-0 flex-1 rounded border border-neutral-700 bg-neutral-950 px-1.5 py-0.5" />
          <span class="text-neutral-500">→</span>
          <input bind:value={replaceTo} placeholder="with" class="min-w-0 flex-1 rounded border border-neutral-700 bg-neutral-950 px-1.5 py-0.5" />
          <button class={btn} disabled={!replaceFrom.trim() || !replaceTo.trim()}>OK</button>
        </form>
        <div class="flex gap-1">
          <button class={btn} title="Remove every tag from them (Ctrl+Z undoes)" onclick={() => clearAll('tags')}>Clear tags</button>
          <button class={btn} title="Remove their captions (Ctrl+Z undoes)" onclick={() => clearAll('captions')}>Clear captions</button>
        </div>
        {#if !tagCounts.length}
          <p class="text-neutral-500">No tags yet.</p>
        {/if}
        <ul>
          {#each tagCounts as t (t.tag.toLowerCase())}
            <li class="group/t flex items-center gap-1 rounded px-1 py-0.5 hover:bg-neutral-800 {only?.toLowerCase() === t.tag.toLowerCase() ? 'bg-sky-900/60' : ''}">
              {#if renaming === t.tag}
                <!-- svelte-ignore a11y_autofocus -->
                <input
                  value={t.tag}
                  autofocus
                  class="min-w-0 flex-1 rounded border border-sky-700 bg-neutral-950 px-1"
                  onkeydown={(e) => {
                    if (e.key === 'Enter') renameTag(t.tag, e.currentTarget.value);
                    if (e.key === 'Escape') {
                      e.stopPropagation();
                      renaming = null;
                    }
                  }}
                  onblur={(e) => renameTag(t.tag, e.currentTarget.value)}
                />
              {:else}
                <button
                  class="min-w-0 flex-1 truncate text-left text-neutral-300"
                  title="Show only the photos with this tag"
                  onclick={() => {
                    only = only?.toLowerCase() === t.tag.toLowerCase() ? null : t.tag;
                    page = 0;
                  }}>{t.tag}</button
                >
                <span class="tabular-nums text-neutral-500 group-hover/t:hidden">{t.n}</span>
                <button class="hidden px-1 text-neutral-400 hover:text-white group-hover/t:block" title="Rename (or merge into another tag) in {workLabel}" onclick={() => (renaming = t.tag)}>✎</button>
                <button
                  class="hidden px-1 font-bold text-neutral-400 hover:text-red-300 group-hover/t:block"
                  title="Remove from {workLabel}"
                  onclick={() => change(() => bulkTags(work, 'remove', t.tag))}>×</button
                >
              {/if}
            </li>
          {/each}
        </ul>
      </section>
    </aside>

    <main class="min-w-0 flex-1 overflow-y-auto">
      {#if loading}
        <p class="p-4 text-sm text-neutral-500">Loading…</p>
      {:else}
        <div class="sticky top-0 z-10 flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-neutral-800 bg-neutral-950/95 px-4 py-1.5 text-xs text-neutral-400 backdrop-blur">
          <span>Check:</span>
          <button class="text-sky-400 hover:underline" onclick={() => checkAll(shown)}>all{only ? ' shown' : ''}</button>
          <button class="text-sky-400 hover:underline" onclick={() => checkAll(pageIds)}>this page</button>
          <button class="text-sky-400 hover:underline" onclick={() => checkAll(shown.filter(isEmpty))}>without caption or tags</button>
          {#if checked.size}
            <button class="text-sky-400 hover:underline" onclick={() => checked.clear()}>none</button>
            <span class="text-sky-300">{checked.size} checked: the panel on the left works on them</span>
          {:else}
            <span class="text-neutral-600">Nothing checked: the panel works on all {ids.length}. Shift+click checks a range.</span>
          {/if}
          {#if only}
            <span class="ml-auto">
              {shown.length} with <span class="text-sky-300">{only}</span> · <button class="text-sky-400 hover:underline" onclick={() => (only = null)}>show all</button>
            </span>
          {/if}
        </div>
        <!-- Cards: one column on narrow screens, more side by side on wide ones. -->
        <ul class="grid gap-x-3 gap-y-3 p-3" style="grid-template-columns: repeat(auto-fill, minmax(34rem, 1fr))">
          {#each pageIds as id (id)}
            {@const d = data[id]}
            {#if d}
              <li class="flex gap-4 rounded-lg border p-3 {checked.has(id) ? 'border-sky-700 bg-sky-950/30' : 'border-neutral-900 bg-neutral-900/30'}">
                <div class="relative shrink-0">
                  <button title="Open the photo" onclick={() => (view.photo = id)}>
                    <img src="/thumbs/{id}.jpg" alt={d.rel_path} loading="lazy" class="h-32 w-32 rounded object-cover" />
                  </button>
                  <input
                    type="checkbox"
                    class="absolute left-1.5 top-1.5 h-4 w-4 cursor-pointer accent-sky-500"
                    aria-label="Check {d.rel_path}"
                    title="Check (Shift+click: a range)"
                    checked={checked.has(id)}
                    onclick={(e) => toggleCheck(e, id)}
                  />
                </div>
                <div class="min-w-0 flex-1 space-y-2">
                  <p class="truncate text-[11px] text-neutral-500" title={d.rel_path}>
                    {d.rel_path}{#if d.method && d.method !== 'manual'}<span class="ml-2 text-neutral-600">caption: {d.method}</span>{/if}
                  </p>
                  {#key d.caption}
                    <textarea
                      rows="2"
                      placeholder="Caption"
                      value={d.caption ?? ''}
                      onblur={(e) => saveCaption(id, e.currentTarget.value)}
                      class="w-full resize-y rounded border border-neutral-800 bg-neutral-900 px-2 py-1 text-sm text-neutral-200 outline-none placeholder:text-neutral-600 focus:border-sky-700"
                    ></textarea>
                  {/key}
                  <div class="flex flex-wrap items-center gap-1">
                    {#each d.tags as tag, i (tag)}
                      <span
                        role="listitem"
                        draggable="true"
                        ondragstart={() => (dragging = { id, index: i })}
                        ondragover={(e) => e.preventDefault()}
                        ondrop={() => drop(id, i)}
                        class="flex cursor-grab items-center gap-0.5 rounded-full border border-neutral-700 bg-neutral-900 py-0.5 pl-2 pr-1 text-xs text-neutral-300"
                      >
                        {tag}
                        <button
                          class="rounded-full px-1 text-neutral-500 hover:bg-neutral-700 hover:text-white"
                          aria-label="Remove {tag}"
                          onclick={() => setTags(id, d.tags.filter((t) => t !== tag))}>×</button
                        >
                      </span>
                    {/each}
                    <input
                      list="caption-tags"
                      placeholder="+ tag"
                      class="w-28 rounded border border-transparent bg-transparent px-1.5 py-0.5 text-xs outline-none placeholder:text-neutral-600 focus:border-neutral-700"
                      onkeydown={(e) => {
                        if (e.key === 'Enter' || e.key === ',') {
                          e.preventDefault();
                          addTags(id, e.currentTarget);
                        }
                      }}
                      onblur={(e) => addTags(id, e.currentTarget)}
                    />
                  </div>
                </div>
              </li>
            {/if}
          {/each}
        </ul>
        {#if pages > 1}
          <div class="flex items-center justify-center gap-3 py-3 text-xs text-neutral-400">
            <button class={btn} disabled={page === 0} onclick={() => page--}>‹ Previous</button>
            Page {page + 1} of {pages}
            <button class={btn} disabled={page >= pages - 1} onclick={() => page++}>Next ›</button>
          </div>
        {/if}
      {/if}
    </main>
  </div>
</div>

<datalist id="caption-tags">
  {#each tagCounts as t (t.tag.toLowerCase())}
    <option value={t.tag}></option>
  {/each}
</datalist>
