// View state, mirrored into the URL query string so views can be bookmarked.

// EXIF filters, keyed by their API query parameter. Arrays are OR-combined values.
const FILTER_ARRAYS = ['camera', 'lens', 'orientation', 'flag', 'country', 'region', 'place', 'loc_source', 'folder'];
const FILTER_SCALARS = ['date_from', 'date_to', 'focal_min', 'focal_max', 'aperture_min', 'aperture_max', 'iso_min', 'iso_max', 'gps', 'exported'];

export function emptyFilters() {
  return Object.fromEntries([...FILTER_ARRAYS.map((k) => [k, []]), ...FILTER_SCALARS.map((k) => [k, ''])]);
}

export function activeFilterCount(f) {
  return FILTER_ARRAYS.filter((k) => f[k].length).length + FILTER_SCALARS.filter((k) => f[k] !== '').length;
}

export const view = $state({
  q: '',
  similar: null, // photo id
  tags: [], // tag ids, AND-combined: photos must have all of them
  excludeTags: [], // tag ids: photos must have none of them
  filters: emptyFilters(),
  group: '', // '' | 'day' | 'month' | 'year' | 'place' | 'region' | 'country' (browsing only, not search)
  collapse: 'dupes', // 'dupes' | 'stacks': one tile per duplicate group or per stack
  sort: 'taken_at', // 'taken_at' | '-taken_at' | 'taste' (likely keepers first) | '-taste' (likely rejects first)
  overview: false, // calendar (date grouping) or map (location grouping) instead of the grid
  compare: null, // open compare view: {kind: 'stack', id} | {kind: 'ids', ids} | {kind: 'review'} (not in the URL)
  exporting: false, // export dialog open: true (picks) | {ids, fresh} (a Curate draft) (not in the URL)
  curate: false, // Curate view open (not in the URL)
  help: false, // how-to guide open (not in the URL)
  photo: null, // open detail photo id
  raws: false, // unmatched RAWs list open
  library: false, // folders / indexing dialog open (not mirrored to the URL)
});

export function readUrl() {
  const p = new URLSearchParams(location.search);
  view.q = p.get('q') ?? '';
  view.similar = p.has('similar') ? Number(p.get('similar')) : null;
  view.tags = (p.get('tags') ?? '').split(',').filter(Boolean).map(Number);
  view.excludeTags = (p.get('xtags') ?? '').split(',').filter(Boolean).map(Number);
  view.photo = p.has('photo') ? Number(p.get('photo')) : null;
  view.raws = p.has('raws');
  view.group = GROUPS.includes(p.get('group')) ? p.get('group') : '';
  view.collapse = p.get('collapse') === 'stacks' ? 'stacks' : 'dupes';
  view.sort = SORTS.includes(p.get('sort')) ? p.get('sort') : 'taken_at';
  view.overview = p.has('overview') && GROUPS.includes(view.group);
  const f = emptyFilters();
  for (const k of FILTER_ARRAYS) f[k] = p.getAll(k);
  for (const k of FILTER_SCALARS) f[k] = p.get(k) ?? '';
  view.filters = f;
}

export function urlFor(v) {
  const p = new URLSearchParams();
  if (v.q) p.set('q', v.q);
  if (v.similar) p.set('similar', v.similar);
  if (v.tags.length) p.set('tags', v.tags.join(','));
  if (v.excludeTags.length) p.set('xtags', v.excludeTags.join(','));
  for (const k of FILTER_ARRAYS) for (const x of v.filters[k]) p.append(k, x);
  for (const k of FILTER_SCALARS) if (v.filters[k] !== '') p.set(k, v.filters[k]);
  if (v.group) p.set('group', v.group);
  if (v.collapse === 'stacks') p.set('collapse', 'stacks');
  if (v.sort !== 'taken_at') p.set('sort', v.sort);
  if (v.overview) p.set('overview', '');
  if (v.photo) p.set('photo', v.photo);
  if (v.raws) p.set('raws', '');
  const s = p.toString();
  return s ? `?${s}` : location.pathname;
}

// ---- date groups -----------------------------------------------------------

export const SORTS = ['taken_at', '-taken_at', 'name', '-name', 'place', '-place', 'taste', '-taste'];
export const isTasteSort = (sort) => sort === 'taste' || sort === '-taste';

/** Whether a sort works with a grouping: date sorts always; place names with a
 * location grouping and file names with folders (they order the groups by name). */
export function sortFits(group, sort) {
  if (!group || sort === 'taken_at' || sort === '-taken_at') return true;
  if (LOCATION_GROUPS.includes(group)) return sort === 'place' || sort === '-place';
  return group === 'folder' && (sort === 'name' || sort === '-name');
}

