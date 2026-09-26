function query(params) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (Array.isArray(v)) v.forEach((x) => p.append(k, x)); // repeated parameter
    else if (v !== undefined && v !== null && v !== '') p.set(k, v);
  }
  return p.toString();
}

/** The filter part of the view (tags + EXIF filters) as query parameters. */
const filterParams = (view) => ({ tags: view.tags.join(','), exclude_tags: view.excludeTags.join(','), ...view.filters });

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
  if (view.q) return get('/api/search/text', { ...common, q: view.q });
  // Grouped views keep their own order (date, trip, or folder); only date sorts apply there.
  const sort = view.group && view.sort !== 'taken_at' && view.sort !== '-taken_at' ? 'taken_at' : view.sort;
  return get('/api/photos', { ...common, group: view.group, sort });
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

export const fetchSources = () => get('/api/sources');
export const addSource = (path) => send('POST', '/api/sources', { path });
export const removeSource = (path) => send('DELETE', '/api/sources', { path });
export const browse = (path, files = null) => get('/api/fs', { path, files });
export const fetchIndexStatus = () => get('/api/index');
export const startIndex = () => send('POST', '/api/index');
