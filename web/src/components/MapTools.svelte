<script>
  // The maps' tool switch: pan, or select by box or lasso (lib/mapselect.svelte.js).
  let { tool = $bindable('pan') } = $props();
  const TOOLS = [
    ['pan', 'Pan', 'Drag to move, wheel to zoom (Shift-drag: select with a box)'],
    ['box', 'Box', 'Drag a box to select (Ctrl: add to the selection)'],
    ['lasso', 'Lasso', 'Draw around photos to select them (Ctrl: add to the selection)'],
  ];
</script>

<div class="flex overflow-hidden rounded border border-neutral-700" role="group" aria-label="Map tool">
  {#each TOOLS as [value, label, title], i (value)}
    <button
      class="px-2 py-1 {i ? 'border-l border-neutral-700' : ''} {tool === value ? 'bg-sky-700 text-white' : 'text-neutral-300 hover:bg-neutral-800'}"
      aria-pressed={tool === value}
      {title}
      onclick={() => (tool = value)}>{label}</button
    >
  {/each}
</div>
