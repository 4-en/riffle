<script>
  // Settings → Indexing: run it, and follow a run.
  import { startIndex } from '../../lib/api.js';

  let { status, busy, act } = $props();
  const pct = $derived(status?.total ? Math.min(100, (100 * status.done) / status.total) : null);
</script>

<div class="flex items-start justify-between gap-4">
  <div>
    <h3 class="text-base font-semibold text-neutral-100">Indexing</h3>
    <p class="mt-1 text-xs text-neutral-400">
      Finds new and changed photos in your folders and computes what Riffle needs: thumbnails, the AI model's view of each photo,
      tags, duplicates and stacks, sharpness, colours, and locations. A run only fills in what is missing, so it is quick when
      little has changed. It covers every profile's folders, so switching profiles never waits for it, and it runs by itself after
      adding a folder no profile had.
    </p>
  </div>
  <button
    class="shrink-0 rounded bg-sky-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-600 disabled:opacity-40"
    disabled={busy || status?.running}
    onclick={() => act(startIndex)}>Index now</button
  >
</div>

<div class="mt-4">
  {#if status?.running}
    <p class="text-neutral-200">{status.step}{status.pending ? ' (another run queued)' : ''}</p>
    <div class="mt-1 h-1.5 overflow-hidden rounded bg-neutral-800">
      {#if pct !== null}
        <div class="h-full bg-sky-600 transition-[width]" style="width: {pct}%"></div>
      {:else}
        <div class="h-full w-1/3 animate-pulse bg-sky-700"></div>
      {/if}
    </div>
    {#if status.total}
      <p class="mt-1 text-xs tabular-nums text-neutral-500">{status.done} / {status.total}</p>
    {/if}
  {:else if status?.error}
    <p class="text-red-400">Indexing failed: {status.error}</p>
  {:else if status?.finished_at}
    <p class="text-neutral-400">Last run finished {new Date(status.finished_at * 1000).toLocaleTimeString()}.</p>
  {:else}
    <p class="text-neutral-500">Not run since the server started. Use <span class="font-mono">riffle index</span> or <em>Index now</em>.</p>
  {/if}
  {#if status?.lines?.length}
    <pre class="mt-3 max-h-72 overflow-y-auto rounded bg-neutral-950 p-2 text-[11px] leading-relaxed text-neutral-400">{status.lines.join('\n')}</pre>
  {/if}
</div>
