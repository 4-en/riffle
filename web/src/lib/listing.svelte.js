// The grid's listing, loaded in pages on demand. The first page gives the total and
// (when grouped) every group with its count, so the whole layout is known up front;
// the grid then asks for the pages that come into view, in any order. A jump is one
// scroll plus one page instead of loading everything above the target.

import { SvelteMap } from 'svelte/reactivity';
import { view } from './state.svelte.js';
import { fetchResults } from './api.js';
import { flagOverlay } from './culling.svelte.js';
import { hiddenBefore, isHidden, offsetAt } from './listing-layout.js';

export const PAGE = 120;
const PARALLEL = 4; // page requests at once

export class Listing {
  total = $state(0);
  groups = $state(null); // [{key, count, start, …}] in listing order, or null (ungrouped)
  loading = $state(false); // any page request running
  loaded = $state(false); // the current query's first page has arrived
  error = $state('');
  pages = new SvelteMap(); // page number -> items
  #offsets = new SvelteMap(); // photo id -> offset (loaded photos)
  #inflight = new Map(); // page number -> promise
  #token = 0;
  #active = 0;
  #waiting = [];

  /** Offsets of loaded photos whose flag changed so they no longer match the flag
   * filter: they disappear at once, before the listing is queried again. */
  hidden = $derived.by(() => {
    const wanted = view.filters.flag;
    if (!wanted.length) return [];
    const out = [];
    for (const [id, flag] of flagOverlay) {
      const o = this.#offsets.get(id);
      if (o !== undefined && !wanted.includes(flag ?? 'none')) out.push(o);
    }
    return out.sort((a, b) => a - b);
  });

  /** Sections for the layout: [{key, count, vstart, total}] with visible counts. */
  sections = $derived.by(() => {
    const hidden = this.hidden;
    if (!this.groups) return this.loaded ? [{ key: null, count: this.total - hidden.length, vstart: 0, total: this.total }] : [];
    let vstart = 0;
    return this.groups.map((g) => {
      const count = g.count - (hiddenBefore(hidden, g.start + g.count) - hiddenBefore(hidden, g.start));
      const s = { key: g.key, count, vstart, total: g.count };
      vstart += count;
      return s;
    });
  });

  /** A new query: forget everything and load the first page. */
  async reset() {
    const mine = ++this.#token;
    this.pages.clear();
    this.#offsets.clear();
    this.#inflight.clear();
    this.total = 0;
    this.groups = null;
    this.loaded = false;
    this.error = '';
    await this.#load(0, mine);
  }

  /** Load the pages covering offsets from..to (inclusive). */
  ensure(from, to = from) {
    const last = this.loaded ? this.total - 1 : to;
    const jobs = [];
    for (let p = Math.floor(Math.max(0, from) / PAGE); p <= Math.floor(Math.min(to, last) / PAGE); p++) {
      if (this.pages.has(p)) continue;
      jobs.push(this.#inflight.get(p) ?? this.#load(p, this.#token));
    }
    return Promise.all(jobs);
  }

  #load(page, mine) {
    this.loading = true;
    const job = (async () => {
      await this.#slot();
      try {
        if (mine !== this.#token) return;
        const res = await fetchResults(view, page * PAGE, PAGE);
        if (mine !== this.#token) return;
        this.total = res.total;
        if (!this.loaded) {
          let start = 0;
          this.groups = res.groups ? res.groups.map((g) => ({ ...g, start: (start += g.count) - g.count })) : null;
        }
        res.items.forEach((item, i) => this.#offsets.set(item.id, page * PAGE + i));
        this.pages.set(page, res.items);
        this.loaded = true;
        this.error = '';
      } catch (e) {
        if (mine === this.#token) this.error = e.message;
      } finally {
        this.#release();
        if (mine === this.#token) {
          this.#inflight.delete(page);
          this.loading = this.#inflight.size > 0;
        }
      }
    })();
    this.#inflight.set(page, job);
    return job;
  }

  async #slot() {
    if (this.#active >= PARALLEL) await new Promise((r) => this.#waiting.push(r));
    this.#active++;
  }
  #release() {
    this.#active--;
    this.#waiting.shift()?.();
  }

  // ---- lookups -------------------------------------------------------------------

  itemAt(offset) {
    return this.pages.get(Math.floor(offset / PAGE))?.[offset % PAGE];
  }
  offsetOf(id) {
    return this.#offsets.get(id);
  }
  byId(id) {
    const o = this.#offsets.get(id);
    return o === undefined ? undefined : this.itemAt(o);
  }
  /** The photo at an offset, loading its page if needed. */
  async load(offset) {
    await this.ensure(offset);
    return this.itemAt(offset);
  }
  group(key) {
    return this.groups?.find((g) => g.key === key);
  }

  get visibleTotal() {
    return this.total - this.hidden.length;
  }
  /** Visible position <-> listing offset. */
  visibleIndex(offset) {
    return offset - hiddenBefore(this.hidden, offset);
  }
  offsetAt(v) {
    return offsetAt(this.hidden, v);
  }
  /** The next shown offset from `offset` in direction dir (±1), or -1. */
  nextVisible(offset, dir) {
    let o = offset + dir;
    while (o >= 0 && o < this.total && isHidden(this.hidden, o)) o += dir;
    return o >= 0 && o < this.total ? o : -1;
  }

  /** Ids of the shown photos from offset a to b (either order), loading what is missing. */
  async idsBetween(a, b) {
    const [lo, hi] = a <= b ? [a, b] : [b, a];
    await this.ensure(lo, hi);
    const ids = [];
    for (let o = lo; o <= hi; o++) {
      const item = this.itemAt(o);
      if (item && !isHidden(this.hidden, o)) ids.push(item.id);
    }
    return ids;
  }
}
