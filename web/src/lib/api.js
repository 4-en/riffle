import { sortFits, prefs } from './state.svelte.js';

function query(params) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (Array.isArray(v)) v.forEach((x) => p.append(k, x)); // repeated parameter
    else if (v !== undefined && v !== null && v !== '') p.set(k, v);
  }
  return p.toString();
}

/** The filter part of the view (tags + EXIF filters) as query parameters. */
const filterParams = (view) => ({
  tags: view.tags.join(','),
  exclude_tags: view.excludeTags.join(','),
  ctags: view.ctags.join(','),
  exclude_ctags: view.excludeCtags.join(','),
  ...view.filters,
});

export async function get(path, params = {}) {
  const qs = query(params);
  const res = await fetch(qs ? `${path}?${qs}` : path);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {}
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json();
}

/** One page of results for the current view: similar search, text search, or plain listing. */
export function fetchResults(view, offset, limit) {
  const common = { ...filterParams(view), collapse: view.collapse, offset, limit };
  if (view.similar) return get(`/api/search/similar/${view.similar}`, common);
  if (view.q) return get('/api/search/text', { ...common, q: view.q, names: prefs.nameMatch ? undefined : 'false' });
  // Grouped views allow only the sorts that keep groups together (date, or the groups' names).
  const sort = sortFits(view.group, view.sort) ? view.sort : 'taken_at';
  return get('/api/photos', { ...common, group: view.group, level: view.group === 'similar' ? view.level : undefined, sort });
}

/** Tags with counts within the current filter; tags no matching photo carries are omitted. */
export const fetchTags = (view) => get('/api/tags', filterParams(view));
/** EXIF filter options, each within the other active filters. */
export const fetchFacets = (view) => get('/api/facets', filterParams(view));
export const fetchPhoto = (id) => get(`/api/photos/${id}`);
export const fetchUnmatchedRaws = () => get('/api/raws/unmatched');

async function send(method, path, body = {}) {
  const res = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ?? `${res.status}: ${res.statusText}`);
  return data;
}

/** Just the groups (count, dates, cover; location: label and centre) for an overview. */
export const fetchGroups = (view, group) => get('/api/groups', { ...filterParams(view), collapse: view.collapse, group });
/** The Similar map: the view's photos on the library's 2D layout, with their clusters. */
/** Discover: branches from a photo. opts: {trail, came_from, prefs, drift, scoped, rejects, seed}. */
export const fetchDiscover = (view, id, { trail = [], came_from = null, prefs = {}, drift = {}, scoped = false, rejects = false, seed = null }) =>
  get(`/api/discover/${id}`, {
    ...(scoped ? { ...filterParams(view), scoped: 'true' } : {}),
    trail: trail.join(','),
    came_from,
    prefs: JSON.stringify(prefs),
    drift: JSON.stringify(drift),
    rejects: rejects ? 'true' : undefined,
    seed,
  });
export const fetchSimilarMap = (view) =>
  get('/api/similar/map', { ...filterParams(view), collapse: view.collapse, level: view.level });
export const fetchIds = (view) => get('/api/ids', { ...filterParams(view), collapse: view.collapse });
export const fetchStacks = (view, unreviewed = true) => get('/api/stacks', { ...filterParams(view), unreviewed });
export const fetchStack = (id) => get(`/api/stacks/${id}`);
/** Suggested keeper among photos, with its sharpness / exposure / quality scores. */
export const fetchSuggestion = (ids) => get('/api/suggest', { ids: ids.join(',') });
/** ops: [{ids, flag}] with flag 'pick' | 'reject' | null. Resolves to {previous: {id: flag}}. */
export const postFlags = (ops) => send('POST', '/api/flags', { ops });
/** Export picks; with body.scope === 'filtered' the current filters narrow them. */
export function startExport(view, body) {
  const qs = query(filterParams(view));
  return send('POST', qs ? `/api/export?${qs}` : '/api/export', body);
}
export const fetchExportStatus = () => get('/api/export');
/** Forget which photos were exported: all, or those within the current filters. */
export function postResetExported(view, scope) {
  const qs = scope === 'filtered' ? query(filterParams(view)) : '';
  return send('POST', qs ? `/api/exported/reset?${qs}` : '/api/exported/reset', { scope });
}
/** Picks within a scope, split into exported before / not: {yes, no}. */
export async function fetchPickExportCounts(view, scope) {
  const base = scope === 'filtered' ? filterParams(view) : {};
  return (await get('/api/facets', { ...base, flag: ['pick'] })).exported;
}
/** Unflag all photos (scope 'all') or those within the current filters ('filtered'). */
export function postResetFlags(view, scope) {
  const qs = scope === 'filtered' ? query(filterParams(view)) : '';
  return send('POST', qs ? `/api/flags/reset?${qs}` : '/api/flags/reset', { scope });
}

