<script>
  // Settings → Flags & exports: the counts, and resets (deliberately low-key: rarely wanted).
  import { view, activeFilterCount, tagFilterActive } from '../../lib/state.svelte.js';
  import { resetFlags } from '../../lib/culling.svelte.js';
  import { postResetExported } from '../../lib/api.js';

  // tags / facets: the app's counts (global picks and rejects; flags within the filters).
  // onchange(): refresh the counts; onerror(message).
  let { tags = null, facets = null, onchange = () => {}, onerror = () => {} } = $props();

  const flagged = $derived((tags?.picks ?? 0) + (tags?.rejects ?? 0));
  const filtering = $derived(tagFilterActive() || activeFilterCount(view.filters) > 0);
  const flaggedInFilters = $derived((facets?.flag?.pick ?? 0) + (facets?.flag?.reject ?? 0));
  let note = $state('');

  async function reset(scope) {
    const what = scope === 'all'
      ? `all ${flagged} flagged photos (${tags.picks} picked, ${tags.rejects} rejected)`
      : `the ${flaggedInFilters} flagged photos in the current filters`;
    if (!confirm(`Unflag ${what}?\n\nThey become unflagged again. You can undo this with Ctrl+Z until you reload the page.`)) return;
    try {
      const n = await resetFlags(view, scope);
      note = `Unflagged ${n} photo${n === 1 ? '' : 's'}. Ctrl+Z undoes it.`;
      onerror('');
    } catch (e) {
      onerror(e.message);
    }
  }

  async function forgetExports() {
    if (!confirm(`Forget that ${tags.exported} photos were exported?\n\nThe exported files are not touched. Riffle stops marking these photos as exported, and "only new" exports include them again. This cannot be undone.`)) return;
    try {
      const { cleared } = await postResetExported(view, 'all');
      note = `Forgot the export history of ${cleared} photo${cleared === 1 ? '' : 's'}.`;
      onchange(); // refresh counts and the grid badges
      onerror('');
    } catch (e) {
      onerror(e.message);
    }
  }

  const danger = 'rounded border border-neutral-700 px-3 py-1 text-xs text-neutral-300 hover:border-red-800 hover:bg-red-950 disabled:opacity-40';
</script>

<h3 class="text-base font-semibold text-neutral-100">Flags &amp; exports</h3>
<p class="mt-1 text-xs text-neutral-400">
  In this profile: {tags?.picks ?? 0} picked, {tags?.rejects ?? 0} rejected, {tags?.exported ?? 0} exported. They are saved with
  your tags and captions (see <button class="text-sky-400 hover:underline" onclick={() => (view.settings = 'files')}>Files</button>).
</p>

<h4 class="mb-2 mt-5 text-xs font-semibold uppercase tracking-wider text-neutral-500">Start over</h4>
<div class="flex flex-wrap gap-2">
  {#if filtering}
    <button class={danger} disabled={!flaggedInFilters} onclick={() => reset('filtered')}>Unflag photos in the current filters ({flaggedInFilters})…</button>
  {/if}
  <button class={danger} disabled={!flagged} onclick={() => reset('all')}>Unflag all photos ({flagged})…</button>
  <button
    class={danger}
    disabled={!tags?.exported}
    title="Photos stay exported on disk; Riffle just stops marking them (↗) and 'only new' exports include them again"
    onclick={forgetExports}>Forget export history ({tags?.exported ?? 0})…</button
  >
</div>
{#if note}<p class="mt-2 text-xs text-emerald-400">{note}</p>{/if}
