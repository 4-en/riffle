<script>
  // Discover: a walk from photo to photo. The current photo sits in the middle; each
  // branch around it leads to photos related in one way (discover.py: same subject
  // elsewhere, colour echo, shape echo, opposite light…). Clicking one moves there;
  // the trail along the bottom leads back. The walk learns a little as it goes:
  // lenses chosen more often come first and show more, and a drift (the phrases the
  // steps move towards, shown at the top) nudges every branch the same way. Each step
  // samples its branches (a seed per step, kept in the trail, so going back shows the
  // same ones again).
  import { untrack } from 'svelte';
  import { fade, scale } from 'svelte/transition';
  import { view } from '../lib/state.svelte.js';
  import { fetchDiscover } from '../lib/api.js';
  import { selection } from '../lib/culling.svelte.js';

  const newSeed = () => Math.floor(Math.random() * 2 ** 31);
  const start = untrack(() => view.discover.id);
  let trail = $state([{ id: start, lens: null, seed: newSeed() }]); // the walk; the last one is the centre
  let prefs = $state({}); // lens -> times chosen
  let drift = $state({}); // phrase -> weight (from the server)
  let scoped = $state(false); // only photos within the current filters
  let rejects = $state(false); // also rejected photos
  let data = $state(null);
  let error = $state('');
  let loading = $state(false);
  let width = $state(1200);
  let height = $state(800);
  let token = 0;

  const centre = $derived(trail.at(-1).id);
  const previous = $derived(trail.length > 1 ? trail.at(-2) : null);

  async function load(cameFrom = null) {
    const mine = ++token;
    loading = true;
    try {
      const res = await fetchDiscover(view, centre, {
        trail: trail.slice(0, -1).map((t) => t.id),
        came_from: cameFrom,
        prefs,
        drift,
        scoped,
        rejects,
        seed: trail.at(-1).seed,
      });
      if (mine !== token) return;
      data = res;
      drift = res.drift;
      error = '';
    } catch (e) {
      if (mine === token) error = e.message;
    } finally {
      if (mine === token) loading = false;
    }
  }
  load();

  function go(id, lens) {
    const from = centre;
    trail = [...trail, { id, lens, seed: newSeed() }];
    prefs = { ...prefs, [lens]: (prefs[lens] ?? 0) + 1 };
    load(from);
  }
  function back(to = trail.length - 2) {
    if (to < 0) return;
    trail = trail.slice(0, to + 1);
    load();
  }
  /** Other photos from the same place in the walk: a new sample of each branch. */
  function reshuffle() {
    trail = [...trail.slice(0, -1), { ...trail.at(-1), seed: newSeed() }];
    load();
  }
  function resetLearning() {
    prefs = {};
    drift = {};
    load();
  }
  function selectTrail() {
    selection.clear();
    for (const t of trail) selection.add(t.id);
    view.discover = null;
  }

  function onkeydown(e) {
    if (view.photo != null || e.target instanceof HTMLInputElement) return;
    if (e.key === 'Backspace') {
      e.preventDefault();
      back();
    } else if (e.key === 'r') {
      reshuffle();
    } else if (/^[1-9]$/.test(e.key) && data?.branches[Number(e.key) - 1]) {
      const b = data.branches[Number(e.key) - 1];
      go(b.photos[0].id, b.lens);
    }
  }

  // ---- layout ------------------------------------------------------------------------
  // The centre in the middle; branches around it at slightly irregular angles, closer
  // the more alike their first photo is (seeded jitter, so they hold still); each
  // branch's other photos orbit its first one on the outer side; the way back on the left.

  /** A small seeded random generator (mulberry32): the same centre, the same layout. */
  function random(seed) {
    let a = seed >>> 0;
    return () => {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  const TRAIL_H = 96;
  const MAIN = 104; // a branch's first photo (diameter)
  const SMALL = 50; // its others
  const geometry = $derived.by(() => {
    const h = height - TRAIL_H;
    const cx = width / 2;
    const cy = h / 2;
    const size = Math.max(150, Math.min(width, h) * 0.3);
    const rx = Math.max(size / 2 + MAIN, width / 2 - MAIN - 70);
    const ry = Math.max(size / 2 + MAIN * 0.8, h / 2 - MAIN / 2 - 44);
    const branches = data?.branches ?? [];
    const n = branches.length;
    const rand = random(centre * 7919 + n);
    const gap = previous ? 0.9 : 0; // the left side stays free for the way back
    const stepA = (2 * Math.PI - gap) / Math.max(n, 1);
    const big0 = MAIN;
    const nodes = branches.map((b, i) => {
      const base = previous ? Math.PI + gap / 2 + stepA * (i + 0.5) : -Math.PI / 2 + stepA * i;
      const angle = base + (rand() - 0.5) * stepA * 0.55;
      // Closer to the centre the more alike (similarity ~0.3 at the edge … ~0.9 near), a little jitter.
      const alike = Math.min(1, Math.max(0, ((b.photos[0].sim ?? 0.5) - 0.3) / 0.6));
      const inner = (size / 2 + big0 / 2 + 16) / Math.min(rx, ry); // never on top of the centre
      const reach = Math.max(inner, 1.06 - 0.5 * alike + (rand() - 0.5) * 0.08);
      const x = cx + rx * reach * Math.cos(angle);
      const y = cy + ry * reach * Math.sin(angle);
      const big = big0 * ((prefs[b.lens] ?? 0) >= 2 ? 1.15 : 1);
      // The others around the first, on the side away from the centre.
      const out = Math.atan2(y - cy, x - cx);
      const moons = b.photos.slice(1).map((p, k, all) => {
        const spread = 0.85;
        const a = out + (all.length === 1 ? 0.5 : -spread + (2 * spread * k) / Math.max(all.length - 1, 1)) + (rand() - 0.5) * 0.3;
        const d = big / 2 + SMALL / 2 + 4 + rand() * 8;
        return { p, x: Math.cos(a) * d, y: Math.sin(a) * d };
      });
      return { b, x, y, big, moons, labelBelow: y >= cy - 20 };
    });
    return { cx, cy, size, nodes, backX: cx - rx * 0.92, backY: cy + (rand() - 0.5) * 40 };
  });
  const edge = (x, y, bend = 0.14) => {
    const { cx, cy } = geometry;
    const mx = (cx + x) / 2 + (y - cy) * bend;
    const my = (cy + y) / 2 - (x - cx) * bend;
    return `M${cx},${cy} Q${mx},${my} ${x},${y}`;
  };
  const LENS_COLOURS = {
    subject: '#38bdf8',
    light: '#fbbf24',
    colour: '#f472b6',
    shape: '#a3e635',
    tag: '#c084fc',
    opposite: '#fb923c',
    complement: '#2dd4bf',
    moment: '#94a3b8',
    traits: '#e879f9',
    mirror: '#facc15',
    hidden_traits: '#f0abfc',
    hidden_mirror: '#fde68a',
    closest: '#737373',
  };
  let hovered = $state(null);
  const btn = 'rounded border border-neutral-700 px-2.5 py-1 text-xs text-neutral-300 hover:bg-neutral-800 disabled:opacity-40';
</script>

<svelte:window {onkeydown} />

<div class="fixed inset-0 z-20 flex flex-col bg-neutral-950" role="dialog" aria-modal="true" aria-label="Discover">
  <header class="flex flex-wrap items-center gap-3 border-b border-neutral-800 bg-neutral-900 px-4 py-2 text-sm">
    <h2 class="font-semibold">Discover</h2>
    <span class="text-xs text-neutral-400">
      {trail.length - 1} step{trail.length === 2 ? '' : 's'}
      {#if data?.drift_labels?.length}
        · drifting towards <span class="text-sky-300">{data.drift_labels.join(' · ')}</span>
      {/if}
      {#if loading}<span class="text-neutral-500"> · …</span>{/if}
    </span>
    <div class="ml-auto flex flex-wrap items-center gap-2">
      <label class="flex items-center gap-1.5 text-xs text-neutral-400" title="Only photos within the current search and filters">
        <input type="checkbox" bind:checked={scoped} onchange={() => load()} /> Within the current filters
      </label>
      <label class="flex items-center gap-1.5 text-xs text-neutral-400" title="Rejected photos are left out unless this is on">
        <input type="checkbox" bind:checked={rejects} onchange={() => load()} /> Include rejects
      </label>
      <button class={btn} title="Other photos for the same branches (R)" onclick={reshuffle}>Shuffle</button>
      <button class={btn} title="Forget which kinds of branches you follow and where the walk drifts" onclick={resetLearning}>Reset learning</button>
      <button class={btn} title="Select the photos of this walk in the grid" onclick={selectTrail}>Select trail</button>
      <button class={btn} onclick={() => (view.photo = centre)}>Open photo</button>
      <button class="text-neutral-400 hover:text-white" aria-label="Close (Esc)" onclick={() => (view.discover = null)}>✕</button>
    </div>
  </header>

  {#if error}
    <p class="bg-red-950/60 px-4 py-1.5 text-xs text-red-300">{error}</p>
  {/if}

  <div class="relative min-h-0 flex-1 overflow-hidden" bind:clientWidth={width} bind:clientHeight={height}>
    {#if data}
      {#key `${centre}:${trail.at(-1).seed}`}
        <svg class="pointer-events-none absolute inset-0" {width} height={height - TRAIL_H} in:fade={{ duration: 250 }}>
          {#if previous}
            <path d={edge(geometry.backX, geometry.backY, 0.05)} fill="none" stroke="#525252" stroke-width="2" stroke-dasharray="6 5" />
          {/if}
          {#each geometry.nodes as n (n.b.lens)}
            <path
              d={edge(n.x, n.y)}
              fill="none"
              stroke={LENS_COLOURS[n.b.lens] ?? '#737373'}
              stroke-opacity={hovered === n.b.lens ? 0.9 : 0.35}
              stroke-width={hovered === n.b.lens ? 3 : 1.5}
            />
          {/each}
        </svg>

        <!-- The centre -->
        <button
          class="absolute overflow-hidden rounded-full shadow-2xl ring-4 ring-white/15 transition-shadow hover:ring-white/30"
          style="left: {geometry.cx - geometry.size / 2}px; top: {geometry.cy - geometry.size / 2}px; width: {geometry.size}px; height: {geometry.size}px"
          title="Open this photo"
          in:scale={{ duration: 300, start: 0.85 }}
          onclick={() => (view.photo = centre)}
        >
          <img src="/previews/{centre}.jpg" alt={data.centre.rel_path} class="h-full w-full object-cover" />
        </button>

        <!-- The way back -->
        {#if previous}
          <button
            class="group absolute flex -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-1"
            style="left: {geometry.backX}px; top: {geometry.backY}px"
            title="Back (Backspace)"
            in:fade={{ duration: 250 }}
            onclick={() => back()}
          >
            <img src="/thumbs/{previous.id}.jpg" alt="" class="h-16 w-16 rounded-full object-cover opacity-50 ring-1 ring-neutral-600 group-hover:opacity-100" />
            <span class="text-[11px] text-neutral-500 group-hover:text-neutral-300">← back</span>
          </button>
        {/if}

        <!-- Branches -->
        {#each geometry.nodes as n, i (n.b.lens)}
          {@const colour = LENS_COLOURS[n.b.lens] ?? '#737373'}
          <div
            role="group"
            class="absolute"
            style="left: {n.x}px; top: {n.y}px"
            in:fade={{ duration: 300, delay: 40 * i }}
            onmouseenter={() => (hovered = n.b.lens)}
            onmouseleave={() => (hovered = null)}
          >
            {#each n.moons as m (m.p.id)}
              <button
                class="absolute -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-full ring-1 ring-neutral-700 transition hover:z-10 hover:scale-110"
                style="left: {m.x}px; top: {m.y}px; width: {SMALL}px; height: {SMALL}px"
                title="{m.p.reason} · {m.p.rel_path}"
                onclick={() => go(m.p.id, n.b.lens)}
              >
                <img src={m.p.thumb} alt={m.p.rel_path} class="h-full w-full object-cover" />
              </button>
            {/each}
            <button
              class="absolute -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-full shadow-lg ring-2 transition hover:z-10 hover:scale-105"
              style="width: {n.big}px; height: {n.big}px; --tw-ring-color: {colour}"
              title="{n.b.photos[0].reason} · {n.b.photos[0].rel_path}"
              onclick={() => go(n.b.photos[0].id, n.b.lens)}
            >
              <img src={n.b.photos[0].thumb} alt={n.b.photos[0].rel_path} class="h-full w-full object-cover" />
            </button>
            <div
              class="pointer-events-none absolute w-48 -translate-x-1/2 text-center"
              style="top: {n.labelBelow ? n.big / 2 + 6 : -n.big / 2 - 40}px"
            >
              <p class="truncate text-[11px] font-medium" style="color: {colour}">
                <span class="text-neutral-600">{i + 1}</span> {n.b.label}
              </p>
              <p class="truncate text-[11px] text-neutral-400">{n.b.reason}</p>
            </div>
          </div>
        {/each}
      {/key}
    {:else if !error}
      <p class="p-6 text-sm text-neutral-500">Finding paths… (the first time takes a few seconds)</p>
    {/if}

    <!-- The trail -->
    <div class="absolute inset-x-0 bottom-0 flex items-center gap-1.5 overflow-x-auto border-t border-neutral-800 bg-neutral-900/80 px-3" style="height: {TRAIL_H}px">
      {#each trail as t, i (i)}
        {#if i > 0}
          <span class="shrink-0 text-[10px]" style="color: {LENS_COLOURS[t.lens] ?? '#737373'}">→</span>
        {/if}
        <button
          class="shrink-0 overflow-hidden rounded-full {i === trail.length - 1 ? 'ring-2 ring-white/70' : 'opacity-70 hover:opacity-100'}"
          title={i === trail.length - 1 ? 'Here' : 'Go back to here'}
          onclick={() => back(i)}
        >
          <img src="/thumbs/{t.id}.jpg" alt="" class="h-14 w-14 object-cover" />
        </button>
      {/each}
    </div>
  </div>
</div>
