<script>
  // The editor's Find mode (segment.py, editing.retouch_regions): find what some words
  // name in every photo the editor has, review the masks, then remove it (filled from the
  // surroundings; LaMa) or replace it (with a prompt; Stable Diffusion). Each changed
  // photo gets one step in its edit list, undone here or in the editor's list. Refine
  // takes one photo's mask to the brush, to correct it and use any tool on it.
  import { fetchEditingTools, chooseEditModel, batchFind, batchApply, fetchBatchJob, deleteEdit } from '../lib/api.js';

  // onchange(ids): these photos' edits changed. onrefine(id, maskUrl): paint with this mask.
  // onshow(id): show the photo's result in the editor.
  let { ids, onchange = () => {}, onrefine = () => {}, onshow = () => {} } = $props();

  let tools = $state([]);
  let error = $state('');
  let text = $state('');
  let threshold = $state(0.3);
  let grow = $state(1); // % of the long side
  let token = $state(null);
  let items = $state([]); // [{id, found, labels, coverage, overlay, error, edit_id, applied, keep}]
  let running = $state(null); // the batch job's status
  let stage = $state('find'); // find | review | done
  let action = $state('remove'); // remove | inpaint
  let prompt = $state('');
  let negative = $state('');

  const toolOf = (key) => tools.find((t) => t.key === key);
  const found = $derived(items.filter((i) => i.found));
  const none = $derived(items.filter((i) => !i.found));
  const kept = $derived(found.filter((i) => i.keep));

  async function loadTools() {
    try {
      tools = (await fetchEditingTools()).tools;
    } catch (e) {
      error = e.message;
    }
  }
  loadTools();

  async function wait() {
    let status;
    do {
      await new Promise((r) => setTimeout(r, 400));
      status = await fetchBatchJob();
      running = status;
    } while (status.running);
    running = null;
    if (status.error) throw new Error(status.error);
    return status.result;
  }

  async function find() {
    error = '';
    try {
      token = (await batchFind({ ids, text, threshold, grow: grow / 100 })).token;
      running = { step: 'starting' };
      const res = await wait();
      items = res.items.map((i) => ({ ...i, keep: i.found > 0 }));
      stage = 'review';
    } catch (e) {
      running = null;
      error = e.message;
    }
  }

  async function apply() {
    error = '';
    try {
      await batchApply({ token, ids: kept.map((i) => i.id), tool: action, prompt, negative });
      running = { step: 'starting' };
      const res = await wait();
      const byId = Object.fromEntries(res.items.map((i) => [i.id, i]));
      items = items.map((i) => (byId[i.id] ? { ...i, ...byId[i.id] } : i));
      stage = 'done';
      onchange(res.items.filter((i) => i.applied).map((i) => i.id));
    } catch (e) {
      running = null;
      error = e.message;
    }
  }

  async function undo(item) {
    try {
      await deleteEdit(item.edit_id);
      item.applied = false;
      item.undone = true;
      onchange([item.id]);
    } catch (e) {
      error = e.message;
    }
  }

  async function setModel(t, value) {
    if (value === '__custom') {
      const spec = window.prompt(
        t.key === 'segment'
          ? 'A detector and a segmenter, "detector+segmenter" (Hugging Face repos or local folders):'
          : 'A Hugging Face repo ("org/model", or "org/model:file.pt") or a local path:',
      );
      if (!spec) return;
      value = spec;
    }
    try {
      tools = (await chooseEditModel(t.key, value)).tools;
    } catch (e) {
      error = e.message;
    }
  }

  function refine(item) {
    item.keep = false; // done by hand now
    onrefine(item.id, item.mask);
  }

  const pct = (x) => (x < 0.001 ? '<0.1' : (x * 100).toFixed(x < 0.1 ? 1 : 0));
  const btn = 'rounded border border-neutral-700 px-2.5 py-1 text-xs text-neutral-200 hover:bg-neutral-800 disabled:opacity-40';
  const busy = $derived(!!running);
</script>

