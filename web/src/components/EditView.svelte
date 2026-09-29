<script>
  // The editor (edits.py, editing.py): non-destructive edits of the photos it was opened
  // with. Brush: paint over something, then Heal (spots, dust), Remove (objects,
  // people; LaMa) or Replace (with a prompt; Stable Diffusion, 1-4 candidates). Crop:
  // quarter turns, straightening, a crop, a flip. View: the result, hold \ for the
  // original. Find (BatchEdit): find things by name in all the photos, then remove or
  // replace them, or refine one photo's mask with the brush. Every step is in the photo's edit list (turn off, delete, restore the
  // original); the original file is never touched unless the edits are baked in.
  import { untrack } from 'svelte';
  import { view } from '../lib/state.svelte.js';
  import BatchEdit from './BatchEdit.svelte';
  import {
    fetchEditingTools, chooseEditModel, fetchEdits, setGeometry, runEditTool, fetchEditJob,
    keepCandidate, toggleEdit, deleteEdit, restoreOriginal, bakeEdits,
  } from '../lib/api.js';

  // onchange(): the photos' edits changed (the grid shows the new thumbnails).
  let { onchange = () => {} } = $props();

  const ids = untrack(() => view.editing.ids);
  let index = $state(untrack(() => Math.max(0, ids.indexOf(view.editing.start ?? ids[0]))));
  const id = $derived(ids[index]);

  let info = $state(null); // /api/edits/{id}
  let tools = $state([]);
  let error = $state('');
  let notice = $state('');
  let mode = $state(untrack(() => view.editing.mode ?? 'brush')); // brush | find | crop | view
  // Find keeps its masks and results while another mode is used.
  let findOpened = $state(false);
  $effect(() => {
    if (mode === 'find') findOpened = true;
  });
  let showOriginal = $state(false);
  let running = $state(null); // the edit job's status while a tool runs
  let candidates = $state(null); // {token, urls, chosen}

  // ---- loading ------------------------------------------------------------------------
  async function loadTools() {
    try {
      tools = (await fetchEditingTools()).tools;
    } catch (e) {
      error = e.message;
    }
  }
  loadTools();

  async function load() {
    try {
      info = await fetchEdits(id);
      error = '';
      const g = info.edits.find((e) => e.kind === 'geometry')?.params;
      geo = { rot90: g?.rot90 ?? 0, angle: g?.angle ?? 0, crop: g?.crop ?? [0, 0, 1, 1], flip_h: !!g?.flip_h };
    } catch (e) {
      error = e.message;
    }
  }
  $effect(() => {
    id;
    untrack(() => {
      info = null;
      candidates = null;
      natW = natH = 0; // (a mask to paint waits for this photo's image)
      clearMask();
      load();
    });
  });

  function changed(result) {
    info = result;
    candidates = null;
    clearMask();
    onchange();
  }

  // ---- the image and the brush ---------------------------------------------------------
  let areaW = $state(800);
  let areaH = $state(600);
  let natW = $state(0); // the base image's pixels (the editor's canvas)
  let natH = $state(0);
  let zoom = $state(1);
  let brush = $state(40); // screen pixels
  let erase = $state(false);
  let hasMask = $state(false);
  let canvas = $state();
  const fit = $derived(natW ? Math.min((areaW - 32) / natW, (areaH - 32) / natH) : 1);
  const dispW = $derived(Math.round(natW * fit * zoom));
  const dispH = $derived(Math.round(natH * fit * zoom));

  const imageSrc = $derived.by(() => {
    if (!info) return '';
    if (mode === 'view') return showOriginal ? info.original : info.preview;
    if (candidates) return candidates.urls[candidates.chosen];
    return info.base;
  });

  function loaded(e) {
    if (mode === 'view') return;
    const img = e.currentTarget;
    if (img.naturalWidth !== natW || img.naturalHeight !== natH) {
      natW = img.naturalWidth;
      natH = img.naturalHeight;
      if (canvas) {
        canvas.width = natW;
        canvas.height = natH;
        hasMask = false;
      }
    }
  }

  // (A canvas shown again after another mode starts at the default size.)
  $effect(() => {
    if (canvas && natW && (canvas.width !== natW || canvas.height !== natH)) {
      canvas.width = natW;
      canvas.height = natH;
      hasMask = false;
    }
    if (canvas && natW && mode === 'brush' && pendingMask?.id === id) untrack(drawPending);
  });

  // ---- from Find: a photo's found mask, to correct with the brush ------------------------
  let pendingMask = $state(null); // {id, url}
  function refine(pid, url) {
    pendingMask = { id: pid, url };
    candidates = null;
    mode = 'brush';
    index = ids.indexOf(pid);
  }
  async function drawPending() {
    const { url } = pendingMask;
    pendingMask = null;
    const img = new Image();
    img.src = url;
    try {
      await img.decode();
    } catch {
      error = 'The found mask is gone: find again';
      return;
    }
    // White on black → the brush's red, its alpha the mask.
    const off = document.createElement('canvas');
    off.width = canvas.width;
    off.height = canvas.height;
    const octx = off.getContext('2d');
    octx.drawImage(img, 0, 0, off.width, off.height);
    const d = octx.getImageData(0, 0, off.width, off.height);
    for (let i = 0; i < d.data.length; i += 4) {
      const on = d.data[i] > 127;
      d.data[i] = 255;
      d.data[i + 1] = 40;
      d.data[i + 2] = 40;
      d.data[i + 3] = on ? 255 : 0;
    }
    canvas.getContext('2d').putImageData(d, 0, 0);
    hasMask = true;
    notice = 'The found mask: paint or erase to correct it, then choose a tool.';
  }
  function show(pid) {
    index = ids.indexOf(pid);
    mode = 'view';
  }
  function batchChanged(changedIds) {
    onchange();
    if (changedIds.includes(id)) load();
  }

  function clearMask() {
    if (canvas) canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
    hasMask = false;
  }

  let last = null;
  function paint(e) {
    const r = canvas.getBoundingClientRect();
    const s = canvas.width / r.width;
    const p = [(e.clientX - r.left) * s, (e.clientY - r.top) * s];
    const ctx = canvas.getContext('2d');
    ctx.globalCompositeOperation = erase ? 'destination-out' : 'source-over';
    ctx.strokeStyle = ctx.fillStyle = 'rgb(255, 40, 40)';
    ctx.lineWidth = brush * s;
    ctx.lineCap = ctx.lineJoin = 'round';
    ctx.beginPath();
    ctx.moveTo(...(last ?? p));
    ctx.lineTo(...p);
    ctx.stroke();
    last = p;
    if (!erase) hasMask = true;
  }
  function down(e) {
    if (mode !== 'brush' || candidates || running) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    last = null;
    paint(e);
  }
  const move = (e) => last && paint(e);
  const up = () => (last = null);

  // ---- running a tool ---------------------------------------------------------------------
  let tool = $state('heal');
  let prompt = $state('');
  let negative = $state('');
  let count = $state(2);
  let strength = $state(0.99); // Replace: denoising (1 starts from noise; lower keeps more of what is there)
  let blur = $state(3); // the mask's blur where the result meets the photo, in this view's 1600 px
  let fill = $state('original'); // under the mask, below full denoising (editing.FILLS)
  const FILLS = [
    ['original', 'Original', 'What is there now'],
    ['heal', 'Heal', 'Smooth colours from the edges (OpenCV)'],
    ['remove', 'Remove', 'A plausible background (LaMa), for the model to refine'],
    ['noise', 'Noise', 'The surrounding colours with noise: something new in those colours (works with high denoising, 0.8 and up)'],
  ];
  const toolInfo = $derived(tools.find((t) => t.key === tool));

  async function run(seed = null) {
    if (!hasMask && !candidates && !toolInfo?.mask_optional) return;
    error = '';
    const mask = candidates ? candidates.mask : canvas.toDataURL('image/png');
    const choose = toolInfo?.prompt; // prompted tools give candidates to choose from
    try {
      await runEditTool(id, {
        tool,
        mask,
        prompt,
        negative: toolInfo?.kind === 'instruct' ? '' : negative,
        candidates: choose ? count : 1,
        seed,
        strength,
        blur,
        fill,
      });
      running = { step: 'starting' };
      let status;
      do {
        await new Promise((r) => setTimeout(r, 400));
        status = await fetchEditJob();
        running = status;
      } while (status.running);
      running = null;
      if (status.error) throw new Error(status.error);
      const res = status.result;
      if (choose) {
        candidates = { token: res.token, urls: res.candidates, chosen: 0, mask };
      } else {
        changed(await keepCandidate(id, res.token, 0)); // one result: kept at once (undo in the list)
      }
    } catch (e) {
      running = null;
      error = e.message;
    }
  }

  async function keep() {
    try {
      changed(await keepCandidate(id, candidates.token, candidates.chosen));
    } catch (e) {
      error = e.message;
    }
  }

  async function setModel(t, value) {
    if (value === '__custom') {
      const spec = prompt_('A Hugging Face repo ("org/model", or "org/model:file.pt") or a local path:');
      if (!spec) return;
      value = spec;
    }
    try {
      tools = (await chooseEditModel(t.key, value)).tools;
    } catch (e) {
      error = e.message;
    }
  }
  const prompt_ = (text) => window.prompt(text);

  // ---- crop and straighten ------------------------------------------------------------
  let geo = $state({ rot90: 0, angle: 0, crop: [0, 0, 1, 1], flip_h: false });
  let aspect = $state('free');
  const ASPECTS = [
    ['free', 'Free', null],
    ['orig', 'Original', 'orig'],
    ['3:2', '3:2', 3 / 2],
    ['4:3', '4:3', 4 / 3],
    ['1:1', '1:1', 1],
    ['16:9', '16:9', 16 / 9],
  ];
  const turned = $derived(geo.rot90 % 2 === 1);
  // The frame (the base image turned by the quarter turns), fitted into the area.
  const frameW = $derived(Math.round((turned ? natH : natW) * Math.min((areaW - 32) / (turned ? natH : natW), (areaH - 32) / (turned ? natW : natH))));
  const frameH = $derived(Math.round(frameW * (turned ? natW / natH : natH / natW)));

  function ratio() {
    const a = ASPECTS.find((x) => x[0] === aspect)?.[2];
    if (!a) return null;
    const r = a === 'orig' ? (turned ? natH / natW : natW / natH) : a;
    return frameW >= frameH ? r : 1 / r; // (the long side along the frame's long side)
  }
  function applyAspect() {
    const r = ratio();
    if (!r) return;
    let [x, y, w, h] = geo.crop;
    const pw = w * frameW, ph = h * frameH;
    if (pw / ph > r) w = (ph * r) / frameW;
    else h = pw / r / frameH;
    geo.crop = [Math.min(x, 1 - w), Math.min(y, 1 - h), w, h];
  }

  let dragging = null; // {kind: 'move' | corner, start: [px, py], crop}
  function cropDown(e, kind) {
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    dragging = { kind, start: [e.clientX, e.clientY], crop: [...geo.crop] };
  }
  function cropMove(e) {
    if (!dragging) return;
    const dx = (e.clientX - dragging.start[0]) / frameW;
    const dy = (e.clientY - dragging.start[1]) / frameH;
    let [x, y, w, h] = dragging.crop;
    const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
    if (dragging.kind === 'move') {
      x = clamp(x + dx, 0, 1 - w);
      y = clamp(y + dy, 0, 1 - h);
    } else {
      const [kx, ky] = { nw: [-1, -1], ne: [1, -1], sw: [-1, 1], se: [1, 1] }[dragging.kind];
      let nx = x, ny = y, nw = w, nh = h;
      if (kx < 0) (nx = clamp(x + dx, 0, x + w - 0.02)), (nw = w - (nx - x));
      else nw = clamp(w + dx, 0.02, 1 - x);
      if (ky < 0) (ny = clamp(y + dy, 0, y + h - 0.02)), (nh = h - (ny - y));
      else nh = clamp(h + dy, 0.02, 1 - y);
      const r = ratio();
      if (r) {
        // Keep the ratio: the height follows the width.
        const want = (nw * frameW) / r / frameH;
        if (ky < 0) ny = ny + nh - want;
        nh = want;
        if (ny < 0 || ny + nh > 1) return;
      }
      [x, y, w, h] = [nx, ny, nw, nh];
    }
    geo.crop = [x, y, w, h];
  }
  const cropUp = () => (dragging = null);

  function turn(by) {
    geo = { ...geo, rot90: (geo.rot90 + by + 4) % 4, crop: [0, 0, 1, 1] };
  }
  async function applyGeometry() {
    try {
      changed(await setGeometry(id, geo));
      mode = 'view';
    } catch (e) {
      error = e.message;
    }
  }

  // ---- the edit list ---------------------------------------------------------------------
  const LABELS = { geometry: 'Crop & straighten', heal: 'Heal', remove: 'Remove', inpaint: 'Replace', instruct: 'Prompt edit' };
  function describe(e) {
    const p = e.params;
    if (e.kind === 'geometry') {
      const parts = [];
      if (p.rot90) parts.push(`turned ${p.rot90 * 90}°`);
      if (p.angle) parts.push(`straightened ${p.angle.toFixed(1)}°`);
      if (p.crop && (p.crop[2] < 1 || p.crop[3] < 1)) parts.push('cropped');
      if (p.flip_h) parts.push('flipped');
      return parts.join(', ');
    }
    return [p.prompt ? `"${p.prompt}"` : '', p.model].filter(Boolean).join(' · ');
  }
  async function toggle(e) {
    await toggleEdit(e.id, !e.enabled);
    changed(await fetchEdits(id));
  }
  async function remove(e) {
    if (e.kind !== 'geometry' && !confirm(`Delete this ${LABELS[e.kind].toLowerCase()} step? Its pixels are gone for good (turning it off keeps them).`)) return;
    await deleteEdit(e.id);
    changed(await fetchEdits(id));
  }
  async function restore() {
    changed(await restoreOriginal(id));
    notice = 'Back to the original. The steps are kept (turned off): turn any back on.';
  }
  async function undoLast() {
    const lastOn = [...(info?.edits ?? [])].reverse().find((e) => e.enabled);
    if (lastOn) await toggle(lastOn);
  }

  let baking = $state(null); // {backup}
  async function bake() {
    try {
      const done = await bakeEdits(id, baking.backup);
      baking = null;
      notice = done.backup ? `Written into the file. The original is kept at ${done.backup}` : 'Written into the file.';
      changed(await fetchEdits(id));
    } catch (e) {
      error = e.message;
    }
  }

  // ---- keys ------------------------------------------------------------------------------
  function onkeydown(e) {
    const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement;
    if (typing || view.photo != null || baking) return;
    if (e.key === '\\') {
      mode = 'view';
      showOriginal = true;
    } else if (e.key === 'b') mode = 'brush';
    else if (e.key === 'f') mode = 'find';
    else if (e.key === 'c') mode = 'crop';
    else if (e.key === 'v') mode = 'view';
    else if (e.key === '[') brush = Math.max(4, brush - 6);
    else if (e.key === ']') brush = Math.min(300, brush + 6);
    else if (e.key === 'ArrowRight' && index < ids.length - 1) index++;
    else if (e.key === 'ArrowLeft' && index > 0) index--;
    else if ((e.ctrlKey || e.metaKey) && e.key === 'z') undoLast();
    else return;
    e.preventDefault();
  }
  const onkeyup = (e) => e.key === '\\' && (showOriginal = false);

  const btn = 'rounded border border-neutral-700 px-2.5 py-1 text-xs text-neutral-200 hover:bg-neutral-800 disabled:opacity-40';
  const modeBtn = (m) => `rounded px-2.5 py-1 text-xs ${mode === m ? 'bg-sky-700 text-white' : 'text-neutral-300 hover:bg-neutral-800'}`;
