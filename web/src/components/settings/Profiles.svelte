<script>
  // Settings → Profiles: separate sets of flags, export history, your tags, captions and taste.
  import { createProfile, activateProfile, renameProfile, deleteProfile } from '../../lib/api.js';
  import { reloadForProfile } from '../../lib/connection.svelte.js';

  // profiles: /api/profiles list; onprofiles(): refresh it after a change.
  let { profiles = [], onprofiles = () => {} } = $props();

  let newName = $state('');
  let copyCurrent = $state(false);
  let copyParts = $state({ flags: true, exported: true, tags: true, captions: true });
  let renaming = $state(null); // {slug, name}
  let confirmDelete = $state(null); // slug
  let error = $state('');
  const activeProfile = $derived(profiles.find((p) => p.active));

  async function run(fn) {
    error = '';
    try {
      await fn();
    } catch (e) {
      error = e.message;
    }
  }

  const switchTo = (slug) =>
    run(async () => {
      await activateProfile(slug);
      reloadForProfile();
    });

  const createAndSwitch = () =>
    run(async () => {
      const parts = Object.keys(copyParts).filter((k) => copyParts[k]);
      const { slug } = await createProfile(newName, copyCurrent ? activeProfile?.slug : null, copyCurrent ? parts : null);
      newName = '';
      await activateProfile(slug);
      reloadForProfile();
    });

  const saveRename = () =>
    run(async () => {
      await renameProfile(renaming.slug, renaming.name);
      renaming = null;
      onprofiles();
    });

  const remove = (slug) =>
    run(async () => {
      if (confirmDelete !== slug) {
        confirmDelete = slug;
        return;
      }
      await deleteProfile(slug);
      confirmDelete = null;
      onprofiles();
    });
</script>

<h3 class="text-base font-semibold text-neutral-100">Profiles</h3>
<p class="mb-3 mt-1 text-xs text-neutral-400">
  Each profile has its own picks and rejects, export history, your tags, captions, and taste model: one per project, for example.
  The photos, their index, and the settings are shared. With more than one, a switcher appears in the top bar.
</p>

<ul class="divide-y divide-neutral-800 rounded border border-neutral-800">
  {#each profiles as p (p.slug)}
    <li class="flex flex-wrap items-center gap-2 px-3 py-1.5 text-xs">
      {#if renaming?.slug === p.slug}
        <input
          bind:value={renaming.name}
          onkeydown={(e) => e.key === 'Enter' && saveRename()}
          class="min-w-0 flex-1 rounded border border-neutral-700 bg-neutral-950 px-2 py-0.5 text-neutral-100 outline-none focus:border-sky-600"
        />
        <button class="text-sky-400 hover:underline" onclick={saveRename}>Save</button>
        <button class="text-neutral-400 hover:underline" onclick={() => (renaming = null)}>Cancel</button>
      {:else}
        <span class="min-w-0 flex-1 truncate {p.active ? 'font-medium text-sky-300' : 'text-neutral-200'}">{p.name}{p.active ? ' · active' : ''}</span>
        <span class="tabular-nums text-neutral-500">{p.picks} picks · {p.rejects} rejects · {p.tags} tags</span>
        {#if !p.active}
          <button class="text-sky-400 hover:underline" onclick={() => switchTo(p.slug)}>Switch</button>
        {/if}
        <button class="text-neutral-400 hover:underline" onclick={() => (renaming = { slug: p.slug, name: p.name })}>Rename</button>
        {#if p.slug !== 'default' && !p.active}
          <button class={confirmDelete === p.slug ? 'rounded bg-red-800 px-1.5 text-white' : 'text-red-400 hover:underline'} onclick={() => remove(p.slug)}>
            {confirmDelete === p.slug ? 'Click again to delete' : 'Delete'}
          </button>
        {/if}
      {/if}
    </li>
  {/each}
</ul>

<h4 class="mb-2 mt-5 text-xs font-semibold uppercase tracking-wider text-neutral-500">New profile</h4>
<div class="space-y-1.5 rounded border border-neutral-800 p-2 text-xs">
  <div class="flex items-center gap-2">
    <input
      bind:value={newName}
      placeholder="e.g. Photo book 2026"
      class="min-w-0 flex-1 rounded border border-neutral-700 bg-neutral-950 px-2 py-1 text-neutral-100 outline-none focus:border-sky-600"
    />
    <button class="rounded bg-sky-700 px-3 py-1 font-medium text-white hover:bg-sky-600 disabled:opacity-40" disabled={!newName.trim()} onclick={createAndSwitch}
      >Create and switch</button
    >
  </div>
  <div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-neutral-400">
    <label class="flex items-center gap-1"><input type="radio" bind:group={copyCurrent} value={false} /> Start empty</label>
    <label class="flex items-center gap-1"><input type="radio" bind:group={copyCurrent} value={true} /> Copy from {activeProfile?.name ?? 'the current profile'}:</label>
    {#if copyCurrent}
      <label class="flex items-center gap-1"><input type="checkbox" bind:checked={copyParts.flags} /> flags</label>
      <label class="flex items-center gap-1"><input type="checkbox" bind:checked={copyParts.exported} /> export history</label>
      <label class="flex items-center gap-1"><input type="checkbox" bind:checked={copyParts.tags} /> your tags</label>
      <label class="flex items-center gap-1"><input type="checkbox" bind:checked={copyParts.captions} /> captions and fixed tags</label>
    {/if}
  </div>
</div>
{#if error}<p class="mt-1 text-xs text-red-300">{error}</p>{/if}
