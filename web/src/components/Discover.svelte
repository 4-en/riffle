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
  import { selection, setFlag, undo } from '../lib/culling.svelte.js';

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
        heading: trail.at(-1).heading ?? null,
      });
      if (mine !== token) return;
      data = res;
      if (cameFrom != null) trail.at(-1).heading = res.heading; // the walk's direction, for momentum later
      drift = res.drift;
      error = '';
    } catch (e) {
      if (mine === token) error = e.message;
    } finally {
      if (mine === token) loading = false;
    }
  }
  load();

  /** Step to a photo of a branch; the trail remembers how you got there (for Replay). */
  function go(photo, branch) {
    const from = centre;
    trail = [...trail, { id: photo.id, lens: branch.lens, label: branch.label, reason: photo.reason, seed: newSeed() }];
    prefs = { ...prefs, [branch.lens]: (prefs[branch.lens] ?? 0) + 1 };
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

  // ---- the walk: remove photos, export, pick, replay --------------------------------

  const walkIds = $derived([...new Set(trail.map((t) => t.id))]);
  let notice = $state('');

  /** Take a photo out of the walk (not the one you are at). It may show up in branches again. */
  function removeStep(i) {
    if (i >= trail.length - 1) return;
    trail = trail.filter((_, k) => k !== i);
    load();
  }
  /** Curate around the walk: its photos locked in a draft, the rest filled from the filters. */
  function curateWalk() {
    view.discover = null;
    view.curate = { locked: walkIds };
  }
  function exportWalk() {
    view.exporting = { ids: walkIds, fresh: null, kind: 'walk' };
  }
  let picked = $state(false);
  async function pickWalk() {
    await setFlag(walkIds, 'pick');
    picked = true;
    notice = `Picked the ${walkIds.length} photos of this walk.`;
  }
  async function undoPick() {
    await undo();
    picked = false;
    notice = '';
  }

  // Replay: the walk as a slideshow, each step with how it was reached.
  let replay = $state(null); // index into trail, or null
  let playing = $state(true);
  // App's Esc would close Discover; while replaying, Esc only ends the replay.
  $effect(() => {
    view.discover.replaying = replay != null;
  });
  $effect(() => {
    if (replay == null || !playing) return;
    const timer = setTimeout(() => (replay = replay < trail.length - 1 ? replay + 1 : 0), 4000);
    return () => clearTimeout(timer);
  });

  function onkeydown(e) {
    if (view.photo != null || view.exporting || e.target instanceof HTMLInputElement) return;
    if (replay != null) {
      e.preventDefault();
      if (e.key === 'Escape') replay = null; else if (e.key === 'ArrowRight') replay = Math.min(trail.length - 1, replay + 1);
      else if (e.key === 'ArrowLeft') replay = Math.max(0, replay - 1);
      else if (e.key === ' ') playing = !playing;
      return;
    }
    if (e.key === 'Backspace') {
      e.preventDefault();
      back();
    } else if (e.key === 'r') {
      reshuffle();
    } else if (/^[1-9]$/.test(e.key) && data?.branches[Number(e.key) - 1]) {
      const b = data.branches[Number(e.key) - 1];
      go(b.photos[0], b);
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
  // The photo under the pointer, shown larger: {lens, id}. Cleared a moment after the
  // pointer leaves (so moving between a photo and its label does not flicker).
  let focus = $state(null);
  let unfocusTimer;
  const EXPANDED_MAX = 270;
  const EXPANDED = $derived(Math.max(MAIN * 1.6, Math.min(EXPANDED_MAX, Math.min(width, height - TRAIL_H) * 0.42)));
  function hoverPhoto(lens, id) {
    clearTimeout(unfocusTimer);
    focus = { lens, id };
    backdrop = id;
  }
  function leavePhoto() {
    clearTimeout(unfocusTimer);
    unfocusTimer = setTimeout(() => (focus = null), 120);
  }
  const MAIN = 104; // a branch's first photo (diameter)
  const SMALL = 50; // its others
  const BEND = 0.14; // how much the edges curve
  const CHAR = 6.1; // average width of a label character at 11 px (for the arc length)
  const TAU = 2 * Math.PI;
  const angleDist = (a, b) => Math.abs(((((a - b) % TAU) + TAU * 1.5) % TAU) - Math.PI);

  /** The largest arc of the circle not covered by ``blocked`` [(centre, half width)]:
   * its centre angle and width (72 steps are plenty). */
  function freeArc(blocked) {
    const steps = 72;
    const free = Array.from({ length: steps }, (_, k) => blocked.every(([c, h]) => angleDist((k / steps) * TAU, c) > h));
    if (free.every(Boolean)) return { centre: 0, width: TAU };
    let best = { start: 0, len: 0 };
    for (let k = 0; k < steps; k++) {
      if (!free[k] || free[(k + steps - 1) % steps]) continue; // start of a run
      let len = 0;
      while (free[(k + len) % steps] && len < steps) len++;
      if (len > best.len) best = { start: k, len };
    }
    return { centre: ((best.start + (best.len - 1) / 2) / steps) * TAU, width: (best.len / steps) * TAU };
  }

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
    // Branches with a real direction (nearby places) go where the place is, north up;
    // the others share what is left, the left side staying free for the way back.
    const fixedAngle = (b) => (b.bearing != null ? ((b.bearing - 90) * Math.PI) / 180 : null);
    const blocked = [...(previous ? [[Math.PI, 0.45]] : []), ...branches.filter((b) => b.bearing != null).map((b) => [fixedAngle(b), 0.32])];
    const free = [];
    for (let k = 0; k < 360; k++) {
      const a = -Math.PI / 2 + (k / 360) * TAU; // clockwise from the top
      if (blocked.every(([c, h]) => angleDist(a, c) > h)) free.push(a);
    }
    const loose = branches.filter((b) => b.bearing == null).length;
    const stepA = (free.length / 360) * TAU / Math.max(loose, 1);
    let nextLoose = 0;
    const nodes = branches.map((b, i) => {
      let angle = fixedAngle(b);
      if (angle == null) {
        const k = Math.floor(((nextLoose++ + 0.5) * free.length) / Math.max(loose, 1));
        angle = (free[Math.min(k, free.length - 1)] ?? -Math.PI / 2 + (TAU * i) / Math.max(n, 1)) + (rand() - 0.5) * stepA * 0.4;
      }
      // Closer to the centre the more alike (similarity ~0.3 at the edge … ~0.9 near), a little jitter.
      const alike = Math.min(1, Math.max(0, ((b.photos[0].sim ?? 0.5) - 0.3) / 0.6));
      const inner = (size / 2 + MAIN / 2 + 16) / Math.min(rx, ry); // never on top of the centre
      const reach = Math.max(inner, 1.06 - 0.5 * alike + (rand() - 0.5) * 0.08);
      const x = cx + rx * reach * Math.cos(angle);
      const y = cy + ry * reach * Math.sin(angle);
      const hoverMain = focus?.lens === b.lens && focus.id === b.photos[0].id;
      const big = hoverMain ? EXPANDED : MAIN * ((prefs[b.lens] ?? 0) >= 2 ? 1.15 : 1);

      // The edge arrives along its curve: its direction at the node points to the control point.
      const mx = (cx + x) / 2 + (y - cy) * BEND;
      const my = (cy + y) / 2 - (x - cx) * BEND;
      const edgeIn = Math.atan2(my - y, mx - x);

      // The label on an arc above or below the circle, whichever is further from the edge;
      // as wide as its longer line (shortened beyond ~150°).
      const r = big / 2 + 7;
      const maxChars = Math.floor((2.6 * (r + 12)) / CHAR);
      const cut = (t) => (t.length > maxChars ? t.slice(0, maxChars - 1) + '…' : t);
      const label = cut(b.label);
      const reason = cut(b.reason);
      const top = angleDist(-Math.PI / 2, edgeIn) >= angleDist(Math.PI / 2, edgeIn);
      const labelAt = top ? -Math.PI / 2 : Math.PI / 2;
      const span = Math.min(2.7, (Math.max(label.length, reason.length) * CHAR) / (r + 6) + 0.2);

      // The other photos in the largest gap left by the label and the edge.
      const arc = freeArc([
        [labelAt, span / 2 + 0.2],
        [edgeIn, 0.45],
      ]);
      const others = b.photos.slice(1);
      const d = big / 2 + SMALL / 2 + 6;
      const stepM = Math.min(1.0, others.length > 1 ? (arc.width - 0.5) / (others.length - 1) : 1);
      const moons = others.map((p, k) => {
        const a = arc.centre + (k - (others.length - 1) / 2) * stepM + (rand() - 0.5) * 0.12;
        return { p, x: Math.cos(a) * d, y: Math.sin(a) * d, size: SMALL };
      });

      // Arc paths for the two lines: above the circle drawn clockwise (glyphs upright,
      // outward); below it counter-clockwise, so the text still reads left to right.
      const arcPath = (radius) => {
        const a1 = top ? labelAt - span / 2 : labelAt + span / 2;
        const a2 = top ? labelAt + span / 2 : labelAt - span / 2;
        const p1 = [x + radius * Math.cos(a1), y + radius * Math.sin(a1)];
        const p2 = [x + radius * Math.cos(a2), y + radius * Math.sin(a2)];
        return `M${p1[0]},${p1[1]} A${radius},${radius} 0 0 ${top ? 1 : 0} ${p2[0]},${p2[1]}`;
      };
      // Above: the label outside, the reason nearer the circle; below: the other way round.
      const labelPath = top ? arcPath(r + 13) : arcPath(r + 9);
      const reasonPath = top ? arcPath(r + 1) : arcPath(r + 22);
      return { b, x, y, dx: 0, dy: 0, big, moons, label, reason, labelPath, reasonPath, float: 5 + rand() * 4, delay: -rand() * 8 };
    });

    // A hovered photo grows where it is; everything it would cover moves out of its way:
    // other branches (with their labels) and the branch's own other photos.
    const hit = focus && nodes.find((n) => n.b.lens === focus.lens);
    if (hit) {
      const moon = hit.moons.find((m) => m.p.id === focus.id);
      const fx = hit.x + (moon ? moon.x : 0);
      const fy = hit.y + (moon ? moon.y : 0);
      const reach = EXPANDED / 2;
      const push = (x, y, r) => {
        const vx = x - fx, vy = y - fy;
        const d = Math.hypot(vx, vy) || 1;
        const need = reach + r + 8;
        return d < need ? [(vx / d) * (need - d), (vy / d) * (need - d)] : [0, 0];
      };
      const margin = MAIN / 2 + 30;
      for (const n of nodes) {
        if (n === hit && !moon) continue; // the grown photo itself stays put
        [n.dx, n.dy] = push(n.x, n.y, n.big / 2 + 30); // + the label around it
        // …but not off the screen.
        n.dx = Math.min(width - margin, Math.max(margin, n.x + n.dx)) - n.x;
        n.dy = Math.min(h - margin, Math.max(margin, n.y + n.dy)) - n.y;
      }
      if (moon) {
        moon.x -= hit.dx; // the hovered small photo stays under the pointer
        moon.y -= hit.dy;
        moon.size = EXPANDED;
      }
      for (const m of hit.moons) {
        if (m === moon) continue;
        const [px, py] = push(hit.x + hit.dx + m.x, hit.y + hit.dy + m.y, SMALL / 2);
        m.x += px;
        m.y += py;
      }
    }
    const compassShown = branches.some((b) => b.bearing != null);
    return { cx, cy, size, nodes, compassShown, northY: cy - ry - 30, backX: cx - rx * 0.92, backY: cy + (rand() - 0.5) * 40 };
  });
  const edge = (x, y, bend = BEND) => {
    const { cx, cy } = geometry;
    const mx = (cx + x) / 2 + (y - cy) * bend;
    const my = (cy + y) / 2 - (x - cx) * bend;
    return `M${cx},${cy} Q${mx},${my} ${x},${y}`;
  };

  // The background: the last photo hovered (the centre until then), blurred and muted,
  // drifting slowly, with soft glows where the photo is brightest.
  let backdrop = $state(untrack(() => start));
  $effect(() => {
    backdrop = centre;
  });

  const GRID = 8; // the thumbnail is read at GRID × GRID to find its bright areas
  const glowCache = new Map();
  let glows = $state([]);

  /** Up to 3 glows at the photo's brightest areas: [{x, y (0..1), colour, strength, speed}]. */
  function findGlows(id) {
    if (glowCache.has(id)) return Promise.resolve(glowCache.get(id));
    return new Promise((resolve) => {
      const img = new Image();
      img.onload = () => {
        const canvas = document.createElement('canvas');
        canvas.width = canvas.height = GRID;
        const ctx = canvas.getContext('2d', { willReadFrequently: true });
        ctx.drawImage(img, 0, 0, GRID, GRID);
        const px = ctx.getImageData(0, 0, GRID, GRID).data;
        const cells = [];
        for (let k = 0; k < GRID * GRID; k++) {
          const [r, g, b] = [px[4 * k], px[4 * k + 1], px[4 * k + 2]];
          cells.push({ k, r, g, b, lum: (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 });
        }
        const mean = cells.reduce((s, c) => s + c.lum, 0) / cells.length;
        const found = [];
        for (const c of [...cells].sort((a, b) => b.lum - a.lum)) {
          if (found.length === 3 || c.lum < Math.max(mean + 0.08, 0.35)) break;
          const x = (c.k % GRID + 0.5) / GRID, y = (Math.floor(c.k / GRID) + 0.5) / GRID;
          if (found.some((f) => Math.hypot(f.x - x, f.y - y) < 0.3)) continue; // one glow per bright area
          found.push({ x, y, colour: `${c.r}, ${c.g}, ${c.b}`, strength: Math.min(1, (c.lum - mean) * 2.2 + 0.25), speed: 7 + found.length * 3.5 });
        }
        glowCache.set(id, found);
        resolve(found);
      };
      img.onerror = () => resolve([]);
      img.src = `/thumbs/${id}.jpg`;
    });
  }
  $effect(() => {
    const id = backdrop;
    findGlows(id).then((g) => {
      if (backdrop === id) glows = g;
    });
  });
  const LENS_COLOURS = {
    subject: '#38bdf8',
    light: '#fbbf24',
    colour: '#f472b6',
    shape: '#a3e635',
    tag: '#c084fc',
    opposite: '#fb923c',
    complement: '#2dd4bf',
    moment: '#94a3b8',
    place: '#34d399',
    nearby: '#4ade80',
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
      <span class="mx-1 h-4 w-px bg-neutral-700"></span>
      <button class={btn} title="Play the walk as a slideshow" disabled={trail.length < 2} onclick={() => ((replay = 0), (playing = true))}>Replay</button>
      <button class={btn} title="Flag the photos of this walk as picks" onclick={pickWalk}>Pick walk</button>
      <button class={btn} title="Copy the photos of this walk to a folder, numbered in walk order" onclick={exportWalk}>Export walk…</button>
      <button class={btn} title="Open Curate with the photos of this walk locked in; it fills the rest from the current filters" onclick={curateWalk}>Curate walk…</button>
      <button class={btn} title="Select the photos of this walk in the grid (closes Discover)" onclick={selectTrail}>Select</button>
      <button class={btn} onclick={() => (view.photo = centre)}>Open photo</button>
      <button class="text-neutral-400 hover:text-white" aria-label="Close (Esc)" onclick={() => (view.discover = null)}>✕</button>
    </div>
  </header>

  {#if error}
    <p class="bg-red-950/60 px-4 py-1.5 text-xs text-red-300">{error}</p>
  {:else if notice}
    <p class="flex items-center gap-3 bg-emerald-950/60 px-4 py-1.5 text-xs text-emerald-200">
      {notice}
      {#if picked}<button class="underline" onclick={undoPick}>Undo</button>{/if}
      <button class="ml-auto text-emerald-300/70 hover:text-white" aria-label="Dismiss" onclick={() => (notice = '')}>✕</button>
    </p>
  {/if}

  <div class="relative min-h-0 flex-1 overflow-hidden bg-neutral-950" bind:clientWidth={width} bind:clientHeight={height}>
    {#key backdrop}
      <div class="pointer-events-none absolute inset-0 overflow-hidden" transition:fade={{ duration: 1500 }}>
        <div
          class="backdrop absolute -inset-16 bg-cover bg-center"
          style="background-image: url('/thumbs/{backdrop}.jpg'); filter: blur(20px) saturate(0.55) brightness(0.32)"
        ></div>
        {#each glows as g (g.x + ':' + g.y)}
          <div
            class="glow absolute rounded-full"
            style="left: {g.x * 100}%; top: {g.y * 100}%; width: 55vmin; height: 55vmin;
                   background: radial-gradient(circle, rgba({g.colour}, {0.28 * g.strength}) 0%, rgba({g.colour}, 0) 65%);
                   animation-duration: {g.speed}s"
            in:fade={{ duration: 1200 }}
          ></div>
        {/each}
      </div>
    {/key}
    {#if data}
      {#key `${centre}:${trail.at(-1).seed}`}
        <svg class="pointer-events-none absolute inset-0" {width} height={height - TRAIL_H} in:fade={{ duration: 250 }}>
          {#if geometry.compassShown}
            <!-- North is up for the nearby places -->
            <g transform="translate({geometry.cx}, {Math.max(14, geometry.northY)})" opacity="0.55">
              <path d="M0,-9 L4,3 L0,0 L-4,3 Z" fill="#4ade80" />
              <text y="15" text-anchor="middle" class="text-[10px]" fill="#86efac">N</text>
            </g>
          {/if}
          {#if previous}
            <path d={edge(geometry.backX, geometry.backY, 0.05)} fill="none" stroke="#525252" stroke-width="2" stroke-dasharray="6 5" />
          {/if}
          {#each geometry.nodes as n (n.b.lens)}
            <path
              d={edge(n.x + n.dx, n.y + n.dy)}
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
            class="branch moving absolute {focus?.lens === n.b.lens ? 'z-30' : ''}"
            style="left: {n.x + n.dx}px; top: {n.y + n.dy}px; animation: drift {n.float}s ease-in-out {n.delay}s infinite"
            in:fade={{ duration: 300, delay: 40 * i }}
            onmouseenter={() => (hovered = n.b.lens)}
            onmouseleave={() => (hovered = null)}
          >
            {#each n.moons as m (m.p.id)}
              <button
                class="grow absolute -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-full ring-1 ring-neutral-700 {m.size > SMALL ? 'z-10 shadow-2xl ring-2' : ''}"
                style="left: {m.x}px; top: {m.y}px; width: {m.size}px; height: {m.size}px"
                title="{m.p.reason} · {m.p.rel_path}"
                onmouseenter={() => hoverPhoto(n.b.lens, m.p.id)}
                onmouseleave={leavePhoto}
                onclick={() => go(m.p, n.b)}
              >
                <img src={m.size > SMALL ? `/previews/${m.p.id}.jpg` : m.p.thumb} alt={m.p.rel_path} class="h-full w-full object-cover" />
              </button>
            {/each}
            <button
              class="grow absolute -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-full shadow-lg ring-2 {n.big > MAIN * 1.2 ? 'z-10 shadow-2xl' : ''}"
              style="width: {n.big}px; height: {n.big}px; --tw-ring-color: {colour}"
              title="{n.b.photos[0].reason} · {n.b.photos[0].rel_path}"
              onmouseenter={() => hoverPhoto(n.b.lens, n.b.photos[0].id)}
              onmouseleave={leavePhoto}
              onclick={() => go(n.b.photos[0], n.b)}
            >
              <img src={n.big > MAIN * 1.2 ? `/previews/${n.b.photos[0].id}.jpg` : n.b.photos[0].thumb} alt={n.b.photos[0].rel_path} class="h-full w-full object-cover" />
            </button>
          </div>
        {/each}

        <!-- Labels, bent around each branch's first photo -->
        <svg class="pointer-events-none absolute inset-0" {width} height={height - TRAIL_H} in:fade={{ duration: 400 }}>
          {#each geometry.nodes as n, i (n.b.lens)}
            {@const colour = LENS_COLOURS[n.b.lens] ?? '#737373'}
            <g class="moving" style="animation: drift {n.float}s ease-in-out {n.delay}s infinite; transform: translate({n.dx}px, {n.dy}px)">
              <path d={n.labelPath} id="label-{n.b.lens}" fill="none" />
              <path d={n.reasonPath} id="reason-{n.b.lens}" fill="none" />
              <text class="text-[11px] font-medium" fill={colour} style="paint-order: stroke; stroke: rgb(10 10 10 / 0.7); stroke-width: 3px">
                <textPath href="#label-{n.b.lens}" startOffset="50%" text-anchor="middle"><tspan fill="#737373">{i + 1} </tspan>{n.label}</textPath>
              </text>
              <text class="text-[11px]" fill="#d4d4d4" style="paint-order: stroke; stroke: rgb(10 10 10 / 0.7); stroke-width: 3px">
                <textPath href="#reason-{n.b.lens}" startOffset="50%" text-anchor="middle">{n.reason}</textPath>
              </text>
            </g>
          {/each}
        </svg>
      {/key}
    {:else if !error}
      <p class="p-6 text-sm text-neutral-500">Finding paths… (the first time takes a few seconds)</p>
    {/if}

    <!-- The trail -->
    <div class="absolute inset-x-0 bottom-0 flex items-center gap-1.5 overflow-x-auto border-t border-white/5 bg-neutral-950/50 px-3 backdrop-blur-sm" style="height: {TRAIL_H}px">
      {#each trail as t, i (i)}
        {#if i > 0}
          <span class="shrink-0 text-[10px]" style="color: {LENS_COLOURS[t.lens] ?? '#737373'}">→</span>
        {/if}
        <div class="group/step relative shrink-0">
          <button
            class="overflow-hidden rounded-full {i === trail.length - 1 ? 'ring-2 ring-white/70' : 'opacity-70 hover:opacity-100'}"
            title={i === trail.length - 1 ? 'Here' : `Go back to here${t.label ? ` (reached by ${t.label.toLowerCase()})` : ''}`}
            onclick={() => back(i)}
          >
            <img src="/thumbs/{t.id}.jpg" alt="" class="h-14 w-14 object-cover" />
          </button>
          {#if i < trail.length - 1}
            <button
              class="absolute -right-1 -top-1 hidden h-5 w-5 items-center justify-center rounded-full bg-neutral-800 text-[11px] text-neutral-300 ring-1 ring-neutral-600 hover:bg-red-800 hover:text-white group-hover/step:flex"
              title="Take this photo out of the walk"
              aria-label="Remove from the walk"
              onclick={() => removeStep(i)}>×</button
            >
          {/if}
        </div>
      {/each}
    </div>
  </div>

  {#if replay != null}
    {@const step = trail[replay]}
    <div class="absolute inset-0 z-30 flex flex-col bg-black" role="dialog" aria-label="Replay">
      <div class="relative min-h-0 flex-1">
        {#key replay}
          <img src="/previews/{step.id}.jpg" alt="" class="absolute inset-0 h-full w-full object-contain" transition:fade={{ duration: 900 }} />
        {/key}
      </div>
      <div class="flex items-center gap-4 px-6 py-3 text-sm text-neutral-300">
        <span class="tabular-nums text-neutral-500">{replay + 1} / {trail.length}</span>
        <span class="min-w-0 flex-1 truncate">
          {#if step.label}<span style="color: {LENS_COLOURS[step.lens] ?? '#a3a3a3'}">{step.label}</span> · {step.reason}{:else}The start{/if}
        </span>
        <button class={btn} onclick={() => (replay = Math.max(0, replay - 1))} aria-label="Previous (←)">‹</button>
        <button class={btn} onclick={() => (playing = !playing)}>{playing ? 'Pause' : 'Play'}</button>
        <button class={btn} onclick={() => (replay = Math.min(trail.length - 1, replay + 1))} aria-label="Next (→)">›</button>
        <button class="text-neutral-400 hover:text-white" aria-label="Close (Esc)" onclick={() => (replay = null)}>✕</button>
      </div>
    </div>
  {/if}
</div>

<style>
  /* Branches float a little, each at its own pace (the translate is on a wrapper, so it
     never fights the centring transforms inside). */
  @keyframes drift {
    0%, 100% { translate: 0 0; }
    33% { translate: 2px -3px; }
    66% { translate: -2px 2px; }
  }
  /* The background drifts and breathes very slowly; the glows pulse. Only transform and
     opacity are animated (composited on the GPU; the blur is not redrawn per frame). */
  .backdrop {
    will-change: transform;
    animation: breathe 40s ease-in-out infinite alternate;
  }
  @keyframes breathe {
    from { transform: scale(1.04) translate(0, 0); }
    to { transform: scale(1.12) translate(-1.5%, 1%); }
  }
  .glow {
    translate: -50% -50%;
    mix-blend-mode: screen;
    will-change: transform, opacity;
    animation: pulse 9s ease-in-out infinite alternate;
  }
  @keyframes pulse {
    from { opacity: 0.55; scale: 0.9; }
    to { opacity: 1; scale: 1.1; }
  }
  /* Growing a hovered photo, and making way for it. */
  .grow {
    transition: width 0.75s ease, height 0.75s ease, left 0.75s ease, top 0.75s ease, box-shadow 0.75s;
  }
  .moving {
    transition: left 0.75s ease, top 0.75s ease, transform 0.75s ease;
  }
  @media (prefers-reduced-motion: reduce) {
    .backdrop, .glow, .branch, g { animation: none !important; }
  }
</style>
