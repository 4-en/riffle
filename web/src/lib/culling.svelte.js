// Pick/reject flags on the client: one place that changes them, remembers what
// was there before (for undo), and overlays the latest values on loaded items.

import { SvelteMap, SvelteSet } from 'svelte/reactivity';
import { postFlags, postResetFlags } from './api.js';

/** id -> 'pick' | 'reject' | null, for flags changed since items were loaded. */
export const flagOverlay = new SvelteMap();

export const culling = $state({
  version: 0, // bumps after every change, so views can refresh counts
  error: '',
  autoAdvance: loadSetting('autoAdvance', true),
});

/** The grid selection (photo ids) plus focus and range anchor. */
export const selection = new SvelteSet();
export const cursor = $state({ focus: null, anchor: null });

const undoStack = [];

export function flagOf(item) {
  return flagOverlay.has(item.id) ? flagOverlay.get(item.id) : (item.flag ?? null);
}

/** Apply [{ids, flag}] ops; remembers the previous values for undo. */
export async function applyFlags(ops, { remember = true } = {}) {
  ops = ops.filter((op) => op.ids.length);
  if (!ops.length) return;
  // Optimistic: show the change at once, roll back if the server refuses.
  const before = new Map();
  for (const op of ops) for (const id of op.ids) if (!before.has(id)) before.set(id, flagOverlay.get(id));
  for (const op of ops) for (const id of op.ids) flagOverlay.set(id, op.flag);
  try {
    const { previous } = await postFlags(ops);
    if (remember) undoStack.push(previous);
    if (undoStack.length > 200) undoStack.shift();
    culling.error = '';
  } catch (e) {
    for (const [id, v] of before) (v === undefined ? flagOverlay.delete(id) : flagOverlay.set(id, v));
    culling.error = e.message;
  }
  culling.version++;
}

export const setFlag = (ids, flag) => applyFlags([{ ids: [...ids], flag }]);

/** Unflag everything, or everything within the current filters; undoable. Returns the count. */
export async function resetFlags(view, scope) {
  try {
    const { previous } = await postResetFlags(view, scope);
    for (const id of Object.keys(previous)) flagOverlay.set(Number(id), null);
    if (Object.keys(previous).length) undoStack.push(previous);
    culling.error = '';
    culling.version++;
    return Object.keys(previous).length;
  } catch (e) {
    culling.error = e.message;
    throw e;
  }
}

/** Revert the most recent flag change. */
export async function undo() {
  const previous = undoStack.pop();
  if (!previous) return false;
  const byFlag = new Map();
  for (const [id, flag] of Object.entries(previous)) {
    if (!byFlag.has(flag)) byFlag.set(flag, []);
    byFlag.get(flag).push(Number(id));
  }
  await applyFlags([...byFlag].map(([flag, ids]) => ({ ids, flag })), { remember: false });
  return true;
}

export function clearSelection() {
  selection.clear();
  cursor.anchor = null;
}

function loadSetting(key, fallback) {
  try {
    // "archive." was the prefix before the rename to Riffle.
    const v = localStorage.getItem(`riffle.${key}`) ?? localStorage.getItem(`archive.${key}`);
    return v === null ? fallback : JSON.parse(v);
  } catch {
    return fallback;
  }
}

export function saveSetting(key, value) {
  try {
    localStorage.setItem(`riffle.${key}`, JSON.stringify(value));
  } catch {}
}