{#snippet modelSelect(t)}
  {#if t}
    <select
      class="w-full rounded border border-neutral-700 bg-neutral-950 px-1.5 py-1 text-neutral-200"
      value={t.models.some((m) => m.key === t.chosen) ? t.chosen : '__current'}
      disabled={busy}
      onchange={(e) => setModel(t, e.currentTarget.value)}
    >
      {#each t.models as m (m.key)}
        <option value={m.key}>{m.label}{m.size_gb ? ` (${m.size_gb} GB)` : ''}</option>
      {/each}
      {#if !t.models.some((m) => m.key === t.chosen)}<option value="__current">{t.chosen}</option>{/if}
      <option value="__custom">Another model…</option>
    </select>
    {#if !t.available}<p class="text-amber-300">{t.reason}</p>{/if}
  {/if}
{/snippet}

<div class="flex min-h-0 flex-1">
  <main class="min-w-0 flex-1 overflow-y-auto p-4">
    {#if error}
      <p class="mb-3 rounded bg-red-950/60 px-3 py-1.5 text-xs text-red-300">{error}</p>
    {/if}
    {#if stage === 'find' && !items.length}
      <div class="flex h-full items-center justify-center text-center text-sm text-neutral-500">
        {busy
          ? `${running.step}${running.total ? ` (${running.done}/${running.total})` : ''}…`
          : `Say what to find in ${ids.length === 1 ? 'this photo' : `these ${ids.length} photos`}, e.g. "people", "cars, bicycles", "power lines".`}
      </div>
    {:else}
      {#if found.length}
        <div class="grid grid-cols-[repeat(auto-fill,minmax(16rem,1fr))] gap-3">
          {#each found as item (item.id)}
            <figure class="overflow-hidden rounded border {item.keep && stage === 'review' ? 'border-sky-700' : 'border-neutral-800'} bg-neutral-900">
              {#if stage === 'done' && (item.applied || item.undone)}
                <img src="/thumbs/{item.id}.jpg?v={item.applied ? item.edit_id : 'undone'}" alt="" class="h-48 w-full object-contain" />
              {:else}
                <img src={item.overlay} alt="" class="h-48 w-full object-contain {item.keep || stage !== 'review' ? '' : 'opacity-40'}" />
              {/if}
              <figcaption class="flex items-center gap-2 px-2 py-1.5 text-xs">
                {#if stage === 'review'}
                  <label class="flex items-center gap-1.5 text-neutral-200">
                    <input type="checkbox" bind:checked={item.keep} />
                    {item.found} found
                  </label>
                  <button
                    class="ml-auto rounded px-1.5 py-0.5 text-neutral-400 hover:bg-neutral-800 hover:text-white"
                    title="Correct this mask with the brush, then use any tool on it"
                    disabled={busy}
                    onclick={() => refine(item)}>Refine</button
                  >
                {:else}
                  <span class="text-neutral-300">{item.found} found</span>
                {/if}
                <span class="text-neutral-500">{pct(item.coverage)} % of the photo</span>
                {#if item.error}<span class="truncate text-red-400" title={item.error}>{item.error}</span>{/if}
                {#if stage === 'done'}
                  <span class="ml-auto flex gap-1">
                    {#if item.applied}
                      <button class="rounded px-1.5 py-0.5 text-neutral-400 hover:bg-neutral-800 hover:text-white" onclick={() => onshow(item.id)}>Show</button>
                      <button class="rounded px-1.5 py-0.5 text-neutral-400 hover:bg-neutral-800 hover:text-red-300" onclick={() => undo(item)}>Undo</button>
                    {:else if item.undone}
                      <span class="text-neutral-500">undone</span>
                    {/if}
                  </span>
                {/if}
              </figcaption>
            </figure>
          {/each}
        </div>
      {/if}
      {#if none.length}
        <details class="mt-4 text-xs text-neutral-400">
          <summary class="cursor-pointer">Nothing found in {none.length} photo{none.length === 1 ? '' : 's'}</summary>
          <div class="mt-2 flex flex-wrap gap-1.5">
            {#each none as item (item.id)}
              <img src="/thumbs/{item.id}.jpg" alt="" title={item.error ?? ''} class="h-16 rounded {item.error ? 'ring-1 ring-red-500' : ''}" />
            {/each}
          </div>
        </details>
      {/if}
    {/if}
  </main>

  <aside class="w-80 shrink-0 space-y-4 overflow-y-auto border-l border-neutral-800 bg-neutral-900 p-4 text-xs">
    <section class="space-y-2">
      <h3 class="font-semibold uppercase tracking-wider text-neutral-500">1. Find</h3>
      <input
        bind:value={text}
        placeholder="What to find, e.g. people, cars"
        disabled={busy}
        onkeydown={(e) => e.key === 'Enter' && text.trim() && !busy && find()}
        class="w-full rounded border border-neutral-700 bg-neutral-950 px-2 py-1.5 text-sm text-neutral-100 outline-none focus:border-sky-600"
      />
      <label class="block text-neutral-300" title="How sure the model must be: lower finds more, including wrong things">
        <span class="flex justify-between"><span>Sensitivity</span><span class="text-neutral-500">{threshold <= 0.25 ? 'finds more' : threshold >= 0.4 ? 'only clear ones' : 'normal'}</span></span>
        <input type="range" min="0.15" max="0.5" step="0.05" bind:value={threshold} disabled={busy} class="w-full accent-sky-600" style="direction: rtl" />
      </label>
      <label class="block text-neutral-300" title="Masks are grown a little, so edges and halos are covered too">
        <span class="flex justify-between"><span>Grow masks</span><span class="tabular-nums text-neutral-500">{grow} %</span></span>
        <input type="range" min="0" max="4" step="0.5" bind:value={grow} disabled={busy} class="w-full accent-sky-600" />
      </label>
      {@render modelSelect(toolOf('segment'))}
      <button
        class="w-full rounded bg-sky-700 px-3 py-1.5 font-medium text-white hover:bg-sky-600 disabled:opacity-40"
        disabled={busy || !text.trim() || !toolOf('segment')?.available}
        onclick={find}>{items.length ? 'Find again' : 'Find'}</button
      >
    </section>

    {#if stage !== 'find'}
      <section class="space-y-2">
        <h3 class="font-semibold uppercase tracking-wider text-neutral-500">2. Change</h3>
        <label class="flex items-start gap-2 text-neutral-200">
          <input type="radio" bind:group={action} value="remove" disabled={busy} class="mt-0.5" />
          <span>Remove<span class="block text-neutral-400">Filled with what would be behind it, from the surroundings.</span></span>
        </label>
        {#if action === 'remove'}<div class="pl-5">{@render modelSelect(toolOf('remove'))}</div>{/if}
        <label class="flex items-start gap-2 text-neutral-200">
          <input type="radio" bind:group={action} value="inpaint" disabled={busy} class="mt-0.5" />
          <span>Replace<span class="block text-neutral-400">With what you describe.</span></span>
        </label>
        {#if action === 'inpaint'}
          <div class="space-y-1.5 pl-5">
            <textarea bind:value={prompt} rows="2" placeholder="What should be there, e.g. empty cobblestone street" disabled={busy}
              class="w-full rounded border border-neutral-700 bg-neutral-950 px-2 py-1 text-neutral-100 outline-none focus:border-sky-600"></textarea>
            <input bind:value={negative} placeholder="Not this (optional)" disabled={busy}
              class="w-full rounded border border-neutral-700 bg-neutral-950 px-2 py-1 text-neutral-100 outline-none focus:border-sky-600" />
            {@render modelSelect(toolOf('inpaint'))}
          </div>
        {/if}
        {#if stage === 'review'}
          <div class="flex gap-1.5">
            <button class={btn} disabled={busy} onclick={() => found.forEach((i) => (i.keep = true))}>All</button>
            <button class={btn} disabled={busy} onclick={() => found.forEach((i) => (i.keep = false))}>None</button>
          </div>
          <button
            class="w-full rounded bg-sky-700 px-3 py-1.5 font-medium text-white hover:bg-sky-600 disabled:opacity-40"
            disabled={busy || !kept.length || (action === 'inpaint' && !prompt.trim()) || !toolOf(action)?.available}
            onclick={apply}>{action === 'remove' ? 'Remove' : 'Replace'} in {kept.length} photo{kept.length === 1 ? '' : 's'}</button
          >
        {:else}
          <p class="text-neutral-400">
            Changed {items.filter((i) => i.applied).length} photos. Undo one here; in Brush or View the step is in each photo's list.
          </p>
          <button class={btn} disabled={busy} onclick={() => (stage = 'review')}>Back to the masks</button>
        {/if}
      </section>
    {/if}

    {#if running}
      <p class="text-neutral-300">
        {running.step}{running.total ? ` (${running.done}/${running.total})` : ''}…
      </p>
    {/if}
  </aside>
</div>