export const fetchLocationHistory = () => get('/api/location-history');
export const addLocationHistory = (path) => send('POST', '/api/location-history', { path });
export const removeLocationHistory = (path) => send('DELETE', '/api/location-history', { path });

/** The taste model's status: {enabled, reason, keeper_scenes, reject_scenes, auc, top20_recall, training}. */
export const fetchTaste = () => get('/api/taste');
/** Learn from the current flags now; resolves to the new status. */
export const calibrateTaste = () => send('POST', '/api/taste/calibrate');

/** Curate: the styles for the sliders, a draft for the current filters, and alternatives for one slot. */
export const fetchStyles = () => get('/api/styles');
export const fetchHues = () => get('/api/hues');
export function fetchCurate(view, body) {
  const qs = query(filterParams(view));
  return send('POST', qs ? `/api/curate?${qs}` : '/api/curate', body);
}
export function fetchAlternatives(view, body) {
  const qs = query(filterParams(view));
  return send('POST', qs ? `/api/curate/alternatives?${qs}` : '/api/curate/alternatives', body);
}

/** Custom tags (taught by example photos). */
export const createCustomTag = (name, photoIds, strictness) =>
  send('POST', '/api/custom-tags', { name, photo_ids: photoIds, strictness });
export const editCustomTag = (id, changes) => send('POST', `/api/custom-tags/${id}`, changes);
export const deleteCustomTag = (id) => send('DELETE', `/api/custom-tags/${id}`);
/** {counts: {strict, normal, loose}, edge: items, examples} for these example photos. */
export const previewCustomTag = (photoIds, strictness) =>
  send('POST', '/api/custom-tags/preview', { photo_ids: photoIds, strictness });

/** Captions and fixed tags: {id: {caption, method, edited, tags}}. */
export async function fetchCaptions(ids) {
  const out = {};
  for (let i = 0; i < ids.length; i += 200) Object.assign(out, (await get('/api/captions', { ids: ids.slice(i, i + 200).join(',') })).captions);
  return out;
}
/** items: [{id, caption?, tags?}]; resolves to {previous: [...]} for undo. */
export const saveCaptions = (items) => send('POST', '/api/captions', { items });
/** op: add | remove | rename; at: 'end' | 'start' (add); resolves to {previous: [...]} for undo. */
export const bulkTags = (ids, op, tag, to = null, at = 'end') => send('POST', '/api/captions/tags', { ids, op, tag, to, at });
/** {methods: [{key, label, group, outputs, available, reason, download}], job}. */
export const fetchCaptioning = () => get('/api/captioning');
export const startCaptioning = (body) => send('POST', '/api/captioning', body);
export const cancelCaptioning = () => send('POST', '/api/captioning/cancel');
export const fetchTaglists = () => get('/api/taglists');
export const downloadDanbooru = () => send('POST', '/api/taglists/danbooru');

/** Profiles: switchable sets of flags, export history, and custom tags. */
export const fetchProfiles = () => get('/api/profiles');
export const createProfile = (name, copyFrom = null, parts = null) =>
  send('POST', '/api/profiles', { name, copy_from: copyFrom, parts });
export const activateProfile = (slug) => send('POST', `/api/profiles/${slug}/activate`);
export const renameProfile = (slug, name) => send('POST', `/api/profiles/${slug}`, { name });
export const deleteProfile = (slug) => send('DELETE', `/api/profiles/${slug}`);

export const fetchSources = () => get('/api/sources');
export const addSource = (path) => send('POST', '/api/sources', { path });
export const removeSource = (path) => send('DELETE', '/api/sources', { path });
export const browse = (path, files = null) => get('/api/fs', { path, files });
export const fetchIndexStatus = () => get('/api/index');
export const startIndex = () => send('POST', '/api/index');
