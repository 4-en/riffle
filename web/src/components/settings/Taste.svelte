<script>
  // Settings → Your taste: the model that learns what you tend to keep.
  import { calibrateTaste } from '../../lib/api.js';

  // taste: its status; ontaste(status): it was (re)calibrated; onerror(message).
  let { taste = null, ontaste = () => {}, onerror = () => {} } = $props();

  let calibrating = $state(false);
  async function calibrate() {
    calibrating = true;
    try {
      ontaste(await calibrateTaste());
      onerror('');
    } catch (e) {
      onerror(e.message);
    } finally {
      calibrating = false;
    }
  }
</script>

<div class="flex items-start justify-between gap-4">
  <div>
    <h3 class="text-base font-semibold text-neutral-100">Your taste</h3>
    <p class="mt-1 text-xs text-neutral-400">
      Riffle can learn what you tend to keep from your picks, rejects and exports, and sort by it. Curate uses it too. It only
      orders photos; it never flags anything. Once calibrated, it recalibrates by itself when Riffle starts if your flags changed.
    </p>
  </div>
  <button
    class="shrink-0 rounded bg-sky-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-600 disabled:opacity-40"
    disabled={calibrating}
    title="Learn from your current picks, rejects and exports (a second or two)"
    onclick={calibrate}>{calibrating ? 'Calibrating…' : taste?.calibrated ? 'Recalibrate' : 'Calibrate'}</button
  >
</div>

<div class="mt-4 text-xs">
  {#if !taste}
    <p class="text-neutral-500">Checking…</p>
  {:else if !taste.calibrated}
    <p class="text-neutral-400">Not calibrated yet. Flag some photos, then press <em>Calibrate</em>.</p>
  {:else if taste.enabled}
    <p class="text-neutral-300">
      Learned from {taste.keeper_scenes} keeper scenes and {taste.reject_scenes} rejected ones. Checked on photos it did not learn
      from, <strong>{Math.round(taste.top20_recall * 100)}%</strong> of your keepers are in its top fifth (quality {taste.auc.toFixed(2)},
      where 0.5 is chance).
    </p>
    <p class="mt-1 text-neutral-500">
      Use <em>Sort → Likely keepers first</em> to review the promising photos first, or <em>Likely rejects first</em> to clear out
      misses quickly.
    </p>
  {:else}
    <p class="text-neutral-400">Not active. {taste.reason}</p>
  {/if}
  {#if taste?.calibrated}
    <p class="mt-2 {taste.changed_since >= 25 ? 'text-amber-300' : 'text-neutral-500'}">
      Calibrated {new Date(taste.calibrated_at * 1000).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}{taste.changed_since
        ? ` · ${taste.changed_since} flag${taste.changed_since === 1 ? '' : 's'} changed since${taste.changed_since >= 25 ? ': worth recalibrating' : ''}`
        : ' · up to date'}
    </p>
  {/if}
</div>
