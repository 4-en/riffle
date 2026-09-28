# Riffle: Development Plan

Design notes, measurements, and open work. User-facing documentation is in the README.

Started 25 September 2026 as "Computational Photo Archive"; renamed to Riffle on 26 September 2026.

**Status:** MVP, culling and export, location history, taste model, Curate, and standalone releases are implemented. The search and tag evaluation (§14) is still open.

## 1. Goal

Riffle is for **making a selection** from a large photo library (several thousand photos from trips): find the good ones, pick or reject quickly, and export the picks for editing, sharing, or printing.

It makes the library searchable by image content (CLIP embeddings, zero-shot tags) without training, labelled data, or manual annotation. The first question was whether CLIP search and tagging make an archive noticeably easier to explore. The culling workflow (§8) is built on that.

## 2. Principles

1. **Originals are read-only.** Nothing is renamed, moved, edited, or written next to a source file.
2. **Derived data is disposable.** Thumbnails, embeddings, tags, and positions can be deleted and rebuilt from the originals and the config. The only user-created data (pick/reject flags, export history, your tags) lives separately in `selections.sqlite3` (§6.2).
3. **Local only.** No network calls except downloading model weights once. The map uses bundled outlines, not a tile server.
4. **No data gathering.** Everything comes from a pretrained CLIP model, a hand-written vocabulary, and the user's own flags. The taste model (§9) trains only on those flags, locally.
5. **Few moving parts.** The standard library plus a few well-known packages; no frameworks where plain code does.

Out of scope for now: RAW decoding, captions and OCR, object detection, embedding maps, manual tagging, named collections, star ratings, multi-user or remote access. Several of these are candidates in §15.

## 3. Architecture

```mermaid
flowchart LR
    A[Photo folders, read-only] --> B[Indexer]
    B --> C[(SQLite catalogue)]
    B --> D[Thumbnails and previews]
    B --> E[Embeddings .npy]
    C --> F[FastAPI server]
    D --> F
    E --> F
    S[(selections.sqlite3)] --> F
    F --> G[Svelte UI]
    F -. background thread .-> B
```

- **Indexer** (`index.py`): scan, metadata, thumbnails, embeddings, tags, duplicates, quality and colour measures, stacks, locations. Incremental and restartable. Runs from the CLI (`riffle index`) or in a background thread of the server; both use the same pipeline and report progress through callbacks.
- **Server** (`server.py`, FastAPI): serves the JSON API, thumbnails, previews, and the built UI on one port. It keeps the embedding matrix in memory and reloads it when the files change, so re-indexing needs no restart. The CLIP text encoder loads in a background thread; until it is ready (on the first start that includes the download), browsing works and search reports that the model is loading.
- **UI** (`web/`, Svelte 5 + Tailwind 4, built with Vite, no SvelteKit): a single-page app. `npm run build` writes it into the Python package (`src/riffle/web/`), so every install includes it.
- **Launcher** (`launch.py`): `riffle` without arguments starts the server on 127.0.0.1 (port 8000, or a free port), waits for `/api/health`, and opens the browser. A `server.json` in the cache folder records the running instance; a second launch that finds it answering for the same config only opens a tab.

There is no vector database: search is one matrix–vector product over the in-memory matrix.

### Stopping with the last tab

The one-click launch stops once no tab is open. Each tab keeps a Server-Sent Events stream (`/api/events`); a watchdog stops the server 30 s after the last one closes, or after 5 minutes if none ever connected. It never stops within the first minute, or while indexing or exporting. `riffle serve` never stops by itself.

Why an open connection:

- `beforeunload` can't tell a close from a reload, and misses crashes.
- Timer heartbeats are throttled in background tabs.
- An open connection has neither problem.

With ASGI 2.4, Starlette only notices a closed client when a write fails, so the stream writes a keep-alive every 2 s. The same stream carries the model status (`loading`, `ready`, `failed`) to the page.

One Ctrl+C stops the server cleanly: the event streams end first, so the graceful shutdown doesn't wait for open tabs.

## 4. Technology

| Area | Choice | Notes |
|---|---|---|
| Language | Python 3.12+ | |
| Packaging | `pyproject.toml` + pip | Editable install into a local `venv/` |
| Images | Pillow (+ `pillow-heif` for HEIC) | EXIF, orientation, ICC → sRGB |
| Perceptual hash | `imagehash` (pHash) | Duplicates |
| Embeddings | `open_clip_torch` | Model configurable |
| Maths | NumPy, SciPy, scikit-learn | Similarity; the taste model's optimiser; clustering (SciPy) and the Similar map's t-SNE (scikit-learn) |
| Database | `sqlite3` (stdlib) | Plain SQL; schema version in `PRAGMA user_version` |
| Server | FastAPI + Uvicorn | |
| Places | `reverse_geocode` | Offline GeoNames lookup |
| Paths | `platformdirs` | Per-platform config, data, and cache folders |
| Frontend | Svelte 5, Tailwind 4, Vite | d3-geo / d3-zoom and `world-atlas` for the map |
| Tests | pytest, httpx | Synthetic images, fake encoder |
| Releases | PyInstaller, GitHub Actions | §13 |

Deliberately not used: SQLAlchemy, Alembic, FAISS, OpenCV, pyvips, ExifTool, a component library.

## 5. Files and configuration

Per-user files follow platform conventions (`paths.py`; Linux shown, XDG variables honoured; `riffle paths` prints them):

```text
~/.config/riffle/
  config.yaml            # sources, model, thresholds (created from src/riffle/defaults/)
  vocabulary.yaml        # tags and Curate styles
~/.local/share/riffle/
  selections.sqlite3     # flags, export history, your tags: user data (§6.2)
~/.cache/riffle/         # derived, safe to delete (config: data_dir)
  catalogue.sqlite3
  thumbs/                # 320 px long edge
  previews/              # 1600 px long edge
  embeddings/<model_id>.npy, <model_id>.ids.npy, <model_id>.taste.npz
  server.json            # the running instance (one-click launch)
  riffle.log             # standalone builds only
```

macOS uses `~/Library/Application Support/riffle` and `~/Library/Caches/riffle`. Windows uses `%APPDATA%\riffle` (settings and flags, which roam with the profile) and `%LOCALAPPDATA%\riffle\Cache`.

The config is looked up in this order: `--config`, `$RIFFLE_CONFIG`, `./config.yaml`, then the user config (created on first run). In the standalone app, the first-run config defaults to the faster model (§13). `data_dir`, `selections`, and `vocabulary` override the default locations. Relative paths resolve against the config file's folder.

```yaml
sources: [/Volumes/Photos/China]        # written by Settings → Photo folders; comments elsewhere are kept
exclude: ["**/.Trashes/**"]
image_extensions: [.jpg, .jpeg, .png, .tif, .tiff, .heic]
raw_extensions: [.cr2, .cr3, .nef, .arw, .raf, .dng, .orf, .rw2]
raw_search_dirs: [".", "RAW", "../RAW"]  # relative to each image's folder

model:
  name: ViT-L-14-quickgelu
  pretrained: dfn2b
  device: auto          # cuda, mps, or cpu
  batch_size: 32

tags:
  softmax_scale: 100
  subject: { min_prob: 0.15, max_tags: 3 }
  scene:   { min_prob: 0.30, max_tags: 1 }
  look:    { min_prob: 0.30, max_tags: 2 }   # a family without thresholds: 0.2 / 1

dupes:
  phash_max_distance: 8

stacks:
  max_gap_seconds: 30
  min_similarity: 0.92  # model-dependent (§7.7)

location:
  max_gap_minutes: 30
  min_population: 0
location_history: [~/Documents/Timeline.json]
```

The model id is `<name>__<pretrained>`. Each model gets its own embedding file and tags, so models can be compared side by side.

Source layout:

```text
src/riffle/
  cli.py, __main__.py    # riffle [index | tag | serve | paths]
  launch.py              # one-click launch
  frozen.py              # standalone-app setup: log file, error dialogs
  paths.py, config.py    # locations; config loading and the sources rewrite
  db.py                  # catalogue schema and migrations
  scan.py, images.py     # walking, hashing, EXIF; shared image loading
  raws.py, thumbs.py
  embed.py, tags.py      # CLIP; zero-shot tags
  dupes.py               # pHash, and the per-preview measures (sharpness, clipping, colour)
  quality.py, colors.py  # sharpness, clipping, suggested keeper; colour and light
  stacks.py
  timeline.py, locate.py, geotag.py   # location history, placing photos, GPS in exports
  selections.py          # flags and export history
  filters.py             # shared filters and facets
  query.py               # search syntax
  custom_tags.py         # your tags: membership from example photos
  taste.py               # taste model
  curate.py              # Curate
  export.py, jobs.py     # export; background jobs
  server.py
web/src/
  App.svelte
  lib/                   # api.js, state.svelte.js (view state ↔ URL), culling.svelte.js
                         # (flags, undo, selection), connection.svelte.js, justify.js
  components/            # TopBar, ViewBar, Sidebar, Filters, Section, Grid, Detail,
                         # ContextMenu, SelectionBar, Compare, Curate, TagDialog, ExportDialog,
                         # Settings (+ settings/ pages), FolderBrowser, Calendar, MapView, Help, UnmatchedRaws
packaging/               # PyInstaller entry point (riffle_app.py) and riffle.spec
.github/workflows/release.yml
```

## 6. Data model

### 6.1 Catalogue

Everything in `catalogue.sqlite3` can be rebuilt from the originals plus the config.

```sql
CREATE TABLE photos (
  id          INTEGER PRIMARY KEY,
  rel_path    TEXT NOT NULL,             -- relative to its source root
  source      TEXT NOT NULL,             -- the configured source folder
  sha256      TEXT NOT NULL,
  size_bytes  INTEGER, mtime REAL,
  width INTEGER, height INTEGER,         -- as displayed (after EXIF orientation)
  taken_at    TEXT,                      -- EXIF DateTimeOriginal, as written
  tz_offset   TEXT,                      -- EXIF OffsetTimeOriginal
  camera TEXT, lens TEXT, lat REAL, lon REAL,
  focal_length REAL, focal_length_35 REAL, aperture REAL, exposure_time REAL, iso INTEGER,
  meta_version INTEGER NOT NULL DEFAULT 0,   -- EXIF extraction version (§7.1)
  phash TEXT, dupe_group INTEGER,        -- dupe_group: lowest photo id in the group
  stack_id INTEGER,                      -- lowest photo id of the stack (§7.7)
  sharpness REAL, clip_highlights REAL, clip_shadows REAL,   -- §7.6
  brightness REAL, contrast REAL, colorfulness REAL, hues BLOB, -- §7.6
  status TEXT NOT NULL DEFAULT 'ok',     -- ok | error | missing
  error TEXT,
  UNIQUE (source, rel_path)
);
CREATE TABLE raws (id, photo_id, rel_path, source, size_bytes);  -- photo_id NULL = unmatched
CREATE TABLE tags (id, family, name);
CREATE TABLE photo_tags (photo_id, tag_id, prob, sim, model_id); -- prob: softmax in family
CREATE TABLE embedded (photo_id, model_id, sha256);              -- what each embedding file holds
CREATE TABLE photo_locations (         -- §7.8
  photo_id INTEGER PRIMARY KEY, lat REAL, lon REAL,
  source TEXT,                         -- exif | visit | route | nearby
  accuracy_m REAL, gap_s REAL, country_code TEXT, country TEXT, region TEXT, place TEXT
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);            -- step signatures
```

Schema changes are numbered migrations in `db.py`:

