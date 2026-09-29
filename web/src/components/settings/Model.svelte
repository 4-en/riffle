<script>
  // Settings → AI model: which CLIP model reads the photos (models.py). Switching embeds
  // every photo with the new model first (an index run; the current model stays in use
  // meanwhile), then the app uses it. A model used before is ready at once.
  import { fetchModels, chooseModel } from '../../lib/api.js';

  let { status, busy, act } = $props();

  let listing = $state(null);
  let error = $state('');
  let customName = $state('');
  let customPretrained = $state('');

  async function load() {
    try {
      listing = await fetchModels();
      error = '';
    } catch (e) {
      error = e.message;
    }
  }
  // Again as indexing moves on (a preparation finishing switches the model).
  $effect(() => {
    status?.running;
    status?.finished_at;
    load();
  });

  const ready = (m) => listing && m.embedded >= listing.photos;
  const GROUPS = [
    ['general', 'General', 'For any library.'],
    ['nature', 'Nature', 'Specialised: better in their field, weaker elsewhere.'],
    ['custom', 'Other', ''],
  ];

  async function use(name, pretrained, label, m = null) {
    const n = listing?.photos ?? 0;
    const note = m && ready(m)
      ? `${label} has read all your photos already: switching takes a moment.`
      : `Every photo (${n.toLocaleString()}) is read with ${label} once${m?.size_gb ? `, after a ${m.size_gb} GB download` : ''}. ` +
        (listing?.device === 'cpu' ? 'On this computer (no GPU) that can take hours for a large library. ' : '') +
        'The current model stays in use until it is done.';
    if (!confirm(`Use ${label}?\n\n${note}`)) return;
    await act(() => chooseModel(name, pretrained));
    await load();
  }

  const preparing = $derived(listing?.preparing ?? null);
  const card = 'rounded border px-3 py-2';
</script>

<h3 class="text-base font-semibold text-neutral-100">AI model</h3>
<p class="mt-1 text-xs text-neutral-400">
  The model that reads your photos for search, tags, Similar, Discover and Curate. Each model keeps what it has read, so switching
  back to one you used before is instant.
  {#if listing}
    Running on {listing.device === 'cuda' ? 'the GPU' : listing.device === 'mps' ? 'the Apple GPU' : 'the CPU'}{listing.device === 'cpu'
      ? ': the larger models read photos slowly here.'
      : '.'}
  {/if}
</p>

{#if error}<p class="mt-2 text-xs text-red-300">{error}</p>{/if}
{#if preparing}
  <p class="mt-3 rounded border border-sky-900 bg-sky-950/40 px-3 py-2 text-xs text-sky-200">
    Preparing <span class="font-mono">{preparing.name}</span>: every photo is read with it once. The current model stays in use until it
    is done; see <em>Indexing</em> for the progress.
  </p>
{/if}

{#if !listing}
  <p class="mt-3 text-neutral-500">Loading…</p>
{:else}
  {#each GROUPS as [group, title, hint] (group)}
    {@const items = listing.models.filter((m) => m.group === group)}
    {#if items.length}
      <h4 class="mb-2 mt-5 text-xs font-semibold uppercase tracking-wider text-neutral-500">
        {title}{#if hint}<span class="ml-2 font-normal normal-case tracking-normal text-neutral-500">{hint}</span>{/if}
      </h4>
      <div class="space-y-2">
        {#each items as m (m.name + m.pretrained)}
          <div class="{card} {m.in_use ? 'border-sky-800 bg-sky-950/30' : 'border-neutral-800'}">
            <div class="flex items-center gap-2">
              <span class="font-medium text-neutral-100">{m.label}</span>
              <span class="font-mono text-[11px] text-neutral-500">{m.name}{m.pretrained ? ` / ${m.pretrained}` : ''}</span>
              <span class="ml-auto flex shrink-0 items-center gap-2 text-[11px]">
                {#if m.in_use}
                  <span class="rounded bg-sky-700 px-1.5 text-white">In use</span>
                {:else if ready(m)}
                  <span class="rounded bg-emerald-900 px-1.5 text-emerald-200" title="It has read all your photos: switching is instant">Ready</span>
                {:else if m.embedded}
                  <span class="text-neutral-400">{m.embedded.toLocaleString()} of {listing.photos.toLocaleString()} read</span>
                {:else if m.size_gb}
                  <span class="text-neutral-500">{m.size_gb} GB download</span>
                {/if}
                {#if !m.in_use}
                  <button
                    class="rounded border border-neutral-700 px-2 py-0.5 text-xs text-neutral-200 hover:bg-neutral-800 disabled:opacity-40"
                    disabled={busy || status?.running || !!preparing}
                    title={status?.running ? 'Wait until indexing has finished' : ''}
                    onclick={() => use(m.name, m.pretrained, m.label, m)}>Use</button
                  >
                {/if}
              </span>
            </div>
            <p class="mt-0.5 text-xs text-neutral-400">
              {m.description}{m.speed && m.speed !== '1×' ? ` Indexing: ${m.speed}.` : ''}
            </p>
          </div>
        {/each}
      </div>
    {/if}
  {/each}

  <h4 class="mb-2 mt-5 text-xs font-semibold uppercase tracking-wider text-neutral-500">Another OpenCLIP model</h4>
  <form
    class="flex flex-wrap items-center gap-2 text-xs"
    onsubmit={(e) => {
      e.preventDefault();
      if (customName.trim()) use(customName.trim(), customPretrained.trim(), customName.trim());
    }}
  >
    <input
      bind:value={customName}
      placeholder="Name, e.g. ViT-SO400M-14-SigLIP or hf-hub:org/model"
      class="min-w-0 flex-1 rounded border border-neutral-700 bg-neutral-950 px-2 py-1 font-mono text-neutral-100 outline-none focus:border-sky-600"
    />
    <input
      bind:value={customPretrained}
      placeholder="Weights, e.g. webli"
      disabled={customName.trim().startsWith('hf-hub:')}
      class="w-40 rounded border border-neutral-700 bg-neutral-950 px-2 py-1 font-mono text-neutral-100 outline-none focus:border-sky-600 disabled:opacity-40"
    />
    <button
      type="submit"
      class="rounded border border-neutral-700 px-3 py-1 text-neutral-200 hover:bg-neutral-800 disabled:opacity-40"
      disabled={!customName.trim() || busy || status?.running || !!preparing}>Use</button
    >
  </form>
  <p class="mt-1 text-[11px] text-neutral-500">
    Any model OpenCLIP can load (its model list, or a Hugging Face repository in OpenCLIP's format). Stacks are fitted to it from your
    camera bursts; the tag vocabulary and styles were written for general models.
  </p>
{/if}