export const DATE_GROUPS = ['day', 'month', 'year'];
export const LOCATION_GROUPS = ['place', 'region', 'country'];
export const GROUPS = [...DATE_GROUPS, ...LOCATION_GROUPS, 'folder'];
/** Groupings with an overview: a calendar for dates, a map for locations (none for folders). */
export const hasOverview = (mode) => DATE_GROUPS.includes(mode) || LOCATION_GROUPS.includes(mode);
export const isLocationGroup = (mode) => LOCATION_GROUPS.includes(mode);

/** The key the API uses to group a photo (detail object) by date or location. */
export function groupKey(photo, mode) {
  if (isLocationGroup(mode)) return photo.location?.[`${mode}_key`] ?? '';
  const takenAt = photo.taken_at;
  if (!takenAt) return '';
  const day = takenAt.slice(0, 10).replaceAll(':', '-');
  return { day, month: day.slice(0, 7), year: day.slice(0, 4) }[mode];
}

/** A group's heading. Location groups carry their label from the server (group.label). */
export function groupLabel(key, mode, label = null) {
  if (isLocationGroup(mode)) return label || (key ? key : 'Unknown location');
  if (mode === 'folder') return label || key || 'Unknown folder';
  if (!key) return 'Undated';
  const [y, m = 1, d = 1] = key.split('-').map(Number);
  const date = new Date(y, m - 1, d);
  if (mode === 'day') return date.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'long', year: 'numeric' });
  if (mode === 'month') return date.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
  return String(y);
}

/** "12–14 Mar 2026" for a location group's first and last capture dates (EXIF format). */
export function dateSpan(first, last) {
  const parse = (s) => (s ? new Date(s.slice(0, 10).replaceAll(':', '-') + 'T00:00') : null);
  const a = parse(first), b = parse(last);
  if (!a) return '';
  const full = { day: 'numeric', month: 'short', year: 'numeric' };
  if (!b || a.toDateString() === b.toDateString()) return a.toLocaleDateString(undefined, full);
  const sameYear = a.getFullYear() === b.getFullYear();
  const start = a.toLocaleDateString(undefined, sameYear ? { day: 'numeric', month: 'short' } : full);
  return `${start} – ${b.toLocaleDateString(undefined, full)}`;
}

export function filterToGroup(key, mode) {
  if (!key) return;
  if (mode === 'folder') {
    view.filters.folder = [key];
    return;
  }
  if (isLocationGroup(mode)) {
    view.filters[mode] = [key];
    return;
  }
  const [y, m] = key.split('-').map(Number);
  const pad = (n) => String(n).padStart(2, '0');
  const range = {
    day: [key, key],
    month: [`${key}-01`, `${key}-${pad(new Date(y, m, 0).getDate())}`],
    year: [`${key}-01-01`, `${key}-12-31`],
  }[mode];
  view.filters.date_from = range[0];
  view.filters.date_to = range[1];
}

export function toggleFilterValue(key, value) {
  const list = view.filters[key];
  view.filters[key] = list.includes(value) ? list.filter((x) => x !== value) : [...list, value];
}

export function clearFilters() {
  view.filters = emptyFilters();
}

/** The H shortcut: hide rejected photos, i.e. Flag → Picked + Unflagged (and back). */
export function hidingRejected() {
  const f = view.filters.flag;
  return f.length === 2 && f.includes('pick') && f.includes('none');
}
export function toggleHideRejected() {
  view.filters.flag = hidingRejected() ? [] : ['pick', 'none'];
}

export function toggleTag(id) {
  view.excludeTags = view.excludeTags.filter((t) => t !== id);
  view.tags = view.tags.includes(id) ? view.tags.filter((t) => t !== id) : [...view.tags, id];
}

/** Toggle excluding a tag (photos with it are hidden); replaces including it. */
export function toggleExcludeTag(id) {
  view.tags = view.tags.filter((t) => t !== id);
  view.excludeTags = view.excludeTags.includes(id) ? view.excludeTags.filter((t) => t !== id) : [...view.excludeTags, id];
}

/** How many things narrow the view: search, each included/excluded tag, each filter. */
export function activeCount() {
  return (view.q || view.similar ? 1 : 0) + view.tags.length + view.excludeTags.length + activeFilterCount(view.filters);
}

/** Back to the whole library: no search, no tags, no filters. */
export function clearAll() {
  clearSearch();
  view.tags = [];
  view.excludeTags = [];
  clearFilters();
}

/** Any tag included or excluded. */
export function tagFilterActive() {
  return view.tags.length > 0 || view.excludeTags.length > 0;
}

export function search(q) {
  view.q = q.trim();
  view.similar = null;
}

export function findSimilar(id) {
  view.similar = id;
  view.q = '';
  view.photo = null;
}

export function clearSearch() {
  view.q = '';
  view.similar = null;
}