- v2: exposure columns
- v3: `stack_id`, `sharpness`
- v4: locations
- v5: clipping
- v6: colour

`SCHEMA_VERSION` is derived from the list of migrations, so adding one always bumps it. (A missing bump once left existing catalogues without the new columns while fresh ones got them; a test now migrates from every older version and compares the result with a fresh schema.)

Camera GPS stays in `photos.lat/lon`. `photo_locations` holds the result for every placed photo, tagged with its evidence, so camera positions and estimates stay distinguishable.

**Photo identity.** Photos are keyed by `(source, rel_path)`. On rescan:

- same path, same size and mtime → skipped;
- same path, changed content → derived data is recomputed;
- path gone but its hash appears elsewhere → a move, and the id is kept;
- otherwise → `missing`.

Removing a source marks its photos missing. Adding a parent of existing sources keeps their ids through move detection.

### 6.2 Selections (user data)

```sql
CREATE TABLE flags (
  sha256 TEXT PRIMARY KEY,            -- file content, not path or id
  flag TEXT NOT NULL CHECK (flag IN ('pick', 'reject')),
  source TEXT, rel_path TEXT,         -- last known location, for recovery
  updated_at REAL NOT NULL
);
CREATE TABLE exported (               -- v2
  sha256 TEXT PRIMARY KEY, first_at REAL, last_at REAL, times INTEGER,
  last_folder TEXT, source TEXT, rel_path TEXT
);
CREATE TABLE custom_tags (            -- v3: tags taught by example photos (§10)
  id INTEGER PRIMARY KEY, name TEXT UNIQUE COLLATE NOCASE,
  strictness TEXT,                    -- strict | normal | loose
  created_at REAL, updated_at REAL
);
CREATE TABLE custom_tag_examples (tag_id, sha256, source, rel_path, added_at);
CREATE TABLE captions (               -- v5: written captions (§11.2 Captions & tags)
  sha256 TEXT PRIMARY KEY, text TEXT, method TEXT, -- manual | riffle | phrases | joycaption | wd
  edited INTEGER,                     -- changed by hand: kept when generating again
  source TEXT, rel_path TEXT, updated_at REAL
);
CREATE TABLE fixed_tags (             -- v5: written tags, in order
  sha256, tag TEXT COLLATE NOCASE, position INTEGER, method TEXT,
  source, rel_path, added_at, PRIMARY KEY (sha256, tag)
);
CREATE TABLE photo_text (             -- v6: text read from the photo (OCR)
  sha256 TEXT PRIMARY KEY, text TEXT,  -- '' = read, none found
  translation TEXT, language TEXT, method TEXT, edited INTEGER,
  source TEXT, rel_path TEXT, updated_at REAL
);
```

- **Profiles** (`profiles.py`, selections v4 adds a `profile` name table): each profile is one selections file. The default is the configured file; others are `profiles/<slug>.sqlite3` next to it; the active one is remembered in `active_profile` there (per machine, not in `config.yaml`).
  - The server reads the active file through `sel_path()`.
  - On a switch it clears the custom-tag cache and loads that profile's taste model (`<model_id>.<slug>.taste.npz`; the default keeps `<model_id>.taste.npz`).
  - The SSE status carries the profile, so other tabs reload. The client drops custom tag filters (ids differ per profile) and reloads the page, which resets the flag overlay, undo history, and selection.
  - New profiles start empty or copy chosen tables (flags, exported, custom tags, captions and fixed tags) with `ATTACH` + `INSERT … SELECT`.
  - Switching is refused while indexing or an export runs, and an export records history in the file it started with.
  - Deleted profiles move to `profiles/deleted/`. Curate drafts are keyed per profile.
- Keyed by content hash, so flags survive deleting the cache, re-indexing, moving, and renaming. Exact copies share a flag; editing a file drops it.
- Unflagged photos have no row. Export history is independent of the flag.
- Catalogue connections `ATTACH` this file as `sel`, so filters and listings join flags in SQL (`selections.flag_expr`).
- Flags aren't written as XMP next to the originals (principle 1). XMP in the export folder is a candidate (§15).

## 7. Index pipeline

`riffle index` (or **Index now**) runs every step incrementally. Each step only processes photos that need it, and the CLIP model loads only when something needs embedding or tagging. Measured on the development machine (RTX 3090): 37 large 16-bit PNGs (1.5 GB) indexed in 7–17 s including model load; a no-change run finishes immediately.

### 7.1 Scan

1. Walk the sources, skipping excludes, `._*` files, and the cache folder.
2. Record path, size, mtime, and SHA-256.
3. Read EXIF with Pillow: capture time and offset, make/model, lens, GPS, orientation, focal length (and its 35 mm equivalent), aperture, exposure time, ISO.
4. Record unreadable files as `status = error` and continue.

When extraction gains fields (`META_VERSION`), unchanged files are re-read header-only; nothing else is recomputed. Capture times are stored as written by the camera.

### 7.2 RAW matching

A RAW is linked to the image with the same stem (case-insensitive) in its folder, or in one of `raw_search_dirs`. RAWs are never opened; only name and size are read. Only RAWs inside a configured source are found. Unmatched RAWs are listed separately.

### 7.3 Thumbnails and previews

EXIF orientation applied, converted to 8-bit sRGB (using the embedded ICC profile), saved as a 320 px thumbnail and a 1600 px preview, never upscaled. Runs in a process pool (spawn context; `freeze_support()` for the standalone app).

### 7.4 Embeddings

Previews are embedded in batches, L2-normalised, and written as `<model_id>.npy` with an aligned id array. Incremental runs embed new or changed photos only (tracked in `embedded`) and rewrite both files, which takes well under a second at this size.

### 7.5 Tags

Zero-shot, from `vocabulary.yaml` (§10):

1. Each phrase is encoded with every prompt template, and the results are averaged.
2. A tag scores by its best-matching phrase (max similarity).
3. A softmax over the family's tags (`softmax_scale`) turns scores into probabilities, so a tag's own phrases never compete.
4. Tags with `prob ≥ min_prob` are kept, up to `max_tags`.

`riffle tag` redoes this from stored embeddings in seconds (about 4 s for 1,150 photos).

Known limitations:

- Every photo gets its best tag in each family even when nothing fits well; catch-all tags (`other`, `ordinary daylight`) absorb some of this.
- Overlapping tags split probability, so the vocabulary uses non-overlapping main tags with specific phrases underneath.
- Visible text in a photo pulls tags towards what it says.
- Abstract concepts ("waiting", "tension") work poorly.
- Birds against the sky are sometimes tagged as aircraft.

### 7.6 Duplicates and per-preview measures

One pass over each preview (`dupes.compute_phashes`) computes whatever is missing. Adding a measure doesn't redo the others. It runs in a thread pool (up to 8 threads; decoding and most of the measuring release the GIL): 18.7 s for all 2,132 photos of the first library, 8.8 ms per photo. It used to take about 190 s, 69 % of it in clipping (below).

- **pHash**: all pairs are compared by Hamming distance (NumPy, in chunks), and pairs within `phash_max_distance` are merged into groups (union–find). The grid shows one tile per group. pHash misses reframed bursts; stacks catch them.
- **Sharpness** (`quality.py`): Laplacian variance per 50 px tile of an 800 px greyscale copy; the score is the mean of the sharpest 5 % of tiles.
  - Downsampling first keeps sensor noise from counting as detail.
  - Scoring the in-focus region keeps shallow depth of field from scoring low, which the whole-frame variance did on the first library.
  - Only shown relative to the other photos being compared.
- **Clipping**: the share of pixels with all channels ≥ 250 (blown) or ≤ 5 (crushed).
  - Counting any channel at 255 flagged saturated yellow flowers as a third blown; the all-channel rule scores them 0 % and still catches a blown window (16 %) or white sky (17 %).
  - Flagged in the photo and compare views above 2 % blown (8 % of the first library) or 5 % crushed (3 %; black backgrounds are often intentional).
  - It was a sidebar filter briefly; the API still accepts `exposure=`.
  - Computed with PIL (`ImageChops.darker` / `lighter` over the channels, then a histogram): identical results to NumPy's `min` / `max` over the channel axis, which took 60 ms per preview; now 3.5 ms.
- **Colour and light** (`colors.py`, on a 128 px copy), for Curate (§12):
  - brightness: mean luminance;
  - contrast: RMS;
  - colourfulness: Hasler & Süsstrunk;
  - hues: a 24-bin histogram weighted by chroma, so greys don't count. 12 bins mixed red, orange, and yellow, and put sky blue under teal; 24 separated them.
  - accents (`accents_of`, a JSON column, catalogue v9; also for Discover): up to three intense colours that need not cover much of the frame, the red balloon in a blue sky. Per hue (a 45° window), the pixels with chroma ≥ 0.25 and value ≥ 0.2: intensity is their 90th-percentile chroma, area their share of the frame. Strength = intensity, lowered below 1 % of the frame (specks) and above 10 % (down to a quarter at 30 %: a large area is palette, not accent). The hue covering the most of the frame (from 12 %) is the base colour and not an accent. Accents are at least 45° apart. 16 ms per preview; 33 s for 13,468 on 8 threads.
    - First library: the candle → orange, a small blue flower → blue, the desk with blue objects → blue, a plain sky → none. With the first rule (the base only when it held half the colour over a quarter of the frame), skies filling a fifth of the frame were most of the "blue accents"; the largest-area rule and the large-area discount removed most of them (a few sky patches between trees and clouds remain).

**Suggested keeper** (`quality.keeper_scores`, `GET /api/suggest`), among the photos being compared:

| Signal | Weight | Scale |
|---|---|---|
| Sharpness | 0.5 | relative to the sharpest |
| CLIP quality | 0.3 | CLIP-IQA prompt pairs "Good photo." / "Bad photo." and "Sharp photo." / "Blurry photo.", relative to the others |
| Exposure | 0.2 | 10 % blown or 25 % crushed scores 0 |

On a 17-shot flower stack, the top two were the ones in focus and the bottom three the soft ones. It is a hint (★, key A), never applied automatically.

### 7.7 Stacks

Photos in capture order are linked to the next when taken at most `max_gap_seconds` apart with CLIP cosine similarity ≥ `min_similarity`. These links are merged with duplicate groups (union–find); every connected set of two or more is a stack.

On the first library (743 bursty wildlife photos; 253 consecutive pairs within 2 s), 30 s / 0.90 with ViT-B-16 gave 141 stacks covering 510 photos. Also requiring similarity to the stack's first photo made no difference, so chaining stays simple.

ViT-L-14 rates consecutive shots as more similar (median 0.955 vs 0.937 within 30 s). There, 0.92 best matches B-16 at 0.90: 91 % agreement on 810 pairs, 251 stacks covering 813 of 1,146 photos (vs 253 / 804). Disagreements were borderline reframings either way.

### 7.8 Locations

Runs after stacks. It is skipped when nothing it depends on changed: a signature covers the history files, the location settings, and every photo's time, offset, and GPS. A full run takes about a second.

1. **Parse** (`timeline.py`): visits, routes, points, and time-zone spans from:
   - the phone Timeline export (Android `semanticSegments` / `rawSignals`; the iOS variant);
   - Takeout `Records.json`;
   - GPX.

   Parsed files are cached by path, size, and mtime. The first user's 43 MB export (2013–2026: 6,262 visits, 6,632 routes, 109,266 points) parses in 0.5 s.
