<script>
  // A collapsible sidebar section. Open/closed is remembered per section (per
  // browser); a collapsed section with an active filter shows how many are set.
  import { untrack } from 'svelte';

  let { id, title, defaultOpen = false, active = 0, children } = $props();

  const key = `riffle.sidebar.${untrack(() => id)}`;
  let open = $state(read(untrack(() => defaultOpen)));

  function read(fallback) {
    try {
      const v = localStorage.getItem(key);
      return v === null ? fallback : v === '1';
    } catch {
      return fallback;
    }
  }

  function toggle() {
    open = !open;
    try {
      localStorage.setItem(key, open ? '1' : '0');
    } catch {}
  }
</script>

<section class="mt-1">
  <button
    class="flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-xs font-semibold uppercase tracking-wider text-neutral-500 hover:bg-neutral-800/60 hover:text-neutral-300"
    aria-expanded={open}
    onclick={toggle}
  >
    <span class="inline-block w-2 text-[10px] transition-transform {open ? 'rotate-90' : ''}">▶</span>
    <span class="truncate">{title}</span>
    {#if active && !open}
      <span class="ml-auto rounded-full bg-sky-700 px-1.5 text-[10px] font-medium normal-case tracking-normal text-white" title="{active} active">{active}</span>
    {/if}
  </button>
  {#if open}
    <div class="pb-1.5 pt-0.5">{@render children()}</div>
  {/if}
</section>
