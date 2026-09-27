<script>
  // The toolbar above the grid: how the photos are shown (count, grouping and its
  // overview, sort order, stacks). The top bar keeps search and the workflow actions.
  import { view, hasOverview, isLocationGroup, sortFits, groupLabel, LEVELS } from '../lib/state.svelte.js';

  // taste: the taste model's status (enables the "likely keepers / rejects" sorts).
  // groups: the grouped grid's groups (null when not grouped); onjump(key): scroll to one.
  let { total, loading, taste = null, groups = null, onjump = () => {} } = $props();

  const plural = { day: 'days', month: 'months', year: 'years', place: 'places', region: 'regions', country: 'countries', folder: 'folders', similar: 'groups' };

  const searching = $derived(!!(view.q || view.similar));
  const select = 'rounded border border-neutral-700 bg-neutral-900 px-1 py-0.5 text-neutral-200';
  const segment = 'px-2 py-0.5';
  const on = 'bg-sky-800 text-white';
  const off = 'text-neutral-400 hover:bg-neutral-800';
</script>

<div class="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 border-b border-neutral-800 bg-neutral-900/40 px-4 py-1.5 text-xs text-neutral-400">
  <span class="tabular-nums text-neutral-300">
    {#if loading && !total}Loading…{:else}{total.toLocaleString()} {total === 1 ? 'photo' : 'photos'}{/if}
  </span>

  <div class="flex items-center gap-1.5 {searching ? 'opacity-40' : ''}" title={searching ? 'Grouping applies when browsing, not to search results' : 'Group the grid by date, place, folder, or similar content'}>
    <label for="viewbar-group">Group</label>
    <select
      id="viewbar-group"
      bind:value={view.group}
      disabled={searching}
      onchange={() => {
        if (!sortFits(view.group, view.sort)) view.sort = 'taken_at';
      }}
      class={select}
    >
      <option value="">None</option>
      <optgroup label="Date">
        <option value="day">Day</option>
        <option value="month">Month</option>
        <option value="year">Year</option>
      </optgroup>
      <optgroup label="Location">
        <option value="place">Place</option>
        <option value="region">Region</option>
        <option value="country">Country</option>
      </optgroup>
      <optgroup label="Files">
        <option value="folder">Folder</option>
      </optgroup>
      <optgroup label="Content">
        <option value="similar">Similar</option>
      </optgroup>
    </select>
    {#if view.group === 'similar'}
      <div
        class="flex overflow-hidden rounded border border-neutral-700"
        role="group"
        aria-label="How finely to group"
        title="How finely similar photos are grouped: a few broad themes, or many small, close groups"
      >
        {#each LEVELS as level, i (level)}
          <button
            class="{segment} {i ? 'border-l border-neutral-700' : ''} {view.level === level ? on : off}"
            aria-pressed={view.level === level}
            disabled={searching}
            onclick={() => (view.level = level)}>{level[0].toUpperCase() + level.slice(1)}</button
          >
        {/each}
      </div>
    {/if}
    {#if hasOverview(view.group)}
      <div class="flex overflow-hidden rounded border border-neutral-700" role="group" aria-label="Grid or overview">
        <button class="{segment} {view.overview ? off : on}" aria-pressed={!view.overview} disabled={searching} onclick={() => (view.overview = false)}>Grid</button>
        <button
          class="{segment} border-l border-neutral-700 {view.overview ? on : off}"
          aria-pressed={view.overview}
          disabled={searching}
          title="{isLocationGroup(view.group) ? 'Map' : 'Calendar'} of the groups (O)"
          onclick={() => (view.overview = true)}>{isLocationGroup(view.group) ? 'Map' : 'Calendar'}</button
        >
      </div>
    {/if}
  </div>

  <div
    class="flex items-center gap-1.5 {searching ? 'opacity-40' : ''}"
    title={searching
      ? 'Search results are ordered by relevance'
      : taste?.enabled
        ? 'Order photos by date, name, or how likely you are to keep them (learned from your picks and rejects)'
        : 'Order photos by date or name'}
  >
    <label for="viewbar-sort">Sort</label>
    <select
      id="viewbar-sort"
      bind:value={view.sort}
      disabled={searching}
      onchange={() => {
        // The model rates scenes, so a burst's siblings rank together: one tile per stack reads best.
        if (view.sort === 'taste' || view.sort === '-taste') view.collapse = 'stacks';
      }}
      class={select}
    >
      <optgroup label="Date">
        <option value="taken_at">Oldest first</option>
        <option value="-taken_at">Newest first</option>
      </optgroup>
      <optgroup label="Name">
        {#each [['place', 'Place A → Z'], ['-place', 'Place Z → A'], ['name', 'File name A → Z'], ['-name', 'File name Z → A']] as [value, label] (value)}
          <option {value} disabled={!sortFits(view.group, value)}>{label}</option>
        {/each}
      </optgroup>
      <optgroup label="Your taste">
        <option value="taste" disabled={!taste?.enabled || !!view.group}>Likely keepers first</option>
        <option value="-taste" disabled={!taste?.enabled || !!view.group}>Likely rejects first</option>
      </optgroup>
    </select>
  </div>

  <label class="flex cursor-pointer items-center gap-1.5" title="Show one tile per stack of similar shots (S)">
    <input
      type="checkbox"
      checked={view.collapse === 'stacks'}
      onchange={(e) => (view.collapse = e.currentTarget.checked ? 'stacks' : 'dupes')}
      class="accent-sky-600"
    />
    Stacks
  </label>

  {#if groups?.length > 1}
    <div class="ml-auto flex items-center gap-1.5">
      <label for="viewbar-jump">{groups.length} {plural[view.group] ?? 'groups'}</label>
      <select
        id="viewbar-jump"
        class={select}
        onchange={(e) => {
          onjump(e.currentTarget.value);
          e.currentTarget.selectedIndex = 0;
        }}
      >
        <option value="" disabled selected>Jump to…</option>
        {#each groups as g (g.key)}
          <option value={g.key}>{groupLabel(g.key, view.group, g.label)} ({g.count})</option>
        {/each}
      </select>
    </div>
  {/if}
</div>