2. **Place** (`locate.py`): camera GPS first. Otherwise the capture time is made absolute with the EXIF offset, or with the timeline's offset for that local time, and then matched:
   - inside a visit → the shortest matching visit (`visit`, ~50 m);
   - inside an activity → interpolated between the nearest points, or the activity's start or end (`route`; accuracy is half the bracket distance, capped at 5 km);
   - otherwise between points within `max_gap_minutes` (`route`), or one point within half of it (`nearby`);
   - else unplaced.
3. **Name** (`reverse_geocode`, bundled GeoNames cities with states, KD-tree): nearest town, region, country. Group keys: `cc`, `cc|region`, `cc|region|place`.

On the first library, every photo had an EXIF offset. 587 of 743 were placed from visits and 156 along routes (mean accuracy ~230 m, at most 9 minutes from the nearest evidence). All were within 1.8 km of one point, so town names suit trips, not a single area (see "spots" in §15).

## 8. Culling and export

- **Flags**: one global pick / reject / unflagged state per photo. Changes show immediately, are saved through `/api/flags`, and can be undone with Ctrl+Z; the API returns the previous flags. With a flag filter active (e.g. Picked + Unflagged, key `H`), rejected photos disappear at once and the selection moves on.
- **Grid**: click, Ctrl-click, Shift-click, drag-select, arrows (Shift extends), and Ctrl+A for the whole listing, not only loaded pages. A floating bar and P / X / U flag the selection. A right-click menu offers the photo view's actions.
- **Loupe**: the photo view; P / X / U flag and advance.
- **Stacks and compare**: **Stacks** collapses each burst into one tile, showing its pick if there is one. The compare view shows a stack, a selection, or (**Review stacks**) every stack in the filters that still has an unflagged photo.
  - Each photo shows its flag, a relative sharpness bar, clipping warnings, and the suggested keeper.
  - 1–9 marks keepers; Enter picks them and rejects the rest.
  - With nothing marked, the first Enter only asks and the second rejects all, so a stray Enter never rejects a burst.
  - Shift+X rejects all, Shift+U unflags all, and Z zooms all photos to 2.5× at the same spot.
- **Export** (`export.py`): copies the picks (all, within the filters, or an exact set from Curate) into a new folder. Files: images, images + RAWs, or RAWs only (optionally falling back to the image). Layout: flat, or the source folders.
  - Copies keep their dates (`copy2`) and are written under a temporary name first.
  - Nothing is overwritten. Identical files are skipped, so a rerun resumes. Other clashes get `-1`, `-2`…, shared by an image and its RAW.
  - Free space is checked first. The destination can't be inside a source. An `export-manifest.csv` lists every file.
  - Runs as a background job. Exported photos are recorded (§6.2) for the ↗ badge, the `exported` filter, and "only photos not exported before".
