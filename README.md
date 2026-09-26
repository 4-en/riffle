# Riffle

*Riffle through your photos and keep the best.*

A local tool for finding the best photos in a large archive and exporting them for editing, sharing, or printing. You search and browse by what is in the pictures, then cull: pick or reject photos quickly, compare bursts side by side, and copy the picks (and their RAWs) to a new folder. Search uses CLIP embeddings; grouping uses zero-shot subject, scene, and look tags. There's no training, no labelling, and nothing leaves your machine.

> **Status:** MVP implemented; evaluation pending. See [`development_plan.md`](development_plan.md) for the design.

## Features

- **Culling:** pick or reject photos one at a time (loupe with auto-advance) or many at once (multi-select), with undo
- **Stacks:** bursts and near-identical shots are grouped automatically; compare them side by side with a sharpness hint, exposure clipping, and a suggested keeper
- **Export:** copy the picks to a new folder as images, images + RAWs, or RAWs only; originals are never touched
- Text search ("red lanterns at night") and "find similar" from any photo
- Subject, scene, and look tags from an editable vocabulary file, with alternative phrases per tag
- Tag sidebar that narrows with your filter: only tags present in the current selection are shown, with counts. Click a tag to show only photos with it; hover and click **−** (or Alt+click) to hide photos with it instead
- Duplicate grouping via perceptual hash
- RAW files tracked by matching filenames (never read or modified)
- EXIF: date, camera, lens, focal length, aperture, shutter speed, ISO, GPS
- Filters for flag, date range, camera, lens, focal length, aperture, ISO, orientation, and location; like tags, options are counted within the other active filters, and filters that can't narrow the results are hidden
- Timeline view: group the grid by day, month, or year (only groups with matching photos appear), jump between groups, and go from any photo to its day
- **Overviews:** a calendar (for date groupings) and a map (for location groupings) of where your photos are; click a day, month, or place to open it in the grid
- **Location from your phone:** add a Google Timeline export (or Records.json / GPX) and photos without GPS are placed by capture time; group by place, region, or country, filter by where you were, and optionally write the position into exported copies
- Simple local web UI, including adding photo folders and running indexing from the browser

