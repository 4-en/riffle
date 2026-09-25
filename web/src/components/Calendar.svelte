<script>
  // Calendar overview: a year of month grids; days with photos show a cover and a count.
  // Clicking a day, month or year opens that group in the grouped grid.
  import { untrack } from 'svelte';
  import { view } from '../lib/state.svelte.js';
  import { fetchGroups } from '../lib/api.js';

  let { onopen } = $props();

  let days = $state(new Map()); // "2026-03-12" -> group
  let undated = $state(0);
  let year = $state(null);
  let error = $state('');
  let loading = $state(true);

  $effect(() => {
    JSON.stringify(view.filters), view.tags, view.collapse; // reload when the filters change
    untrack(load);
  });

  async function load() {
    loading = true;
    try {
      const { groups } = await fetchGroups(view, 'day');
      days = new Map(groups.filter((g) => g.key).map((g) => [g.key, g]));
      undated = groups.find((g) => !g.key)?.count ?? 0;
      const ys = [...new Set([...days.keys()].map((k) => Number(k.slice(0, 4))))];
      if (!ys.includes(year)) year = ys.length ? Math.max(...ys) : null;
      error = '';
    } catch (e) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  // Photos per year, for the year list.
  const years = $derived.by(() => {
    const out = new Map();
    for (const [k, g] of days) out.set(Number(k.slice(0, 4)), (out.get(Number(k.slice(0, 4))) ?? 0) + g.count);
    return [...out].sort((a, b) => a[0] - b[0]);
  });

  const pad = (n) => String(n).padStart(2, '0');
  const MONTHS = Array.from({ length: 12 }, (_, i) => i);
  const WEEKDAYS = Array.from({ length: 7 }, (_, i) =>
    new Date(2024, 0, 1 + i).toLocaleDateString(undefined, { weekday: 'narrow' })
  ); // 2024-01-01 is a Monday

  /** Cells for a month, Monday first: null for padding, else {day, key, group}. */
  function cells(y, m) {
    const first = (new Date(y, m, 1).getDay() + 6) % 7;
    const count = new Date(y, m + 1, 0).getDate();
    const out = Array(first).fill(null);
    for (let d = 1; d <= count; d++) {
      const key = `${y}-${pad(m + 1)}-${pad(d)}`;
      out.push({ day: d, key, group: days.get(key) });
    }
    return out;
  }

  const monthTotal = (y, m) => {
    let n = 0;
    const prefix = `${y}-${pad(m + 1)}`;
    for (const [k, g] of days) if (k.startsWith(prefix)) n += g.count;
    return n;
  };
  const monthName = (m) => new Date(2024, m, 1).toLocaleDateString(undefined, { month: 'long' });
</script>

<div class="p-4">
  {#if error}
    <p class="text-sm text-red-400">{error}</p>
  {:else if loading && !days.size}
    <p class="text-sm text-neutral-500">Loading…</p>
  {:else if !days.size}
    <p class="text-sm text-neutral-500">No dated photos in this view.</p>
  {:else}
    <div class="mb-4 flex flex-wrap items-center gap-1">
      {#each years as [y, n] (y)}
        <button
          class="rounded px-2.5 py-1 text-sm {y === year ? 'bg-sky-700 text-white' : 'text-neutral-300 hover:bg-neutral-800'}"
          onclick={() => (year = y)}
        >
          {y} <span class="text-xs tabular-nums opacity-70">{n}</span>
        </button>
      {/each}
      <button class="ml-auto text-xs text-sky-400 hover:underline" onclick={() => onopen('year', String(year))}>
        Open {year} in the grid →
      </button>
    </div>

    <div class="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-6">
      {#each MONTHS as m (m)}
        {@const total = monthTotal(year, m)}
        <section class={total ? '' : 'opacity-40'}>
          <button
            class="mb-1 flex w-full items-baseline justify-between text-left disabled:cursor-default"
            disabled={!total}
            onclick={() => onopen('month', `${year}-${pad(m + 1)}`)}
          >
            <span class="text-sm font-medium text-neutral-100 {total ? 'hover:text-sky-300' : ''}">{monthName(m)}</span>
            {#if total}<span class="text-xs tabular-nums text-neutral-500">{total}</span>{/if}
          </button>
          <div class="grid grid-cols-7 gap-0.5 text-center text-[10px] text-neutral-600">
            {#each WEEKDAYS as w, i (i)}<span>{w}</span>{/each}
            {#each cells(year, m) as cell, i (i)}
              {#if !cell}
                <span></span>
              {:else if cell.group}
                <button
                  class="group relative aspect-square overflow-hidden rounded-sm bg-neutral-800 hover:ring-2 hover:ring-sky-500"
                  title="{new Date(cell.key + 'T00:00').toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })}: {cell.group.count} photos"
                  onclick={() => onopen('day', cell.key)}
                >
                  {#if cell.group.cover}
                    <img src="/thumbs/{cell.group.cover}.jpg" alt="" loading="lazy" class="h-full w-full object-cover opacity-80 group-hover:opacity-100" />
                  {/if}
                  <span class="absolute left-0.5 top-0 text-[9px] font-semibold text-white drop-shadow">{cell.day}</span>
                  <span class="absolute bottom-0 right-0.5 text-[9px] tabular-nums text-white/90 drop-shadow">{cell.group.count}</span>
                </button>
              {:else}
                <span class="flex aspect-square items-center justify-center">{cell.day}</span>
              {/if}
            {/each}
          </div>
        </section>
      {/each}
    </div>

    {#if undated}
      <button class="mt-4 text-xs text-neutral-400 hover:text-sky-300" onclick={() => onopen('day', '')}>
        {undated} undated photo{undated === 1 ? '' : 's'} →
      </button>
    {/if}
  {/if}
</div>