- **Location in exports** (`geotag.py`, on by default with a location history): photos placed from the timeline get the position in their copies.
  - JPEG and PNG copies get a GPS IFD appended to the EXIF, as a new IFD0 with a GPSInfo pointer plus the GPS IFD at the end. No existing byte moves, so MakerNotes with absolute offsets and the EXIF thumbnail stay valid, and the image data is streamed through unchanged (+340 bytes on the first user's JPEGs). Rebuilding EXIF with Pillow was rejected after it silently dropped the thumbnail (IFD1).
  - If the grown EXIF wouldn't fit a JPEG segment, the position goes into XMP instead.
  - RAW, TIFF, and HEIC copies are never modified; they get a `.xmp` sidecar.

## 9. Taste model

`taste.py`, trained on the user's flags on request (**Settings → Your taste → Calibrate**, ~1.1 s including cross-validation).

- **Unit: scenes, not photos.** One sample per stack or single photo (mean embedding). A keeper scene contains a pick or an export; a rejected scene contains only rejects; unreviewed scenes are skipped.
  - Per photo it didn't work: AUC 0.74 against 0.73 for the untrained CLIP quality score. It didn't transfer between libraries, and it was worse than sharpness at choosing a frame within a stack.
  - The cause: about 250 rejects are near-identical siblings of a pick, so the same content is labelled both ways.
- **Model**: balanced L2 logistic regression (L-BFGS, C = 1), with 5-fold cross-validation over scenes.
- **Results** on the first fully flagged library (1,889 photos: 61 picks, 1,828 rejects; 943 scenes, 60 keepers):
  - cross-validated AUC 0.82, against 0.64 for the CLIP quality score and 0.54 for random;
  - the top 20 % of scenes hold 68 % of keeper scenes, the top 30 % hold 77 %;
  - trained on one library, it ranks the other at AUC 0.78 / 0.66;
  - nearest neighbours to the picks reached only 0.68.
- **Offered only** with ≥ 20 keeper scenes, ≥ 50 rejected scenes, and cross-validated AUC ≥ 0.65; otherwise Settings → Your taste explains why not.
- **Sort order only**: the bottom 30 % still held 3 of the 60 keeper scenes, so "likely rejects" never flags anything.
- **Stacks on by default for these sorts**, since scenes are scored as a whole. In-sample, with Stacks the top fifth of tiles held 55 of 60 picks; without, 38 of 53.
- **Not used for frames within a stack**: that stays with sharpness and the suggested keeper.
- **Storage and training**: saved as `<model_id>.taste.npz` (derived) and loaded at startup (~7 ms); new photos are scored without retraining. An earlier version retrained after every flag change; an explicit button proved more predictable.
- **API**: `GET /api/taste` reports status, the figures, and `changed_since` (flags changed since calibrating; Settings suggests recalibrating from 25, with an amber dot in its nav).

## 10. Search and vocabulary

Text search encodes the query with the CLIP text encoder and ranks the filtered set by cosine similarity. It never cuts off results. Similar search uses a photo's embedding as the query; the photo and its duplicates are excluded.

**Query syntax** (`query.py`):

- **Exclusion**: `street -people`, `-"parked cars"`. The query vector is the search minus half of each excluded term.
  - Filtering by similarity to the excluded term fails, because CLIP rates "boats" high for any open water: "lake -boats" would lose every lake.
  - Subtraction cleared crowds from "palace -people" and "street -people -cars", and sailboats from "harbour -boats". Weights of 0.8 and above drifted to unrelated photos.
  - With only excluded terms, the photos least like them come first.
- **Alternatives**: `beach | lake -people`. Each alternative is its own phrase and vector, exclusions apply to each, and a photo scores its best match. `-dogs|cats` excludes both.
  - Measured top-30 splits: 11/19 for "ducks | palace", 18/12 for "boats | flowers", 16/14 for "sunset | people -boats", but 30/0 for "sky | statue", because plain-sky photos reach far higher similarities than any statue.
  - Rescaling each alternative against the library (median → 0, 99th percentile → 1) balanced all four. Not adopted: realistic alternatives are of a kind ("cats | dogs"), with comparable similarities, and the plain maximum keeps the score a real CLIP similarity. Revisit if a realistic query shows the imbalance.

**Names** (`query.name_matches`, on by default, `names=false` turns it off; a switch in the search box, remembered per browser): photos whose file name matches the search move to the front, then those whose parent folder does (for a file at a source's root, the source folder), keeping the image order within each group; they carry `name_match: file | folder` for a badge.

- Names are split into words at separators, camelCase, and letter/digit boundaries. Camera prefixes (IMG, DSC, DSCF, PXL, MVIMG, _MG, P…), numbers other than years 1900–2099, and letters wedged between digits (`4cat7`) carry no words.
- A name matches when it holds every word of any alternative (filler words like "at" and "the" ignored; a plural "s" either way) and no excluded term. Whole words keep short searches precise: "cat" matches `cat_01` and `cats-on-roof`, not `catalogue`.
- On the first library, all 1,926 file names are camera names and yield no words; the folders ("Schweden 2026 und so", "Apr 2026") do, so "schweden" puts that trip first. Matching adds 14–20 ms per search.

A text search takes about 10 ms.

**Your tags** (`custom_tags.py`; stored in selections v3, examples by content hash). A photo belongs when its similarity to its best-matching example reaches a fixed level per model: the stack threshold minus 0.05 (strict), 0.08 (normal), or 0.12 (loose). That is 0.87 / 0.84 / 0.80 for ViT-L-14.

- **Best match, not the average**: varied examples each cover their own neighbourhood, and one example equals "find similar". The examples always belong.
- **Tuned on the first library** with tags built from three seagull, fortress, and sheep photos. Members stayed on subject down to about 0.84; below 0.80 they became merely similar scenes (open sky, other waterfront buildings, plain shorelines). Sheep held a plateau of 61–67 photos between 0.85 and 0.75.
- **Rejected: a threshold derived from how alike the examples are.** Near-identical examples (0.96) made it far too strict, and varied examples need no lower bar.
- **Filtering**: members are computed from the in-memory embeddings (cached per tag version and embedding file) and passed to the shared filter as one JSON array per tag (`p.id IN (SELECT value FROM json_each(?))`), so every endpoint honours `ctags=` / `exclude_ctags=`.
- **Speed** on the first library (a three-example gull tag: 44 / 57 / 68 photos at strict / normal / loose): sidebar counts 36 ms, a filtered listing 5 ms, the dialog preview 13 ms.

**Vocabulary** (`vocabulary.yaml`, ~110 tags). Families are top-level keys; an entry is a name, or a name with alternative phrases (the name is always one of them). `styles:` holds Curate's style prompt pairs (§12). `family_templates:` gives single families their own prompt templates; `kind` (photograph, illustration or drawing, painting, document; confidence ≥ 0.7) uses the bare phrase (§14.2). Tags added to the default reach existing users only by editing their copy; the first user's was extended by hand (backup kept).

```yaml
templates: ["a photo of {}", "a photograph of {}", "a street photo of {}"]
subject:
  - people: [a person, a man, a woman, people walking, pedestrians, a couple]
  - cats: [a cat, a kitten, a cat sleeping]
  - a temple or pagoda: [a temple, a pagoda, a buddha statue, incense burning, a shrine]
scene:
  - a quiet alley: [a quiet alley, a narrow lane, a hutong, an empty backstreet]
  - other: [an abstract image, a blurry photo, a close-up texture, a plain background]
look:
  - black and white: [a black and white photo, a monochrome photograph]
  - ordinary daylight: [an ordinary daytime photo, a photo in plain daylight]
styles:
  moody: {label: Moody, towards: [...], away: [...]}
```

## 11. Server and UI

### 11.1 API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/photos?<filters>&collapse=&sort=&group=&offset=&limit=` | Grid listing. `collapse`: `dupes`, `stacks`, `none`. `sort`: `taken_at`, `-taken_at`, `name`, `-name`, `place`, `-place`, `taste`, `-taste`. With `group`, items carry their group key and `groups` lists all groups (§11.2) |
| GET | `/api/groups?group=&<filters>` | Groups only, with count, date span, cover, label, and centre (calendar, map) |
| GET | `/api/photos/{id}` | Detail: metadata, tags, RAWs, duplicates, stack, location, flag |
| GET | `/api/search/text?q=&names=&<filters>` | Text search (§10); `names=false` turns off file/folder name matching |
| GET | `/api/search/similar/{id}?<filters>` | Similar photos |
| GET | `/api/tags?<filters>` | Tags by family with counts within the filters; your tags (`custom`, with examples and counts); totals |
| POST | `/api/custom-tags`, `/api/custom-tags/{id}`; DELETE `/api/custom-tags/{id}` | Create a tag from example photos; edit (name, strictness, add/remove examples); delete |
| POST | `/api/custom-tags/preview` | Member counts per strictness and the edge photos, for the tag dialog |
| GET | `/api/facets?<filters>` | Filter options with counts; each facet ignores its own filter |
| GET | `/api/ids?<filters>` | Every id of the listing (select all) |
| POST | `/api/flags` `{ops}` | Set flags atomically; returns previous flags (undo) |
| POST | `/api/flags/reset?<filters>` `{scope}` | Unflag all or within the filters |
| GET | `/api/stacks?<filters>&unreviewed=`, `/api/stacks/{id}` | Stacks; one stack's photos |
| GET | `/api/suggest?ids=` | Suggested keeper with its scores |
| GET / POST | `/api/export` | Export status / start (picks, filtered picks, or `photo_ids`; `captions`: embed, xmp, txt, jsonl) |
| GET / POST | `/api/captions?ids=`, `/api/captions` `{items}` | Captions and fixed tags; typed edits and undo (return the previous values) |
| POST | `/api/captions/tags` `{ids, op, tag, to}` | Add a tag to all, remove it, rename it (merges) |
| GET / POST | `/api/captioning`, `/api/captioning/cancel` | Methods with availability and job status / generate (cheap methods at once, models as a job) / cancel |
| GET / POST | `/api/taglists`, `/api/taglists/danbooru` | Master tag lists / download the Danbooru list |
| POST | `/api/exported/reset?<filters>` `{scope}` | Forget export history |
| GET / POST | `/api/taste`, `/api/taste/calibrate` | Taste model status / calibrate |
| GET, POST, DELETE | `/api/profiles`, `/api/profiles/{slug}`, `/api/profiles/{slug}/activate` | List (with counts), create (empty or copying parts), rename, delete, switch |
| GET | `/api/styles`, `/api/hues` | Curate's styles and colour swatches |
| POST | `/api/curate?<filters>`, `/api/curate/alternatives?<filters>` | Curate draft; alternatives for one slot (§12) |
| GET | `/api/similar/map?<filters>&level=` | The listing's photos on the library's 2D layout, with clusters (§11.2) |
| GET, POST, DELETE | `/api/sources` | Photo folders (written to `config.yaml`); changes start indexing |
| GET, POST, DELETE | `/api/location-history` | History files and placement counts |
| GET | `/api/fs?path=&files=` | Server-side folder browser |
| GET / POST | `/api/index` | Indexing status / start (a request during a run queues one more) |
| GET | `/api/raws/unmatched` | Unmatched RAWs |
| GET | `/api/health` | Identity, version, config, model status (launcher) |
| GET | `/api/events` | One SSE stream per tab (§3) |
| GET | `/thumbs/{id}.jpg`, `/previews/{id}.jpg`, `/` | Images; the built UI |

`<filters>` (`filters.py`) are shared by all endpoints:

- `tags=1,2` (AND), `exclude_tags=3,4`;
- `date_from`, `date_to` (`YYYY-MM-DD`);
- repeated `camera=`, `lens=` (OR; empty = unknown);
- `focal_min/max`, `aperture_min/max`, `iso_min/max`, `mp_min/max` (megapixels, width × height / 10⁶);
- repeated `orientation=`, `flag=` (`pick`, `reject`, `none`), `exposure=`;
- `gps=`, `exported=`;
- repeated `country=`, `region=`, `place=`, `loc_source=`;
- repeated `folder=`: a photo's direct parent folder, without subfolders;
- `ctags=1,2` (your tags, AND), `exclude_ctags=`;
- repeated `ftags=` (fixed tags, AND), `exclude_ftags=`.

Different filters combine with AND.

Mutating endpoints accept only JSON bodies, so other sites can't trigger them without a CORS preflight, which the server doesn't grant. The server binds to 127.0.0.1 because the folder browser can list anything it can read.

### 11.2 UI

A single page without a router. View state lives in a Svelte store mirrored into the URL (search, tags, filters, grouping, sort, open photo), so views can be bookmarked.

- **Top bar**: search; a "similar to" chip; the workflow (**Review stacks**, **Curate**, **Export**); then **Settings** (a gear; indexing progress while it runs) and **?** (help).
- **Toolbar above the grid**: result count; **Group** with a **Grid | Calendar/Map** switch (or **Broad · Medium · Fine** for Similar); **Sort**; thumbnail size **S · M · L** (minimum tile width 112 / 168 / 260 px, remembered per browser; Large may load the 1600 px preview via `srcset`, since a 320 px thumbnail cropped square looks soft at that size); **Stacks**; and, when grouped, the group count with **Jump to…** at the right end.
- **Similar grouping** (`clusters.py`; `group=similar&level=`): hierarchical clustering (average linkage, cosine; cuts 0.45 / 0.35 / 0.25) of the photos the listing shows, so filters shape the themes. Above 6,000 photos a fixed sample is clustered and the rest assigned to the nearest centre. Clusters under 5 photos go to Other. Keys rank clusters by size; the photo → key map is a JSON object looked up per row in SQL (13 ms; a join against a JSON list took 470 ms). Cached per listing, level, and embedding file. Names (`clusters.names`): the kind of image when most members are not photographs; else the most distinctive phrase of a naming vocabulary (`defaults/cluster_names.yaml`: 483 things and 40 settings, i.e. light, style, sky, texture; not tags), i.e. the highest similarity to the cluster centre minus the library's mean similarity to that phrase. A thing within 0.035 behind a setting is named first ("birds in flight · a blue sky"); a second phrase within 0.01 joins; clusters that would share a name get their next phrase added. Without the model: the members' most common subject tag. The vocabulary's text vectors are cached per model (`<model_id>.names.npz`).
- **Size cap** (`clusters.MAX_SHARE`: 25 / 12 / 6 % of the view for broad / medium / fine, at least 25 photos): a larger cluster is clustered again with the cut lowered by 0.8× (down to 0.12); what its parts leave over stays one group. CLIP keeps kinds of image close whatever they show: all 259 illustrations of the first library were one medium cluster, even with only illustrations in view. Capped, they split into 20 groups by subject (99 % grouped): figurines, swimwear, friends, armour, animal ears, a beach… Centring the view's embeddings instead (removing what all share) left 75 % unclustered at the same cuts.
- **Naming rules, revised:** a custom tag held by ≥ 60 % of a cluster names it (the user's own word; "Holo" on the first library). Distinctiveness is measured against the **view** (its mean similarity per phrase), not the library, so among illustrations "an anime illustration" names none of them; the kind rule applies only when that kind does not fill most of the view. Added phrases (a second name part, or a qualifier telling apart same-named clusters) must also be about as similar to the cluster as the name (raw similarity within 0.02): phrases no photo in view resembles have a tiny baseline and tied with real matches ("figurines · peacocks", "swimmers · donkeys"). Same-named clusters without such a phrase are numbered ("swimmers 3"). In a view that mixes kinds, several clusters of one kind (e.g. illustrations among photos) were all named after the kind and numbered; now each is named against the **other clusters of that kind** (their size-weighted mean as the baseline), after the kind: "Illustrations: swimmers", "Illustrations: figurines", "Illustrations: cats · kittens". Phrases naming a kind of image (`media:` in `cluster_names.yaml`: an anime illustration, a manga drawing, digital art…) are skipped there, since the prefix already says it. On the first library, 26 medium illustration clusters in the whole library went from "an illustration or drawing 2…" to subjects.
- **Naming experiment** (first library, 34 medium clusters, contact sheets): the nearest sidebar tag was generic or wrong for many ("a portrait" for illustrations before the kind family, "grass" for sand textures, "sky and clouds · trees"). Nearest phrase of the big vocabulary picked style and sky phrases ("a macro photo", "a blue sky", "willows"). The distinctive phrase fixed most ("sheep", "ducks", "a palace garden", "a palace interior", "a metro station", "candlelight", "vintage cars", "spiders", "stars at night"). Subtracting only half or three quarters of the baseline was no better. Remaining misses: the Gröna Lund rides as "an industrial area" ("an amusement park" was 0.025 behind); portraits as "a man". On the first library: 300 ms for 2,132 photos (42 ms cached); the Sweden trip 70 ms, 20 medium groups.
- **Similar map** (`SimilarMap.svelte`, `GET /api/similar/map?<filters>&level=`): the Grid | Map overview for `similar`. `clusters.layout` runs t-SNE (scikit-learn, cosine, PCA init, fixed seed) over the whole library once per embedding file and caches it as `<model_id>.map.npz` (derived), so filters show or hide points without moving them; the endpoint returns the listing's points `[id, x, y, cluster]` and each cluster's label, size, and median position. Drawn on a canvas with d3-zoom: dots coloured by cluster; thumbnails (loaded on demand) once tiles reach 20 px, tiles growing more slowly than the spacing (∝ k^0.75) so zooming declutters; the 24 largest cluster names (all from 3× zoom) as buttons that open the group in the grid; click opens a photo. Hovering a photo or a cluster name draws the other clusters greyed out (dots grey at 30 %, thumbnails greyscale at 25 %) and that cluster on top, so neighbouring clusters are easy to tell apart. First layout of the 2,132 photos: 3.9 s; later requests read the cache.
- **Selecting on the maps** (`lib/mapselect.svelte.js`, `MapTools.svelte`): Pan · Box · Lasso on both maps (Shift-drag: box; Ctrl/Cmd: add). The drag is handled on the map's container and d3-zoom is filtered off while selecting (the wheel still zooms); the click ending a drag is ignored. Point-in-polygon (ray casting) on screen positions. The Similar map selects the photos inside and outlines them; the world map selects places and fills the selection with their photos (`/api/ids` with those places as a filter), plus "Only these places" as a filter. The selection bar is shown on the map; `P` / `X` / `U` flag the selection without the grid's move-to-next.
- **Captions & tags** (`captioning.py`, `CaptionView.svelte`; selections v5): a caption and ordered **fixed tags** per photo, opened from a selection (**Caption…**; the older "Tag…" became **Learn tag…**), the context menu, or the photo view, which shows both.
  - **Editing:** captions save on blur; tags are chips (Enter or comma adds, drag reorders). Photos are cards in a grid (`auto-fill, minmax(34rem, 1fr)`), so wide screens get several columns; 50 per page.
  - **Working set:** photos checked in the view (Shift+click ranges; all, this page, or those without caption or tags), else all of them. Generating and the side panel (add to all, replace X with Y / rename, which merges onto an existing tag, remove, clear tags, clear captions) apply to it, and the panel counts its tags. Every write returns the previous values, and Ctrl+Z restores them. "Only those still missing it" is offered only when adding (replacing is for photos that have something); it used to be the default, which left nothing to generate once every photo had a tag.
  - **Text in the photo (OCR):** a third kind of text, apart from the caption (`photo_text`, selections v6). Method `ocr`: Qwen3-VL-8B-Instruct (in bf16 on CUDA, like JoyCaption) with one prompt asking for JSON `{text, language, english}`: the text as written, in its own script and with line breaks, plus an English translation ("" when the text is English). `captioning.parse_ocr` takes the JSON out of fences or prose; "no text" answers store `''`, which counts as read, so filling gaps doesn't read the photo again. **Translate again** sends the current, possibly edited, text to the same model without the image and replaces only the translation. Cards show the text (with a `lang` hint from the language, for CJK fonts) and the translation, both editable; "not read" / "none found" otherwise. Check shortcuts: "with text", "text not read". The photo view shows both.
  - **One pass for everything** (`qwen`): caption, keywords, and text from one Qwen3-VL prompt (JSON with all five fields, `captioning.parse_combined`); §14.5.
  - **Quick text check** (off by default): photos that CLIP says have no text (max similarity to text prompts minus max to plain-photo prompts below −0.097) are marked read by `clip` instead of being read; photos that already have text are always read; a later read without the check treats them as unread. §14.5.
  - **Search in any script:** `query.text_matcher` (captions, tags, read text) matches Latin-script words as whole words and words in scripts without spaces (Han, kana, Hangul, Thai) as substrings, so `北京` finds `欢迎来到北京`; name matching found nothing there, since the whole run is one "word". Read text and translation are searched together, as the tier after captions (badge "text").
  - **Export:** `metadata.jsonl` rows gain `ocr_text` and `ocr_translation`. With `with_text`, the text and translation also follow the caption in the XMP `dc:description` (after a blank line; photo apps show the description as the caption, and XMP has no field for text in the image) and go on their own lines in the `.txt`; photos with only read text get it too.
  - **Export the selection:** **Export…** in the selection bar and the context menu opens the export dialog with `{ids, kind: 'selection'}`, the path the Curate draft uses (`photo_ids`): exactly these photos, picked or not.
  - **Generating:** cheap methods run in the request: `riffle` (vocabulary tags without articles or catch-alls like "other", plus learned tags) and `phrases` (cluster-name phrases with similarity ≥ 0.04 above the library mean and within 0.02 of the photo's best, at most 4). Models run as a `BackgroundJob`, one model per run, loaded in the job thread and freed after (`torch.cuda.empty_cache`); results are stored per batch of 8, so a cancel keeps what is done. Modes: add (a caption only where none, tags appended), replace (generated ones; typed and edited ones kept), replace all. Auto-exit and profile switching wait for the job.
  - **JoyCaption on the first library:** 8 photos in 44.5 s with the model load (~4.4 s per photo for a medium caption plus keywords); VRAM released after the run. Asked for "10 to 20 keywords", it gave 26–40, the last ones filler ("serene, calm, peaceful"): keywords are capped at 15 and "photograph" and the like dropped.
- **Search:** with Names on, text search ranks file-name, folder, fixed-tag, then caption matches first (whole words, `query.text_matches`), badges "tag" / "caption".
  - **Export:** `embed` writes `dc:description` and `dc:subject` into an XMP APP1 segment (JPEG) or iTXt chunk (PNG) in the same pass as the GPS EXIF edit (`geotag.Meta`, edits applied to the header only); a file that already has XMP gets a sidecar. `xmp`: sidecars (combined with a location). `txt`: `<image name>.txt` with tags, the caption, or both. `jsonl`: `metadata.jsonl` (`file_name`, `text`, `tags`).
  - **Optional install:** the `captions` extra (transformers ≥ 5, accelerate, onnxruntime[-gpu]); not in the standalone builds. Unavailable methods say why.
  - A grouping keeps groups together, so it allows the date sorts, plus the place sorts for location groups and the file-name sorts for folders. Those order the groups by name, and the photos within them by the same sort.
- **Sidebar**: "N active · Clear all" when anything narrows the view; then collapsible filter sections (Flag, Folder, Date, Camera, Lens, Exposure, Orientation, Location, Places) and tag families. Subject, Scene, and Look start open; each section remembers its state.
  - Options show counts within the other filters.
  - A filter that can't narrow the result is hidden unless in use.
  - The Folder section lists direct parent folders, labelled like the folder groups: the first 15, then "All N folders".
  - Tags include on click and exclude on Alt+click or hover-**−**.
- **Grid**: badges (✓, ✕, ↗, RAW, duplicates, ▦ stack count), and multi-select (§8). When grouped: a sticky header for the current group with its full count, and "Only this day / folder".
- **Grid loading** (`lib/listing.svelte.js`, `lib/listing-layout.js`): the first page (120) gives the total and every group with its count, so the whole layout (columns from the width, rows per group, header heights) is known before the photos are. Only rows within a screen of the viewport are rendered, and their pages are fetched 60 ms after scrolling pauses (at most 4 requests at once); rows not loaded yet show placeholders. A jump ("Jump to…", "Show day / place", an overview's "open") scrolls to the computed position and loads one or two pages. It used to load every page above the target (17 requests for the last day of the first library), and every loaded photo stayed a tile in the page. Arrow keys, Shift-click ranges, and drag-select work on the layout and load the pages they cover. Photos flagged out of an active flag filter are hidden at once by adjusting positions (they are always loaded, since you just flagged them). Random access needs a deterministic order and a group summary in listing order: the place sorts gained a `source, rel_path` tiebreak, and folder groups stay in path order when sorting newest first (the summary had reversed them).
  - Groups appear in date order, or trip order for places (by each group's first photo), or path order for folders.
- **Overviews**: a year calendar (days with a cover and count), or a map with one cluster per place, region, or country. Clicking opens the group in the grid. The map draws bundled Natural Earth outlines (`world-atlas` 50m, loaded lazily) with d3-geo and d3-zoom; street-level detail was left out deliberately.
- **Photo view**: preview, metadata, tags, location with source and accuracy, taste score, and flag buttons. Actions: Stack, Find similar, Show day, Show place, Copy path.
- **Settings** (`Settings.svelte`, one component per page in `components/settings/`; 28 Sep 2026, replacing the Library dialog, which had grown into one long page of unrelated sections): a nav on the left, one page at a time. Pages: Photo folders (the folder browser behind *Add a folder…*, open on first run), Indexing (Index now, progress, log), Locations (location history), Profiles, Your taste, Flags & exports (the resets), Files (config, flags, derived data paths). While indexing runs, a status strip sits under the header and the nav marks Indexing. `view.settings` holds the page, so other views open one directly: first run → Photo folders, *Manage profiles…* → Profiles, Curate's notes → Indexing / Your taste. `Ctrl+,` opens it (the last page, remembered per browser), `1`–`7` switch pages.
- **Profiles**: a switcher in the top bar once there are two or more, and "Profile: <name>" at the top of the sidebar outside the default profile.
- **Help**: the workflow in steps and all shortcuts.

Tailwind only; no component library.

## 12. Curate

`curate.py`, `Curate.svelte`. It drafts a small selection (photo book, exhibition) from the current filters: high quality, but varied in content, time, and place.

**Candidates.** Picks and unflagged photos within the filters; a switch adds rejects, and a locked reject always stays. Each stack or duplicate group contributes one representative: the locked photo, else the pick, else the best frame (CLIP quality, exposure, sharpness).

**Quality `q`.** Rank percentiles within the candidates, weighted:

| Signal | Weight |
|---|---|
| Taste | 0.45 (when calibrated; otherwise its weight moves to CLIP quality) |
| CLIP quality | 0.25 |
| Exposure | 0.10 |
| Pick | bonus +0.15 |
| Exported before | bonus +0.05 |

Then:

- **Styles** (CLIP prompt pairs in `vocabulary.yaml`; score = embedding · (mean towards − mean away)) add `0.6 · mean(wₖ · (2·pctₖ − 1))` over the active sliders.
  - Each style was checked with top/bottom-12 contact sheets on the first library and separates visibly.
  - "Breathtaking" became Scenic. "Artistic" turned out to be what CLIP reads as abstract close-ups, so it is labelled Abstract & details.
- **Colour and light** choices (§7.6) enter the same term at weight 1, using `curate.look_scores`. Hue affinity is the chroma-weighted share within ±30° of the swatch, with a triangular window. Photos not yet analysed get the median.
  - Checked with top-40 contact sheets per measure. "Soft" mostly finds empty skies, which the quality term keeps in check.
- **Accent** swatches (a second row under the main colour) lean towards photos with an accent of that hue: the strongest accent within ±30°, by strength. First library, the OM-5 photos: red → the red car, red trainers, cola bottles on a café table, a red bus on a bridge; yellow → the pansy's centre, the yellow post box, Swedish flags, the underground train; green is weaker (green is everywhere).
- **Uniqueness** (Common ↔ Unique, −1 … 1): `curate.uniqueness`, 1 − the mean CLIP similarity to a photo's 5 closest photos in the whole library, not counting its own stack or duplicate group (so a burst is judged against everything else). As a percentile among the candidates, `0.6 · slider · (2 · pct − 1)`. Different from Most varied, which spreads the draft itself. 1.3 s for 13,468 photos (chunked matmul); cached in memory and on disk (`<embeddings>/clusters/<model>-unique.<hash of ids and groups>.npy`) and warmed with the other caches. The reason says "unlike most of the library" (top 10 % of the library). First library: unique → a pigeon, the post box, chips at a café, the red car, a snake on the road; common → skies with a bird, sheep, lake shores. Its most unique photos also include short series (7 frames of a torn label) whose frames are one stack: unique as a whole, as intended.
- **A search** (the §10 syntax) adds its own term, `1.0 · w · (2 · pct − 1)`, where `w` is the Search influence slider (0–200 %, 100 % by default; shown when there is a search). It isn't averaged with the styles, so it stays strong beside them, and it scores rather than filters.
  - On the Sweden trip with rejects (474 candidates): "boats" gave boats and harbours from different spots, "people -boats" gave people without boats, and "palace | church" gave palaces, churches, and towers.

**Selection.** Greedy maximal marginal relevance: each step adds the candidate maximising `(1 − v)·q − v·max redundancy`, where `v` is Best ↔ Most varied. Redundancy to a chosen photo is the weighted mean of:

- **content**: CLIP cosine, rescaled so 0.5 → 0 and 0.95 → 1;
- **time closeness**: `exp(−|Δt| / 3 h)` × time spread;
- **place closeness**: `exp(−d / 300 m)` × place spread × position reliability (1 for EXIF and visits; route estimates by `accuracy_m`).

Locked photos are taken first and removed ones are excluded. The result is deterministic. The original design had day/place coverage quotas; the time and place terms already covered every day of the first trip, so they weren't needed.

Place names are too coarse for place variety (a whole trip can fall into one town); coordinates separate the harbour from the old town.

**View.** The draft is shown as one justified block in capture order (`lib/justify.js`):

- Rows of equal height fill the width.
- Row breaks are chosen for the whole draft by dynamic programming, keeping heights within 60–160 % of a target of about a quarter of the width. The last row also fills unless that would make it too tall.
- It uses previews, not the 320 px thumbnails.
- On the Sweden drafts, rows ranged from 255 to 378 px.

Per photo: the reason (e.g. "your pick · best of 14 similar shots · very moody"), Lock, Alternatives (the stack's other frames first, then `0.5 · redundancy + 0.5 · q`), and Remove (for this draft only; not a reject).

**Mark as picks** is the only action that changes flags, and it can be undone. **Export** sends exactly the draft (`photo_ids`). The draft (settings, search, locks, removals) is stored in `localStorage` per filter set. The API still returns `cover` and `sections`, which the view no longer uses.

**Measured** on the first library (1,926 photos, 61 picks, taste calibrated):

- 40–300 ms per draft; the first request with a style encodes its prompts.
- Sweden trip with defaults: 30 candidates; 12 photos over all 3 days and 5 places (7 places at variety 0.8).
- With rejects (474 candidates) and Moody +1: an alley, the underground, and a crow at a café table came in, and 10 picks were kept.

**Order** (above the draft; switching asks the server for nothing: `curate.orders` returns the draft's photos in every order): **Date**; **Best** (quality); **Alike** (a greedy chain from the first by date, each next the most similar remaining); **Colour** (the main hue, the chroma-weighted circular mean of the histogram, around the wheel from red; photos with little colour last, light to dark); **Light** (light to dark); **Route** and **Zigzag** (the shortest and the longest path through the photos' places, by map distance: greedy chains from every start, the best improved by 2-opt, 16 ms for 60 photos; photos without a place last; shown when the draft has places); **Yours**: drag a photo onto another to put it before or after it (a bar shows where); the dragged order is kept for photos still in the draft, new ones follow by date. The order and the dragged ids are saved with the draft; Curate from a walk starts in the walk's order. The export numbers the files in the shown order ("Number the files in draft order", as for walks), and the photo view steps in it.

**Like the locked photos** (a slider, −1 … 1, neutral in the middle; needs locked photos): each candidate's closeness to its nearest locked photo (a varied set's average resembles none of them), as a percentile among the candidates, times 0.7 × the slider, added to its score; variety still keeps near-copies out. On the first library (Sweden, the palace and a seagull locked, 12 photos): towards them, the mean similarity to the nearest locked photo rose from 0.62 to 0.75, 7 of the 10 free photos changing (saturated already at 0.5); away, it fell to 0.57, 5 changing (less room: variety already avoids them).

**Sidebar** (28 Sep 2026, regrouped): the search and its influence; *Draft* (size, Best ↔ Most varied, Include rejects); *Spread* (over time, over places); *Lean* (Common ↔ Unique, Like the locked photos, Surprise; Clear); *Colour & light* (main colour, accent, light, contrast, colour; Clear); *Style* (collapsed); then the counts and Reset. Double-click resets a −1 … 1 slider.

**Surprise** (a slider, 0 = off): each candidate gets Gumbel noise on its score (so the picks are a sample favouring the best), scaled to the standard deviation of the candidates' quality (so full surprise means the same in any library), and none for the weakest third (surprise reshuffles the reasonable ones only). The noise is fixed per photo id and seed (a splitmix64 hash; drawing per position shifted it whenever a photo was removed, and a removal changed 5 of 6 photos): removing or locking still changes only its slot, and **Shuffle** draws a new seed, saved with the settings. On the first library (drafts of 12): half surprise keeps about 8 of 12, full about 6 (Sweden) to 8 (April), with 21–30 different photos over 6 draws; all picks stayed in (their bonus).

## 13. Standalone releases

PyInstaller folder builds without a console window, zipped. A single-file build would unpack PyTorch on every start, so it was ruled out.

**Targets:**

- Linux x64, built on Ubuntu 22.04 for an older glibc;
- Windows x64;
- macOS arm64 (PyTorch has no Intel macOS wheels any more).

Linux and Windows use CPU-only PyTorch, since CUDA wheels would exceed GitHub's 2 GB asset limit. A Vulkan backend isn't practical (PyTorch's was mobile-only and is deprecated). ONNX Runtime with DirectML or CoreML would be the route to GPU support and much smaller builds (§15).

**CPU defaults:** a first start in the app writes the faster model into the new config (`paths.standalone_defaults`: ViT-B-16 / DFN-2B, `stacks.min_similarity` 0.90). On the same CPU that gives 11.5 photos/s instead of 2.7, and a 571 MB download instead of 1.6 GB. The pip install keeps ViT-L.

**In the app** (`frozen.py`, `packaging/riffle_app.py`):

- Output goes to `riffle.log` (previous run: `.log.1`).
- Fatal errors show a native message box.
- On Linux the browser starts without PyInstaller's `LD_LIBRARY_PATH`.
- The model loads in the background (§3), and the page shows a banner while it downloads or if it fails.
- torchvision ≥ 0.29's `_C_stable` operators are collected by hand, because the PyInstaller hook misses them.

**CI** (`.github/workflows/release.yml`):

1. Build the UI.
2. Install and run the tests.
3. Build.
4. Smoke-test each executable offline (`HF_HUB_OFFLINE=1`): `/api/health`, the UI, and `model: failed` without crashing.
5. Package (`tar.gz` keeps the executable bit).

A `v*` tag matching the `pyproject.toml` version publishes a release; a manual run only builds. v0.1.0 artifacts: Windows 208 MB, macOS 201 MB, Linux 311 MB.

**Measured locally** (Linux, Python 3.14, torch 2.14 CPU): a 67 s build, 920 MB unpacked; the server answers in 0.5 s, and the model is ready 25 s later on a first start including its download.

**Open:** signing and notarisation (paid certificates), an icon, publishing to PyPI.

## 14. Evaluation (open)

A short qualitative note:

- 20 text queries: how many of the top 20 results are relevant?
- 10 similar searches: useful, or just near-copies?
- Tags: 30 photos each for the 10 largest tags; which are reliable?
- Timing: full index and search latency.
- Is a multilingual CLIP model worth adding for Chinese queries and signage?

### Large libraries: first use of Similar and Discover (28 Sep 2026)

With 12,000 photos (the user added 10k for testing) the first Similar grouping or Discover after a start took seconds. Measured on synthetic data of that size: clustering 4.5 s per level (the sampled path: average linkage on 6,000); Discover's library axes 5.8 s (they need the fine clustering); phrase profiles and named axes 0.2 s together; the layout fingerprints one thumbnail read per photo, all of them again whenever the embeddings changed.
- **Fingerprints in indexing:** computed with the colours in `dupes.compute_phashes` (the preview is open anyway) and stored per photo (catalogue v8, `layout`), so new photos only add their own; photos indexed before fall back to the thumbnails (cached on disk).
- **Clusters on disk:** `cached_cluster` keys `clusters.cluster` by the exact set of photos, the level, and the clustering settings (so tweaking them recomputes), in `<embeddings>/clusters/` (the 24 newest kept). Discover's axes reuse the fine clustering. After a restart, the first request reads them.
- **Warming:** a background thread, whenever the embeddings change (at start, after an indexing run) and the library has 1,000 photos or more, builds the unfiltered library's clusters at every level and Discover's lens data, after the AI model has loaded.
- **Indicators:** Discover opens at once (the start photo is picked inside it, "Finding a photo to start from…" with a spinner); the grid says "Grouping the photos by similarity…" while that runs.
- On the first library (2,340 photos) a restart took the first Similar requests from 0.6–1.1 s to 0.1–0.8 s and the Discover start from 1.1 to 0.6 s; the rest is naming the clusters, not cached. At 12,000 the clustering (4.5 s per level) is the part now read from disk.

### 14.1 Experiment: penultimate-layer features for similarity (27 Sep 2026)

Question: would image features from deeper inside CLIP serve "find similar" and your tags (§10) better than the final, text-aligned embedding? Compared on the first library (2,132 photos, ViT-L-14 DFN-2B, previews), each variant also mean-centred:

- **final**: the current embedding (projected into the joint image–text space);
- **pre-proj**: the pooled image features before that projection;
- **pen CLS**: the class token after the second-to-last transformer block;
- **pen mean**: the mean of that block's patch tokens.

**Near-duplicates.** Photos within 5 s (or 30 s) of each other count as the same subject. Scored by R-precision: the share of each photo's nearest neighbours, as many as it has burst partners, that are those partners.

| | 5 s | 30 s |
|---|---|---|
| final | 0.758 | 0.680 |
| pre-proj | 0.759 | 0.683 |
| pen CLS (centred) | 0.740 (0.747) | 0.672 |
| pen mean | 0.753 | 0.676 |

**Concepts.** Four concepts labelled by eye: sheep, gulls, the Vaxholm fortress, candles; about 20–50 matching photos each. Only the union of every variant's top 40 was labelled; everything else counts as non-matching. Scored by average precision:

| | Your tags (3 examples) | Find similar (1 example) |
|---|---|---|
| final | 0.966 | 0.940 |
| final, centred | 0.979 | 0.946 |
| pre-proj, centred | 0.979 | 0.947 |
| pen CLS | 0.976 | 0.937 |
| pen CLS, centred | 0.985 | 0.947 |
| pen mean | 0.935 | 0.870 |

**Result.** No clear win for the penultimate layer:

- Its best form (class token, centred) leads on tags by 0.006. Most of that comes from one concept, which is within the noise of four fairly easy concepts.
- It is slightly worse on near-duplicates.
- Averaging patch tokens is clearly worse.

Most of the small gain comes from mean-centring (subtracting the library's average embedding), which costs nothing on the current embedding. But it shifts all similarities, so the tag and stack thresholds would need retuning.

Not adopted: it would mean re-embedding every photo and keeping a second matrix, since text search still needs the final one.

**Open:** more nuanced concepts could favour deeper features, which this benchmark could not test: one particular person, pet, or character among others of its kind. That needs a labelled set with such identities. See §15.

### 14.2 Experiment: clustering and a 2D map (27 Sep 2026)

Question: can the CLIP embeddings group a library into broader themes than stacks (a "Similar" grouping), and lay it out as a 2D map? Tried on the first library (2,132 photos) in a separate environment with scikit-learn.

**Clustering:**

| Method | Time | Clusters of ≥ 5 | Photos in one |
|---|---|---|---|
| Hierarchical, average linkage, cosine, cut 0.45 (broad) | 0.4 s | 23 | 98 % |
| same, cut 0.35 (medium) | 0.4 s | 56 | 94 % |
| same, cut 0.25 (fine) | 0.4 s | 95 | 83 % |
| HDBSCAN (min cluster size 5 / 10) | 3 s | 119 / 51 | 76 / 66 % |

- **Medium** clusters were coherent in contact sheets: waterfront, flowers, birds in the sky, meadows, busy streets, sheep, ducks, palace gardens, palace interiors, illustrations, blurred abstracts.
- **Fine** split the largest into sensible parts: lakes, ferries, sailboats, and waterfront buildings; drawn illustrations and photographed figurines.
- **Broad** was too coarse: one cluster held 518 nature photos.
- **HDBSCAN** left a quarter to a third of the photos out, so it is not used.

**Naming** by the tag nearest the cluster centre worked except where the vocabulary lacked a tag (illustrations named "a portrait") and where catch-alls ("other") won. The fixes were a `kind` family and three new subject tags (castles and palaces, candles, paintings and artworks), checked with contact sheets:
- "Toys and figurines" was dropped: CLIP put it on illustrations whatever the phrasing (292 of 329).
- Kind "a screenshot" and "a 3D render" were left out: the library has none, so the softmax gave them random product shots and landscapes (14 each even at 0.8 confidence).
- Kind needs the bare phrase as its template: with "a photo of {}", only 856 photos counted as photographs and 167 as screenshots; with "{}", 1,629 and 24.

**2D map:** t-SNE (scikit-learn, cosine) in 1.8 s. Thumbnails formed clear regions: illustrations, interiors and museums, streets and squares, waterfront, skies, flowers, meadows and wildlife. UMAP was not tried; it needs numba (heavy, slow to start, awkward in PyInstaller builds).

**Adopted:** the "Similar" grouping and, after it, the map (§11.2).

### 14.3 Experiment: captions and booru tags (27 Sep 2026)

**Question:** which local models to offer for generated captions and tags (exported as `.txt` next to the image, among other formats), how fast they are, and whether a master tag list can keep tags to real booru tags. Captioning itself is established (TagGUI does this); the question is the choice for Riffle.

**Setup:** 40 images of the first library (25 photos from three folders including film scans, 15 illustrations from two folders), 1600 px previews, RTX 3090. Models from the local Hugging Face cache, transformers 5.17, onnxruntime 1.30, greedy decoding.

| Model | Output | Per image | VRAM | Tags / image |
|---|---|---|---|---|
| WD EVA02-large tagger v3 (ONNX) | booru tags + rating, thresholds 0.35 / characters 0.85 | 0.07 s GPU, 1.0 s CPU | ~1 GB | 31 |
| JoyCaption Beta One (Llava, 8B, bf16) | caption (≤ 60 words) + Danbooru tag list | 5.5 s (both) | 17 GB | 37 |
| Qwen3-VL-8B-Instruct (bf16) | caption (2–3 sentences) + prompted Danbooru-style tags | 6.2 s (both) | 18 GB | 18 |
| Florence-2-large PromptGen v2.0 | — | — | — | — |

**Results:**
- **Florence-2 did not load:** its bundled model code (`trust_remote_code`) fails under transformers 5 (`Florence2LanguageConfig` has no `forced_bos_token_id`). Models that ship their own code break as transformers moves on; offer only models transformers supports natively.
- **WD tagger:** fastest by far (usable on CPU), and every tag is from its list by construction. Right for illustrations: characters and details are specific. Wrong for photos: a blurry grass photo was rated "explicit", and a black-and-white street scan got a VTuber character and "1girl". Use it for illustrations only, or without characters and rating on photos.
- **JoyCaption:** the best booru tags from a language model (82 % in the WD list; nearly all the rest are real Danbooru tags outside the WD list's ~10k, e.g. `bangs`, `water_reflection`). The Danbooru prompt adds boilerplate on photos (`copyright:original`, `meta:photoshop_(medium)`: 5 % of tags); drop `artist:` / `copyright:` / `meta:` tags unless asked for. Captions are accurate and concrete, on photos and illustrations. It is uncensored: explicit illustrations are described explicitly.
- **Qwen3-VL-8B:** captions as good as JoyCaption's and better at reading text (it named a vending machine by its sign). Poor booru tags: only 44 % in the list, and much of the rest isn't booru vocabulary (`serene`, `peaceful`, `anime_style`, `daylight`). A usage example in the prompt was copied into the output (`1girl` on a seagull photo) until it was removed.
- **Master tag list:** mapping unknown tags to the nearest list tag by CLIP text similarity (≥ 0.85) is unreliable: it fixes `trees → tree` but also maps `serene → >:)`, `cute → ;)`, `illustration → enmaided`. Spelling rules (plural, `-`/`_`) catch the good cases (6 % of Qwen's tags) and none of the bad ones. With a full Danbooru tag list plus aliases (not only WD's), almost all of JoyCaption's tags would be exact; tags that match nothing are dropped.

**Conclusion:** styles map to models: booru tags from the WD tagger (fast, CPU-capable, illustrations) or JoyCaption (photos and illustrations, GPU); natural-language captions from JoyCaption or Qwen3-VL (GPU). A master list is a filter with spelling rules and aliases, not a semantic mapping.

**Adopted:** Captions & tags (§11.2) with JoyCaption for captions and keywords, the WD tagger and JoyCaption's Danbooru preset for booru tags, and master lists as filters. Photos come first: booru tags are a collapsed section. Qwen3-VL came in later for text in the photo and the one-pass method (§14.4, §14.5).

### 14.4 Experiment: reading text in photos (27 Sep 2026)

Qwen3-VL-8B on the RTX 3090, 1600 px previews: synthetic signs pasted into photos, exact in every case, with correct translations: Chinese ("欢迎来到北京 / 地铁站出口 B" → "Welcome to Beijing / Metro Station Exit B"), small Chinese opening hours, a Japanese menu ("ラーメン 800円" → "Ramen 800 yen"), and English (no translation). Real photos: the vending machine scan read in full (German), a partly hidden Swedish sign read as far as visible. Photos without text: 1.3 s (empty answer); with text 2.0–2.6 s; model load 5.5 s from the cache. VRAM released after the run. The translation of a cut-off word varies between runs ("skälf" → "shellfish", then "shiver").

### 14.5 Experiment: one pass, and skipping photos without text (27 Sep 2026)

**One pass.** Qwen3-VL-8B, 8 images (5 photos, an illustration, two synthetic signs): separate caption, keywords, and OCR prompts took 53.2 s, one combined JSON prompt 41.8 s (6.7 → 5.2 s per image). The saving is reading the image (~1,900 tokens at 1600 px) once instead of three times; generating the answer costs the same. Quality was comparable: identical text on the signs, the vending machine, and the watermark; the combined pass once made a partly hidden word longer ("skälf" → "skälfjärilar"); captions as good, sometimes more complete; text in the photo also shows up in caption and keywords. Only reading text is far faster on photos without text (1.3 s against 4.5–6 s). Adopted as an extra method; the separate ones stay.

**Skipping photos without text.** Ground truth: Qwen OCR on 240 random images of the first library (6.5 min). Text was common: 43 of 201 photos (21%; boat names, shop signs, number plates, a lens ring), 28 of 39 illustrations (signatures, watermarks). The user expected most to have none.

| Filter | Photos with text kept | Photos skipped | Cost |
|---|---|---|---|
| CLIP: max(text prompts) − max(plain prompts), −0.097 | 95% | 22% | free (existing embeddings) |
| CLIP, same, for 90% | 90% | 29% | |
| PP-OCRv4 text detector, 960 px | 74% | 43% | 54 ms (GPU) |
| PP-OCRv4 detector, 1280 px | 95% | 27% | 81 ms, 4.7 MB model, cuDNN |
| PP-OCRv4 detector, 1600 px | 95% | 18% | 108 ms |

CLIP separates photos with and without text moderately (AUC 0.80) and illustrations not at all (0.56: signatures are small). The misses are small, incidental text. The detector sees text in ~70% of photos and gains little over CLIP; the `rapidocr_onnxruntime` package would also pull in the CPU onnxruntime, which replaces the GPU build's files (it did in the test; repaired). Adopted: the CLIP check, off by default (it saves ~20% but drops 1 in 20 photos with text silently, if marked).

**Batching.** Several photos per `generate` call was slower (1.24 s per photo alone, 2.04 in twos, 2.84 in fours, padding and one differing text); batch 8 ran out of memory at 24 GB. Photos stay one per call.

**Long texts.** Two old maps (259 and 343 words) ran past 1024 new tokens, and the cut-off JSON was stored raw. The limit is now 2048, and a cut-off answer still yields the fields it got to (`captioning._json_object`).


### 14.7 Discover, a walk by aspects of similarity (27 Sep 2026; kept 28 Sep 2026)

A **Discover** button in the photo view opens a graph: the photo in the middle, branches to photos related in one way each, the way back on the left, the trail along the bottom (`discover.py`, `GET /api/discover/{id}`, `Discover.svelte`). Lenses:
- **Echoes:** same subject elsewhere, same light and mood, colour echo, shape echo, shares a tag.
- **Contrasts:** same subject in opposite light, complementary colours.
- **Context:** the same day, and the closest photo as a baseline.

Signals:
- **What and how:** a phrase profile over the cluster-name vocabulary (z-scored per phrase, each photo's 12 strongest kept), split into things and settings.
- **Colour and light:** `colors.py` (hue histogram, brightness, contrast, colourfulness).
- **Layout:** a new fingerprint, a 12×12 z-scored luminance grid of the thumbnail (cached as `<model_id>.layout.npz`).
- **Tags and time:** the user's own tags, and when photos were taken.

A photo shows on one branch only; the trail and the centre's stack are excluded; jumps between photos and illustrations are penalised in the colour, light, and shape lenses. Short-term learning in the page: chosen lenses come first (and show a fourth photo after two picks); a drift (the decayed phrase change of each step) gives candidates moving the same way a bonus and is shown ("drifting towards night · blue").

**On the first library** (contact sheets for 8 seeds): 2.8 s the first time (profiles and 2,300 layout fingerprints), then 30–50 ms per step.
- **Good:** the colour echo (a blue-sky seagull → statues against the same blue; the orange figure → tan sandals, red sneakers), shape echoes (the palace across the water → flat horizons, a bird on a hill line), the same subject in opposite light (sheep → a sheep silhouetted at sunset), complementary colours (orange → blue harbours), the same day (a macro → the flowers minutes before and after).
- **Weak, then changed:** the tag lens with the automatic vocabulary tags led nowhere ("outdoor", "livestock" on an illustration, "street vendors" on figurines): it now uses only the user's fixed and learned tags, preferring alike photos that are not near-copies. "The same place" matched everything placed at home by the location history: the lens is now the same day only. Colour echoes of greenery were random until the brightness had to match too.
- **Still weak:** light-and-mood reasons use setting phrases that read oddly ("isometric perspective", "a photo with a date stamp").

**Second round.** Round photos and a looser layout: each branch at a slightly irregular angle and distance (seeded by the centre, so it holds still), its other photos orbiting the first on the outer side. Branches now sample 3 photos from their best 12, weighted exp(−rank/3); a seed per step, kept in the trail, shows the same branches when going back, and **Shuffle** (R) draws again. Quality (the user: "half the images are missed shots or random stuff"): rejected photos are left out (a switch includes them); photos in the weakest 30% by Curate's quality mix (CLIP good/bad and sharp/blurry, exposure, taste) only show when picked; above that, a bonus of up to +0.15. On the first library 1,828 of 2,340 photos are rejected, so without rejects about 500 remain: the branches were visibly better (no misses left on the sheets), some thinner (one photo instead of three), and a few lenses had nothing left for some photos (every other palace shot was rejected).

**Third round: axes, and distance by similarity.**
- *Random masking of embedding dimensions* (the user's question): CLIP spreads concepts over all 768 dimensions, so a random half keeps 8.8 of 10 nearest neighbours (similarity correlation 0.97; a random tenth still keeps 6.9). It only adds noise, like the sampling already there. Not used.
- *Principal axes* (16, named by the phrase whose z-scores correlate most with each end) were interpretable at the top (photo ↔ illustration, macro ↔ wide waterfront, palace interiors ↔ outdoor water, bee macros ↔ dusk silhouettes). But on a library of long photo series they mostly say "which series is this": names like "sheep → water caustics", "spaceship · person reading", and weak lenses. Replaced.
- *Named axes* (`TEXT_AXES`, 14 pairs of opposite phrases: close-up ↔ wide view, night ↔ daylight, indoors ↔ outdoors, illustration ↔ photograph, people ↔ empty, city ↔ nature, calm ↔ busy, colourful ↔ muted, warm ↔ cool light, water ↔ dry land, motion ↔ stillness, historic ↔ modern, dramatic ↔ plain, sharp ↔ dreamy; a position is the similarity to one end minus the other, z-scored). Two lenses:
  - **Shared traits:** 2 of the centre's distinctive axes (≥ 1 SD out), drawn with weight |position|; the same side and close on both, different overall.
  - **Mirrored:** one strong axis flipped, the rest kept.
  
  Results: palace, "city → nature" → a forest path; "historic · wide view" → the old town and castles across the water; beetle, "muted → colourful" → bees on purple flowers, pansies; the night vending machine, "dramatic · night" → a dark night silhouette. Both get the photo/illustration penalty, except a mirror on the illustration axis.
- *Graph:* a branch's distance from the centre follows its first photo's similarity (0.3 at the edge … 0.9 near), with a little jitter, never on top of the centre.
- *The library's own axes, again (unnamed; the user: less synthetic than phrases).* PCA on one point per group of alike photos instead of every photo: the centres of the fine Similar clusters plus the unclustered photos, so a long series counts once. Measure: the largest share of one shooting day among the 30 photos at each end of axes 1–12, lower = more general. Every photo: 0.67 on average; one per stack: 0.59; cluster centres: 0.53; random directions: 0.40 (the library comes from few trips). Axes with a series at either end (share > 0.8; averaging the two ends let one-sided series axes through) are dropped: 8 of 12 remain. Two lenses on them, without names: **hidden traits** (alike on 2 of the centre's strong axes, different overall) and **mirrored, hidden trait** (one flipped, the rest kept). The mirror's closeness is relative to the typical distance: a fixed cut-off that suited the overlapping named axes let almost nothing through on independent ones.
  - Results: plausible but loose jumps, hard to read without a reason. The beetle's hidden traits were sunny countryside (a village road, the car, a cat in grass); the sheep mirrored to a blue lake under clouds. Less synthetic than the named axes, as intended; also more random. Both kinds are kept for walking.
- *At most 9 branches:* favoured lenses first, "closest" dropped first.
- *The walk as a result:* photos can be taken out of the trail (× on hover; not the current one; they may show up in branches again). **Replay** plays the walk full-screen (cross-fades every 4 s, ← → Space, Esc), each step captioned with the lens and reason that led there (the trail remembers them). **Pick walk** flags it (with Undo in the notice, since Ctrl+Z is the grid's). **Export walk…** opens the export dialog over Discover (kind `walk`) with **Number the files in walk order**: `export.assign_targets(numbered=…)` prefixes every file of a photo (image, RAW, sidecars, `.txt`) with its position, `01_`, `02_`…, so the folder keeps the sequence.
- *Curate walk…* opens Curate with the walk's photos locked (`view.curate = {locked}`; the draft's size at least the walk's length); Curate fills the rest from the current filters. Locked photos now stay in Curate's candidates even outside the filters (`build_pool`: `WHERE (filters) OR id IN locked`); before, a walk step outside them was silently dropped.
- *Place:* first only positions that could be trusted (camera GPS, or the location history within 200 m: 1,516 of 1,926 placed photos); then, at the user's request, every position with its uncertainty (GPS ~20 m, estimates their accuracy): "same place" widens by the pair's mean uncertainty, "nearby" needs a distance beyond it (else the direction is noise), pairs uncertain beyond 1 km are not compared, uncertainty costs up to 0.4 (certain positions first), estimates are worded "about …", and only a step longer than its uncertainty sets the heading. The seagull, placed by an estimate, got "about 257 m west" where it had nothing before. **Same place** (within 150 m, looking different or at another time) and **Nearby** (150 m to 25 km, nearer first on a log scale). Momentum: a step between two located photos more than 150 m apart sets the walk's heading (the server returns it; the trail keeps it per step), and nearby places along it get up to +0.5 (cosine of the angle off it; "· keeps heading" within ~45°). Nearby branches sit at their real compass direction in the graph, north up (a small N marks it); the other branches share the free arcs. On the first library: the palace → "194 m south-west" → the old town (clock tower, an alley, the avenue); the sheep → "304 m south" → the other pastures.
- *Discover in the top bar,* next to Curate: `GET /api/discover/start?<filters>` picks a random photo within the filters, weighted exp(3 × recency rank) (the newest about 20 times as likely as the oldest), never a reject or below the quality floor unless nothing else is left.
- *Look:* the background is the last photo hovered (the centre until then), blurred 20 px (44 px hid too much), muted and darkened, cross-fading over 1.5 s; it drifts and zooms very slowly (40 s), and up to 3 soft glows sit on the photo's brightest areas (read from an 8×8 canvas of its thumbnail in the page), in their colours, pulsing slowly (screen blend). Only transform and opacity animate (GPU-composited; the blur is not redrawn per frame); nothing moves with "reduce motion". Labels are bent along an arc around each branch's first photo (SVG `textPath`), above or below it, whichever is further from the incoming edge (the curve's direction at the node, towards its control point); the arc is as wide as the longer line (shortened beyond ~150°). The branch's other photos go in the largest arc left free by the label and the edge, so text and circles never cross. Branches float a few pixels, each at its own pace. Hovering a photo grows it to a large circle (2.5× the branch photo, up to 270 px, the 1600 px preview) where it is; other branches it would cover are pushed away along the line between them (labels with them, kept on screen), its own branch's other photos move out, and a grown first photo widens its label arc. Positions and sizes animate over 0.25 s; it shrinks back 0.12 s after the pointer leaves.

*Accents* (28 Sep 2026): two lenses on the centre's strongest accent (strength ≥ 0.35, §7.6). **Accent echo**: photos with an accent of the same hue (within ±30°), otherwise different; a louder accent than the centre's counts no more (else illustrations won). **Accent takes over**: photos whose colour is mostly that hue (the red balloon → a red sunset); left out when the hue is already the centre's main colour (the colour echo covers it). Both penalise a photo ↔ illustration jump by 0.6 instead of 0.25: on the 13,468-image library (11,300 illustrations, far more saturated), the branches from photos were first almost all illustrations. After: a street with red awnings → the red dress of a figurine, a red flower; the Riksdag arch's orange accent → sheep with orange ear tags; its "takes over" → a spider on tan, a gilded ceiling, yellow flowers.

**Kept** (28 Sep 2026): after walking it, the user judged that it turned out well and fits the app. Documented in the README and the in-app guide.

**The layout fingerprint in Curate (tried, not adopted).** A slider from contrasting to similar compositions (neutral in the middle), drafting 12 photos from two folders of the first library:
- *Similar to the photos chosen so far* (mean layout correlation): Sweden became a visible series of horizontal water-and-sky views (mean pairwise correlation 0.00 → 0.28). But April mixes two composition families (centred close-ups, horizon views), and there it pulled in a near-copy and an unrelated dark field. Damping candidates redundant in content stopped the near-copies but left April unchanged.
- *Similar to the first pick* (the anchor): hardly visible in either folder.
- *Contrasting* (no composition twice: the highest correlation with any chosen photo): 3–4 of 12 photos swapped, a mild effect, since a varied draft rarely repeats a composition anyway.

A 12×12 luminance grid is too crude to steer a selection across mixed material; it only works when one composition dominates. Not reliable enough to offer (the user's condition), so Curate is unchanged. Ordering a draft by flow (echoes between neighbours) was judged not worth it.

## 15. Candidates

Roughly in order of value:

1. Named collections ("Print", "Photo book") next to the global pick/reject, each with its own export; saving a Curate draft as one. Optionally star ratings.
2. Manual tag add/remove, stored with the flags.
3. Finer location "spots" within a town: clustered visit positions (~200 m), labelled with the town, and "Home" / "Work" where the timeline marks them.
4. Comparing models on the §14 queries: SigLIP SO400M, or a multilingual model. SigLIP needs `softmax_scale` and the stack threshold retuned.
5. Per-camera clock correction. Location matching depends on it: a camera 10 minutes off places photos along the wrong stretch of a route.
6. Curate styles and colour measures as grid sorts or filters.
7. "More like my picks" search (the taste model's weight vector as a query).
8. ONNX Runtime inference: smaller releases, and GPU support via DirectML (Windows) and CoreML (macOS).
9. Deeper image features (the penultimate CLIP layer, mean-centred) for find similar and your tags. They weren't better on everyday concepts (§14.1), but could be for individuals or characters, where the text-aligned embedding may blur one member of a kind into the others. Test first on a labelled set of such identities, then weigh it against the cost of a second embedding.
10. People and pets: detect and group individuals (faces with a dedicated recognition model; pets via animal detection and crop embeddings). See §15.1.
11. Mean-centring the current embedding for find similar and your tags: a small, free gain in §14.1, with the thresholds retuned.
12. Captions and OCR through a local vision-language model.
13. Flags as XMP sidecars in the export folder, for Lightroom and darktable.

### 15.1 People and pets (design notes, not started)

Grouping photos by individual (a person, a pet) is beyond CLIP. It is trained to match whole images to captions, so it encodes "a man with glasses on a street", not which man: two people in similar settings come out closer than one person in two settings. Identity needs models trained for it, applied to crops.

**People: face detection and face recognition** (the approach of Immich, digiKam, and PhotoPrism).

1. Detect faces in each preview (boxes and landmarks); skip tiny or blurred ones.
2. Embed each face with a recognition model, which maps the same person to nearby vectors across age, lighting, and angle.
3. Cluster the face vectors into likely individuals.
4. The user names clusters, merges them, and marks "not this person". New photos are assigned to named people, and uncertain matches are offered for confirmation.

Model licences matter, because the release builds ship the models:

| Option | Notes |
|---|---|
| InsightFace (e.g. `buffalo_l`, used by Immich) | Best accuracy; weights are non-commercial / research only, so not for the releases |
| OpenCV YuNet (detector) + SFace (recogniser) | Good accuracy, small ONNX models, fast on CPU; Apache 2.0. The preferred start, run through ONNX Runtime or OpenCV DNN |
| dlib / `face_recognition` | Permissive but older; weaker on non-frontal faces; heavy dependency |

**Pets: experimental.** There is no animal counterpart to face recognition. The practical route:

1. Detect animals with a general object detector (COCO classes: cats, dogs, horses…). Use a permissively licensed one, such as torchvision's detectors or the DETR family; Ultralytics YOLO is AGPL.
2. Embed each crop with a self-supervised model such as DINOv2 (Apache 2.0), which keeps fine detail like fur pattern and markings better than CLIP. This is the "individuals" case of §14.1.
3. Teach an individual by example through the your-tags mechanism (§10), using crop embeddings, instead of clustering automatically.

Distinctive animals should work; look-alikes (two black labradors) will be confused.

**Fitting it in:**

- **Index:** an optional step (people first, pets later). Face and animal boxes and vectors are derived data.
- **User data:** names, merges, and confirmations go in `selections.sqlite3`, keyed by photo content hash and box, so they survive re-indexing.
- **UI:**
  - a People section in the sidebar that filters like tags, with AND for "photos with both";
  - a page of unnamed clusters to name, merge, or split;
  - face chips in the photo view;
  - optionally, a Curate term that spreads a draft over people.
- **Privacy:** face vectors are biometric data. Everything stays local, the step is opt-in in Settings, and a "forget all face data" action deletes the vectors and names.
- **Cost:** roughly 10–30 ms per photo on a CPU for detection and embedding (a couple of minutes per 2,000 photos), far less on a GPU. Adds ONNX Runtime (or OpenCV), which §4 currently avoids.

**First step when picked up:** a feasibility check on the first library. How many usable faces are there (size, angle), and do the clusters make sense? That shows whether trip photos contain enough repeat individuals to be worth it.

Location-history questions still open: how accurate is the history on photos that also have GPS (the first library has none), and how to handle several phones, or a missing phone for part of a trip.

## 16. Decisions

1. Development machine: Linux with an NVIDIA RTX 3090 (CUDA).
2. Formats: JPEG, PNG, and TIFF (test data includes large 16-bit PNGs). HEIC is an optional extra (`.[heic]`).
3. RAW search dirs default to `.`, `RAW`, and `../RAW`, relative to each image's folder.
4. Default model: `ViT-L-14-quickgelu` / `dfn2b` since 26 Sep 2026 (about 81 % ImageNet zero-shot), replacing `ViT-B-16` / `laion2b_s34b_b88k` (70 %). Alternatives: `ViT-B-16` / `dfn2b` (76 %, the standalone default) and `ViT-H-14-quickgelu` / `dfn5b` (83 %). The 1,146-photo library embeds in under a minute on the RTX 3090.
5. Flags: one global pick/reject per photo, in a separate database keyed by content hash.
6. Location history: referenced in place (not copied), placed at place / region / country level, with route-interpolated positions used but marked.
7. Taste model: calibrated on request, not retrained in the background.
8. Standalone builds: zipped folders, not single files; CPU inference with the faster model.
9. Discover (§14.7) is kept as a feature, next to Curate: tried on a branch, judged a good fit after walking it.
