<script>
  // The active profile in the top bar, with a menu to switch (and to manage them in
  // the Library). Only shown once there is more than one profile.
  import { view } from '../lib/state.svelte.js';
  import { activateProfile } from '../lib/api.js';
  import { reloadForProfile } from '../lib/connection.svelte.js';

  // profiles: [{slug, name, active, picks, ...}] from /api/profiles
  let { profiles = [] } = $props();

  let open = $state(false);
  let error = $state('');
  let root = $state();
  const active = $derived(profiles.find((p) => p.active));

  async function choose(slug) {
    error = '';
    try {
      await activateProfile(slug);
      reloadForProfile();
    } catch (e) {
      error = e.message;
    }
  }

  $effect(() => {
    if (!open) return;
    const outside = (e) => {
      if (!root?.contains(e.target)) open = false;
    };
    window.addEventListener('pointerdown', outside, true);
    return () => window.removeEventListener('pointerdown', outside, true);
  });
</script>

{#if profiles.length > 1 && active}
  <div class="relative shrink-0" bind:this={root}>
    <button
      class="flex max-w-40 items-center gap-1 rounded border border-neutral-700 px-2 py-1 text-xs text-neutral-300 hover:bg-neutral-800"
      title="Profile: whose picks, rejects, export history and tags are shown"
      aria-expanded={open}
      onclick={() => (open = !open)}
    >
      <span class="truncate">{active.name}</span>
      <span class="text-[10px] text-neutral-500">▾</span>
    </button>
    {#if open}
      <div class="absolute right-0 top-full z-40 mt-1 min-w-52 overflow-hidden rounded-md border border-neutral-700 bg-neutral-800 py-1 text-sm shadow-2xl" role="menu">
        {#each profiles as p (p.slug)}
          <button
            class="flex w-full items-center justify-between gap-4 px-3 py-1.5 text-left hover:bg-neutral-700 {p.active ? 'text-sky-300' : 'text-neutral-200'}"
            role="menuitem"
            disabled={p.active}
            onclick={() => choose(p.slug)}
          >
            <span class="truncate">{p.active ? '✓ ' : ''}{p.name}</span>
            <span class="text-[11px] tabular-nums text-neutral-500">{p.picks} picks</span>
          </button>
        {/each}
        {#if error}
          <p class="px-3 py-1 text-xs text-red-300">{error}</p>
        {/if}
        <div class="my-1 border-t border-neutral-700"></div>
        <button
          class="w-full px-3 py-1.5 text-left text-neutral-300 hover:bg-neutral-700"
          role="menuitem"
          onclick={() => {
            open = false;
            view.library = true;
          }}>Manage profiles…</button
        >
      </div>
    {/if}
  </div>
{/if}