</script>

<svelte:window {onkeydown} {onkeyup} />

<div class="fixed inset-0 z-20 flex flex-col bg-neutral-950" role="dialog" aria-modal="true" aria-label="Edit">
  <header class="flex flex-wrap items-center gap-3 border-b border-neutral-800 bg-neutral-900 px-4 py-2 text-sm">
    <h2 class="font-semibold">Edit</h2>
    <span class="truncate text-xs text-neutral-400">{info?.rel_path ?? ''}</span>
    <div class="flex items-center gap-1 rounded bg-neutral-800/60 p-0.5" role="group" aria-label="Mode">
      <button class={modeBtn('brush')} title="Paint over what to heal, remove or replace (B)" onclick={() => (mode = 'brush')}>Brush</button>
      <button
        class={modeBtn('find')}
        title="Find things by name in {ids.length === 1 ? 'this photo' : `all ${ids.length} photos`}, then remove or replace them (F)"
        onclick={() => (mode = 'find')}>Find</button
      >
      <button class={modeBtn('crop')} title="Turn, straighten, crop (C)" onclick={() => (mode = 'crop')}>Crop</button>
      <button class={modeBtn('view')} title="The result; hold \ for the original (V)" onclick={() => (mode = 'view')}>View</button>
    </div>
    <span class="text-[11px] text-neutral-500">Edits never change your files, unless you bake them in.</span>
    <button class="ml-auto text-neutral-400 hover:text-white" aria-label="Close (Esc)" onclick={() => (view.editing = null)}>✕</button>
  </header>

  {#if error || notice}
    <p class="px-4 py-1.5 text-xs {error ? 'bg-red-950/60 text-red-300' : 'bg-emerald-950/60 text-emerald-200'}">{error || notice}</p>
  {/if}

  {#if findOpened}
    <div class="{mode === 'find' ? 'flex' : 'hidden'} min-h-0 flex-1 flex-col">
      <BatchEdit {ids} onchange={batchChanged} onrefine={refine} onshow={show} />
    </div>
  {/if}

  <div class="{mode === 'find' ? 'hidden' : 'flex'} min-h-0 flex-1">
    {#if ids.length > 1}
      <nav class="w-24 shrink-0 space-y-1.5 overflow-y-auto border-r border-neutral-800 p-2" aria-label="Photos">
        {#each ids as pid, k (pid)}
          <button class="block w-full overflow-hidden rounded {k === index ? 'ring-2 ring-sky-500' : 'opacity-70 hover:opacity-100'}" onclick={() => (index = k)}>
            <img src="/thumbs/{pid}.jpg?v={k === index ? (info?.version ?? '') : ''}" alt="" class="h-16 w-full object-cover" />
          </button>
        {/each}
      </nav>
    {/if}

    <main class="relative min-w-0 flex-1 overflow-auto bg-neutral-950" bind:clientWidth={areaW} bind:clientHeight={areaH}>
      {#if info && mode !== 'crop'}
        <div class="flex min-h-full min-w-full items-center justify-center p-4">
          <div class="relative shrink-0" style={mode === 'view' ? '' : `width: ${dispW}px; height: ${dispH}px`}>
            <img
              src={imageSrc}
              alt=""
              onload={loaded}
              draggable="false"
              class={mode === 'view' ? 'max-h-[calc(100vh-8rem)] max-w-full object-contain' : 'block h-full w-full select-none'}
            />
            {#if mode === 'brush'}
              <canvas
                bind:this={canvas}
                class="absolute inset-0 h-full w-full touch-none opacity-50 {candidates ? 'pointer-events-none opacity-0' : 'cursor-crosshair'}"
                onpointerdown={down}
                onpointermove={move}
                onpointerup={up}
              ></canvas>
            {/if}
            {#if mode === 'view' && showOriginal}
              <span class="absolute left-2 top-2 rounded bg-black/70 px-1.5 text-xs text-neutral-200">Original</span>
            {/if}
          </div>
        </div>
      {:else if info && mode === 'crop'}
        <div class="flex h-full w-full items-center justify-center">
          <!-- The frame: the base image turned and straightened; the crop is a part of it. -->
          <div class="relative overflow-hidden" style="width: {frameW}px; height: {frameH}px" onpointermove={cropMove} onpointerup={cropUp} role="presentation">
            <img
              src={info.base}
              alt=""
              draggable="false"
              onload={loaded}
              class="pointer-events-none absolute left-1/2 top-1/2 max-w-none select-none"
              style="width: {turned ? frameH : frameW}px; height: {turned ? frameW : frameH}px; transform: translate(-50%, -50%) rotate({geo.rot90 * 90 + geo.angle}deg)"
            />
            <div
              class="absolute cursor-move border border-white/90"
              style="left: {geo.crop[0] * 100}%; top: {geo.crop[1] * 100}%; width: {geo.crop[2] * 100}%; height: {geo.crop[3] * 100}%; box-shadow: 0 0 0 9999px rgba(0,0,0,0.55)"
              onpointerdown={(e) => cropDown(e, 'move')}
              role="presentation"
            >
              {#each ['nw', 'ne', 'sw', 'se'] as corner (corner)}
                <span
                  class="absolute h-3.5 w-3.5 border-2 border-white bg-black/40 {corner[0] === 'n' ? '-top-1.5' : '-bottom-1.5'} {corner[1] === 'w' ? '-left-1.5' : '-right-1.5'}"
                  style="cursor: {corner}-resize"
                  onpointerdown={(e) => cropDown(e, corner)}
                  role="presentation"
                ></span>
              {/each}
              <div class="pointer-events-none absolute inset-0 grid grid-cols-3 grid-rows-3">
                {#each Array(9) as _, k (k)}<span class="border border-white/15"></span>{/each}
              </div>
            </div>
          </div>
        </div>
      {:else}
        <p class="mt-10 text-center text-sm text-neutral-500">Loading…</p>
      {/if}
      {#if running}
        <div class="absolute inset-x-0 top-3 flex justify-center">
          <span class="rounded bg-black/80 px-3 py-1.5 text-xs text-neutral-200"><span class="mr-2 inline-block h-2 w-2 animate-pulse rounded-full bg-sky-500"></span>{running.step}</span>
        </div>
      {/if}
    </main>

    <aside class="w-80 shrink-0 space-y-5 overflow-y-auto border-l border-neutral-800 bg-neutral-900/60 p-4 text-xs">
      {#if mode === 'brush'}
        <section class="space-y-2">
          <h3 class="font-semibold uppercase tracking-wider text-neutral-500">Brush</h3>
          <label class="block text-neutral-300">
            <span class="flex justify-between"><span>Size</span><span class="text-neutral-500">[ ]</span></span>
            <input type="range" min="4" max="300" bind:value={brush} class="w-full accent-sky-600" />
          </label>
          <label class="block text-neutral-300">
            <span class="flex justify-between"><span>Zoom</span><span class="tabular-nums text-neutral-500">{zoom.toFixed(1)}×</span></span>
            <input type="range" min="1" max="4" step="0.25" bind:value={zoom} class="w-full accent-sky-600" />
          </label>
          <div class="flex gap-2">
            <label class="flex items-center gap-1 text-neutral-300"><input type="checkbox" bind:checked={erase} /> Erase</label>
            <button class="{btn} ml-auto" disabled={!hasMask} onclick={clearMask}>Clear</button>
          </div>
        </section>

        <section class="space-y-2">
          <h3 class="font-semibold uppercase tracking-wider text-neutral-500">Then</h3>
          {#each tools.filter((t) => t.kind !== 'upscale' && t.kind !== 'segment') as t (t.key)}
            <div class="rounded border px-2.5 py-2 {tool === t.key ? 'border-sky-700 bg-sky-950/30' : 'border-neutral-800'}">
              <label class="flex items-start gap-2">
                <input type="radio" bind:group={tool} value={t.key} class="mt-0.5" />
                <span class="min-w-0 flex-1">
                  <span class="font-medium text-neutral-100">{t.label}</span>
                  <span class="block text-neutral-400">{t.description}</span>
                </span>
              </label>
              {#if tool === t.key}
                <div class="mt-2 space-y-1.5 pl-5">
                  {#if t.models.length > 1 || t.models[0].spec !== 'builtin'}
                    <select
                      class="w-full rounded border border-neutral-700 bg-neutral-950 px-1.5 py-1 text-neutral-200"
                      value={t.models.some((m) => m.key === t.chosen) ? t.chosen : '__current'}
                      onchange={(e) => setModel(t, e.currentTarget.value)}
                    >
                      {#each t.models as m (m.key)}
                        <option value={m.key}>{m.label}{m.size_gb ? ` (${m.size_gb} GB)` : ''}</option>
                      {/each}
                      {#if !t.models.some((m) => m.key === t.chosen)}<option value="__current">{t.chosen}</option>{/if}
                      <option value="__custom">Another model…</option>
                    </select>
                    <p class="text-[11px] text-neutral-500">{t.models.find((m) => m.key === t.chosen)?.note ?? 'A custom model.'}</p>
                  {/if}
                  {#if !t.available}<p class="text-amber-300">{t.reason}</p>{/if}
                  {#if t.prompt}
                    <textarea
                      bind:value={prompt}
                      rows="2"
                      placeholder={t.kind === 'instruct' ? 'What to change, e.g. make it golden hour' : 'What should be there, e.g. clear blue sky (optional)'}
                      class="w-full rounded border border-neutral-700 bg-neutral-950 px-2 py-1 text-neutral-100 outline-none focus:border-sky-600"
                    ></textarea>
                    {#if t.kind !== 'instruct'}
                      <input bind:value={negative} placeholder="Not this (optional)" class="w-full rounded border border-neutral-700 bg-neutral-950 px-2 py-1 text-neutral-100 outline-none focus:border-sky-600" />
                    {/if}
                    <label class="flex items-center gap-2 text-neutral-300">Candidates
                      <input type="range" min="1" max="4" bind:value={count} class="flex-1 accent-sky-600" /><span class="tabular-nums">{count}</span>
                    </label>
                  {/if}
                  {#if t.kind === 'diffusion'}
                    <label class="block text-neutral-300" title="Where denoising starts: 1 paints from scratch, lower keeps more of what is under the mask (its shapes and colours)">
                      <span class="flex justify-between"><span>Denoising</span><span class="tabular-nums text-neutral-400">{strength.toFixed(2)}</span></span>
                      <input type="range" min="0.1" max="1" step="0.01" bind:value={strength} ondblclick={() => (strength = 0.99)} class="w-full accent-sky-600" />
                    </label>
                    {#if strength < 1}
                      <div class="flex items-center gap-1" role="group" aria-label="Under the mask">
                        <span class="mr-1 text-neutral-400" title="What denoising starts from under the mask">Start from</span>
                        {#each FILLS as [key, label, hint] (key)}
                          <button
                            class="rounded px-1.5 py-0.5 {fill === key ? 'bg-sky-700 text-white' : 'bg-neutral-800 text-neutral-300 hover:bg-neutral-700'}"
                            title={hint}
                            disabled={!!running}
                            onclick={() => (fill = key)}>{label}</button
                          >
                        {/each}
                      </div>
                    {/if}
                  {/if}
                  {#if t.kind !== 'instruct' || hasMask}
                    <label class="block text-neutral-300" title="How softly the result blends into the photo at the mask's edge (in this view's pixels)">
                      <span class="flex justify-between"><span>Mask blur</span><span class="tabular-nums text-neutral-400">{blur} px</span></span>
                      <input type="range" min="0" max="40" step="1" bind:value={blur} ondblclick={() => (blur = 3)} class="w-full accent-sky-600" />
                    </label>
                  {/if}
                  <button
                    class="w-full rounded bg-sky-700 px-3 py-1.5 font-medium text-white hover:bg-sky-600 disabled:opacity-40"
                    disabled={(!hasMask && !t.mask_optional) || !t.available || !!running || !!candidates || (t.kind === 'instruct' && !prompt.trim())}
                    title={hasMask || t.mask_optional ? '' : 'Paint over the area first'}
                    onclick={() => run()}>{t.mask_optional ? (hasMask ? 'Edit the painted area' : 'Edit the whole photo') : t.label}</button
                  >
                </div>
              {/if}
            </div>
          {/each}
          {#if candidates}
            <div class="space-y-1.5 rounded border border-sky-800 p-2">
              <p class="text-neutral-300">Choose one:</p>
              <div class="flex flex-wrap gap-1.5">
                {#each candidates.urls as url, k (url)}
                  <button class="overflow-hidden rounded {candidates.chosen === k ? 'ring-2 ring-sky-500' : 'opacity-70 hover:opacity-100'}" onclick={() => (candidates.chosen = k)}>
                    <img src={url} alt="Candidate {k + 1}" class="h-14 w-auto" />
                  </button>
                {/each}
              </div>
              <div class="flex gap-1.5">
                <button class="flex-1 rounded bg-sky-700 px-2 py-1 font-medium text-white hover:bg-sky-600" onclick={keep}>Keep</button>
                <button class={btn} disabled={!!running} onclick={() => run()}>Try again</button>
                <button class={btn} onclick={() => (candidates = null)}>Discard</button>
              </div>
            </div>
          {/if}
        </section>
      {:else if mode === 'crop'}
        <section class="space-y-2.5">
          <h3 class="font-semibold uppercase tracking-wider text-neutral-500">Crop & straighten</h3>
          <div class="flex gap-1.5">
            <button class={btn} title="Turn left" onclick={() => turn(-1)}>⟲ 90°</button>
            <button class={btn} title="Turn right" onclick={() => turn(1)}>⟳ 90°</button>
            <label class="ml-auto flex items-center gap-1 text-neutral-300"><input type="checkbox" bind:checked={geo.flip_h} /> Flip</label>
          </div>
          <label class="block text-neutral-300">
            <span class="flex justify-between"><span>Straighten</span><span class="tabular-nums text-neutral-400">{geo.angle.toFixed(1)}°</span></span>
            <input type="range" min="-15" max="15" step="0.1" bind:value={geo.angle} ondblclick={() => (geo.angle = 0)} class="w-full accent-sky-600" />
          </label>
          <div class="flex flex-wrap gap-1" role="group" aria-label="Aspect ratio">
            {#each ASPECTS as [key, label] (key)}
              <button
                class="rounded px-2 py-0.5 {aspect === key ? 'bg-sky-700 text-white' : 'bg-neutral-800 text-neutral-300 hover:bg-neutral-700'}"
                onclick={() => {
                  aspect = key;
                  applyAspect();
                }}>{label}</button
              >
            {/each}
          </div>
          <p class="text-[11px] text-neutral-500">Drag the frame or its corners. The flip shows in View.</p>
          <div class="flex gap-1.5">
            <button class="flex-1 rounded bg-sky-700 px-3 py-1.5 font-medium text-white hover:bg-sky-600" onclick={applyGeometry}>Apply</button>
            <button class={btn} onclick={() => (geo = { rot90: 0, angle: 0, crop: [0, 0, 1, 1], flip_h: false })}>Reset</button>
          </div>
        </section>
      {:else}
        <section class="space-y-1 text-neutral-400">
          <p>Hold <kbd class="rounded bg-neutral-800 px-1">\</kbd> to see the original.</p>
          <button class={btn} onmousedown={() => (showOriginal = true)} onmouseup={() => (showOriginal = false)} onmouseleave={() => (showOriginal = false)}>Show original</button>
        </section>
      {/if}

      <section class="space-y-2">
        <h3 class="font-semibold uppercase tracking-wider text-neutral-500">Steps</h3>
        {#if !info?.edits.length}
          <p class="text-neutral-500">No edits yet.</p>
        {:else}
          <ul class="divide-y divide-neutral-800 rounded border border-neutral-800">
            {#each info.edits as e (e.id)}
              <li class="flex items-center gap-2 px-2 py-1.5 {e.enabled ? '' : 'opacity-50'}">
                <button class="shrink-0 text-neutral-300 hover:text-white" title={e.enabled ? 'Turn off' : 'Turn on'} onclick={() => toggle(e)}>{e.enabled ? '◉' : '○'}</button>
                <span class="min-w-0 flex-1">
                  <span class="text-neutral-100">{LABELS[e.kind]}</span>
                  <span class="block truncate text-[11px] text-neutral-500">{describe(e)}</span>
                </span>
                <button class="shrink-0 text-neutral-500 hover:text-red-300" title="Delete this step" onclick={() => remove(e)}>✕</button>
              </li>
            {/each}
          </ul>
          <div class="flex gap-1.5">
            <button class={btn} disabled={!info.edited} onclick={restore}>Restore original</button>
            <button class="{btn} ml-auto border-red-900 text-red-300 hover:bg-red-950" disabled={!info.edited} onclick={() => (baking = { backup: true })}>Bake into file…</button>
          </div>
        {/if}
      </section>
    </aside>
  </div>

  {#if baking}
    <div class="fixed inset-0 z-40 flex items-center justify-center bg-black/70 p-6" role="dialog" aria-modal="true" aria-label="Bake into the file">
      <div class="w-full max-w-md space-y-3 rounded-lg border border-red-900 bg-neutral-900 p-4 text-sm">
        <h3 class="font-semibold text-red-300">Write the edits into the file?</h3>
        <ul class="list-disc space-y-1 pl-5 text-xs text-neutral-300">
          <li><strong>The file itself changes</strong>: <span class="break-all font-mono">{info.rel_path}</span>. Other apps see the edited version.</li>
          <li>The edit steps are done with: this cannot be undone in Riffle{baking.backup ? ', only by putting the backup back' : ''}.</li>
          {#if info.has_raw}<li>Its RAW file is not edited.</li>{/if}
          <li>Your flags, tags and captions for it are kept.</li>
        </ul>
        <label class="flex items-start gap-2 text-xs text-neutral-300">
          <input type="checkbox" bind:checked={baking.backup} class="mt-0.5" />
          <span>Keep the original as a backup (in Riffle's data folder, under originals-backup)</span>
        </label>
        <div class="flex justify-end gap-2">
          <button class={btn} onclick={() => (baking = null)}>Cancel</button>
          <button class="rounded bg-red-700 px-3 py-1 text-xs font-medium text-white hover:bg-red-600" onclick={bake}>Write into the file</button>
        </div>
      </div>
    </div>
  {/if}
</div>