Your originals are never modified. All derived data lives in a cache folder and can be deleted and regenerated at any time. Your pick/reject flags are the one thing you create by hand: they live separately in `selections.sqlite3`, survive deleting the cache, re-indexing, and moving files, and are worth backing up. See [Where things live](#where-things-live).

## Planned

Next is a short evaluation of search and tag quality (see `development_plan.md` §13). Other candidates, such as per-camera clock correction (which also sharpens location matching), finer "spots" within a town, and a map, are in `development_plan.md` §14.

## Requirements

- Python 3.12+
- Node.js 20.19+ or 22.12+ (to build the UI)
- A GPU or Apple Silicon is recommended but not required
- About 1.7 GB of disk for the default CLIP model weights, downloaded on first use (see [Model](#model) for a smaller one)

## Setup

```sh
python3.12 -m venv venv              # or reuse the existing venv/
venv/bin/pip install -e ".[dev]"     # for HEIC support: ".[dev,heic]"
cd web && npm install && npm run build && cd ..   # builds the UI into src/riffle/web/
```

The first run creates your settings (`~/.config/riffle/config.yaml` on Linux); add photo folders in the UI's **Library**, or edit that file.

## Usage

```sh
venv/bin/riffle          # starts Riffle and opens it in your browser
```

Run it again while it is running and it just opens another browser tab. It uses port 8000, or a free one if that is taken. It stops by itself about 30 seconds after you close its last browser tab (a reload is fine, and it never stops while indexing or exporting), or press Ctrl+C. If you come back to a tab after it stopped, the page says so and reconnects once you run `riffle` again. `riffle serve` starts the server without opening a browser and keeps running until stopped (http://localhost:8000; `--host/--port/--reload`), and `python -m riffle` works like `riffle`.

In the UI, open **Library** (top right) to browse the disk, add or remove photo folders, and run indexing in the background with progress. It opens by itself when no folders are configured. Added folders are saved to `sources` in `config.yaml`.

Indexing can also run from the command line:

```sh
venv/bin/riffle index    # scan, thumbnail, embed, tag, group duplicates and stacks (incremental)
```

The server picks up new embeddings without a restart. Other options: `riffle --config PATH ...` and `riffle -v ...` for debug logging.

The folder browser can list any directory the server can read, so keep `serve` bound to `127.0.0.1` (the default).

## Culling and export

A typical pass:

1. Turn on **Stacks** (top bar) so each burst shows as one tile, and **Hide rejected**.
2. **Review stacks** walks through every stack that still has unflagged photos. Click (or press 1–9) the keeper(s), then Enter: they are picked and the rest rejected. The sharpness bar marks the sharpest shot, ▲/▼ warn about blown highlights and crushed shadows, and **★ suggested** marks the likely keeper (sharpness, clipping, and a CLIP "good photo vs. bad photo" score, each relative to the others; hover it for the reasons). A keeps the suggestion; Z zooms all photos to the same spot to check focus.
3. Go through the rest in the grid or the loupe with P (pick), X (reject), U (unflag). In the loupe, the next photo comes up automatically.
4. Filter by **Flag → Picked** to check the selection, then **Export** (top bar): choose the folder, the files (Images / Images + RAWs / RAWs only), and the layout (flat, or keeping the source folders).

To start over, the compare view's **Unflag all** resets the stack you're looking at, and the **Flags** section at the bottom of **Library** unflags every photo (or only those in the current filters). Both can be undone with Ctrl+Z.

With a location history, the export can also **add the location to photos without GPS** (on by default). It goes into the exported JPEG and PNG copies only, appended to their EXIF so nothing already in the file moves or changes (image data, MakerNote, embedded thumbnail). RAW and other files are never modified; they get an `.xmp` sidecar with the position instead, which Lightroom, Capture One, and darktable read.

Riffle remembers which photos you have exported: they get a small ↗ badge (alongside their pick or reject flag), the **Exported** filter shows them or hides them, and the photo view says when and where they last went. In the export dialog, **Only photos not exported before** exports just the picks that are new since last time. The history lives with your flags in `selections.sqlite3`; **Library → Forget export history** clears it (the exported files are not touched).

Exports never overwrite anything: identical files already in the destination are skipped (so an interrupted export can simply be run again), and other name clashes get a `-1` suffix, with an image and its RAW keeping matching names. Each export writes an `export-manifest.csv`. The destination can't be inside a photo folder, where the copies would be indexed again.

Stacks link photos taken at most `stacks.max_gap_seconds` apart that look alike (`stacks.min_similarity`) in `config.yaml`; adjust and re-run `riffle index` (fast, no re-embedding) if they are too eager or too strict. The right `min_similarity` depends on the model (see [Model](#model)).

### Keyboard

| Where | Keys |
|---|---|
| Anywhere | `?` how-to guide · `O` calendar / map overview (with a grouping) · `/` search · `Ctrl+Z` undo the last flag change · `Esc` close / clear selection |
| Grid | right-click menu (pick / reject, compare, find similar, show day / place, copy path, filter by its tags) · click select · `Ctrl`/`Shift`+click add / range · drag to select · arrows move (`Shift` extends) · `Ctrl+A` select all · `Enter` or double-click open · `P` / `X` / `U` flag the selection · `C` compare the selection · `S` stacks · `H` hide rejected · `R` review stacks |
| Loupe | `←` / `→` previous / next · `P` / `X` / `U` flag (and go to the next photo) |
| Compare | click or `1`–`9` keep · `A` keep the suggested one · `Enter` pick kept, reject rest (with nothing kept: press twice to reject all) · `Shift+X` reject all · `Shift+U` unflag all · `P` / `X` / `U` flag the focused photo · arrows focus · `Z` zoom · `N` / `B` next / back (review) |

## Location

Camera files rarely have GPS, but your phone usually knew where you were. In **Library → Location history**, add an export of your location history:

- **Google Timeline** (current format): on an Android phone, *Settings* → *Location* → *Timeline* → *Export Timeline data*, then copy the exported `Timeline.json` to this computer. (Depending on the Android and Google Maps version, the same export may also be reachable from Google Maps' Timeline settings.)
- Older **Google Takeout** `Records.json`, or **GPX** tracks from a logging app.

The file stays where it is and is only read (its path is saved in `config.yaml` under `location_history`). Each photo's capture time, made absolute with its EXIF time zone (or the timeline's own for that day), is matched against the history:

| Source | When | Typical accuracy |
|---|---|---|
| Camera GPS | the photo has GPS in its EXIF | a few metres |
| Timeline: stayed | the phone recorded a visit at that time | ~50 m |
| Timeline: on the move | taken while travelling; interpolated along the route | tens of metres to a few km |
| Timeline: nearby | a recorded position within 15 minutes | depends on the gap |

With a location grouping, **Map** (top bar, or `O`) shows your photos as clusters on a world map; hover for a cover photo, click to open the place in the grid. The outlines are Natural Earth country borders bundled with the app, so the map works offline and never contacts a map server; it is an overview, not a street map. With a date grouping, the same button shows a **Calendar**: a year of months where each day with photos shows a cover and a count.

Places are named offline from a bundled GeoNames dataset: the nearest town or village, its region, and its country. Group the grid by **Place / Region / Country** (groups are in trip order), filter by country, place, or source in the sidebar, and use **Show place** in the photo view. `location.max_gap_minutes` and `location.min_population` in `config.yaml` tune matching and naming.

Photos are matched by the camera's clock, so a camera set to the wrong time places photos wrongly. Location history is sensitive: it never leaves your machine, the derived positions live in the cache folder, and nothing is written into your originals. Keep the export out of any shared or synced folder (the repository's `.gitignore` excludes `Timeline.json`, `Records.json`, and `*.gpx`).

## Model

The CLIP model drives search, tags, stacks, and the quality hint. Set it under `model:` in `config.yaml`:

| Option | `name` / `pretrained` | When |
|---|---|---|
| **Default** | `ViT-L-14-quickgelu` / `dfn2b` | Good search and tags. Fast on a GPU; indexing on a laptop CPU is slow (a few photos per second), but only happens once per photo. |
| **Faster** | `ViT-B-16` / `dfn2b` | About 4× faster indexing and ~0.6 GB. A good choice for laptops; somewhat looser search and tags. |
| **Max quality** | `ViT-H-14-quickgelu` / `dfn5b` | The best search and tags. About 2.5× slower to index than the default and ~4 GB of memory. |

Each model keeps its own embeddings and tags, so switching back and forth loses nothing; the first `riffle index` with a new model embeds every photo once. Similarity values differ between models, so after switching also set `stacks.min_similarity` (0.92 for the default, about 0.90 for ViT-B-16) and check the stacks.

## Tags

Tags live in `vocabulary.yaml` (next to your config; created from the default on first run), grouped into families (`subject`, `scene`, `look`). An entry is a plain name, or a name with alternative phrases:

```yaml
subject:
  - boats
  - cats: [a cat, a kitten, a cat sleeping]
```

A photo scores a tag by its best-matching phrase. Tags within a family compete through a softmax, so keep them from overlapping (prefer "cats", "dogs", "birds" over a general "animals"). Thresholds per family (`min_prob`, `max_tags`) are in `config.yaml`. After editing either file, run:

```sh
venv/bin/riffle tag      # re-tags using stored embeddings; takes seconds
```

## Development

Run the API and the Vite dev server side by side; Vite proxies `/api`, `/thumbs` and `/previews` to port 8000.

```sh
venv/bin/riffle serve --reload
cd web && npm run dev
```

Without the dev server, run `npm run build` after changing anything in `web/src/`. It writes the built UI into `src/riffle/web/`, which the server serves and which ships inside the Python package (build it before `pip wheel` / `python -m build`).

Run tests with `venv/bin/pytest`. They use synthetic images and a fake encoder, so no model download is needed.

## Where things live

Following each platform's conventions (`riffle paths` prints the exact locations):

| What | Linux | Notes |
|---|---|---|
| Settings: `config.yaml`, `vocabulary.yaml` | `~/.config/riffle/` | Created on first run from the defaults in `src/riffle/defaults/` |
| Your flags: `selections.sqlite3` | `~/.local/share/riffle/` | User data: back it up |
| Derived data: catalogue, thumbnails, previews, embeddings | `~/.cache/riffle/` | Safe to delete; `riffle index` rebuilds it (re-embedding takes a while) |
| CLIP model weights | `~/.cache/huggingface/` | Downloaded once |

`$XDG_CONFIG_HOME`, `$XDG_DATA_HOME`, and `$XDG_CACHE_HOME` are honoured; macOS uses `~/Library/Application Support` and `~/Library/Caches`, Windows `%APPDATA%` and `%LOCALAPPDATA%`. To use a different config, pass `--config PATH` or set `RIFFLE_CONFIG`; a `config.yaml` in the current directory is also picked up, for a self-contained setup. `data_dir`, `selections`, and `vocabulary` in the config override the locations.

## Layout

```text
src/riffle/            indexer, CLI and API server
src/riffle/defaults/   default config.yaml and vocabulary.yaml
web/                    Svelte + Tailwind UI (Vite, no SvelteKit)
tests/
```
