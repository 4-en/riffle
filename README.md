# Photo Archive

A local tool for finding the best photos in a large archive and exporting them for editing, sharing, or printing. You search and browse by what is in the pictures, then cull: pick or reject photos quickly, compare bursts side by side, and copy the picks (and their RAWs) to a new folder. Search uses CLIP embeddings; grouping uses zero-shot subject, scene, and look tags. There's no training, no labelling, and nothing leaves your machine.

> **Status:** MVP implemented; evaluation pending. See [`development_plan`](development_plan) for the design.

## Features

- **Culling:** pick or reject photos one at a time (loupe with auto-advance) or many at once (multi-select), with undo
- **Stacks:** bursts and near-identical shots are grouped automatically; compare them side by side with a sharpness hint and keep the best
- **Export:** copy the picks to a new folder as images, images + RAWs, or RAWs only; originals are never touched
- Text search ("red lanterns at night") and "find similar" from any photo
- Subject, scene, and look tags from an editable vocabulary file, with alternative phrases per tag
- Tag sidebar that narrows with your filter: only tags present in the current selection are shown, with counts
- Duplicate grouping via perceptual hash
- RAW files tracked by matching filenames (never read or modified)
- EXIF: date, camera, lens, focal length, aperture, shutter speed, ISO, GPS
- Filters for date range, camera, lens, focal length, aperture, ISO, orientation, and GPS; like tags, options are counted within the other active filters, and filters that can't narrow the results are hidden
- Timeline view: group the grid by day, month, or year (only groups with matching photos appear), jump between groups, and go from any photo to its day
- Simple local web UI, including adding photo folders and running indexing from the browser

Your originals are never modified. All derived data lives in `data/` and can be deleted and regenerated at any time. Your pick/reject flags are the one thing you create by hand: they live separately in `selections.sqlite3` (next to `config.yaml`), survive deleting `data/`, re-indexing, and moving files, and are worth backing up.

## Planned

Next is a short evaluation of search and tag quality (see `development_plan` §13). After that, the most promising addition is **location from phone location history**: camera files rarely carry GPS, but your phone usually recorded where you were. Matching capture times against an exported history (Google Maps Timeline, or GPX tracks from a logging app) would place most photos automatically and enable location groups, place filters, and a map. It will work fully offline, and coordinates will never be written into your originals. Details and other candidates are in `development_plan` §14.

## Requirements

- Python 3.12+
- Node.js 20.19+ or 22.12+ (to build the UI)
- A GPU or Apple Silicon is recommended but not required
- About 600 MB of disk for the CLIP model weights, downloaded on first use

## Setup

```sh
python3.12 -m venv venv              # or reuse the existing venv/
venv/bin/pip install -e ".[dev]"     # for HEIC support: ".[dev,heic]"
cd web && npm install && npm run build && cd ..
cp config.example.yaml config.yaml   # then set your photo folders, or add them in the UI
```

## Usage

```sh
venv/bin/archive serve    # UI and API on http://localhost:8000
```

In the UI, open **Library** (top right) to browse the disk, add or remove photo folders, and run indexing in the background with progress. It opens by itself when no folders are configured. Added folders are saved to `sources` in `config.yaml`.

Indexing can also run from the command line:

```sh
venv/bin/archive index    # scan, thumbnail, embed, tag, group duplicates and stacks (incremental)
```

The server picks up new embeddings without a restart. Other options: `archive --config PATH ...`, `archive -v ...` for debug logging, and `archive serve --host/--port/--reload`.

The folder browser can list any directory the server can read, so keep `serve` bound to `127.0.0.1` (the default).

## Culling and export

A typical pass:

1. Turn on **Stacks** (top bar) so each burst shows as one tile, and **Hide rejected**.
2. **Review stacks** walks through every stack that still has unflagged photos. Click (or press 1–9) the keeper(s), then Enter: they are picked and the rest rejected. The sharpness bar marks the sharpest shot; Z zooms all photos to the same spot to check focus.
3. Go through the rest in the grid or the loupe with P (pick), X (reject), U (unflag). In the loupe, the next photo comes up automatically.
4. Filter by **Flag → Picked** to check the selection, then **Export** (top bar): choose the folder, the files (Images / Images + RAWs / RAWs only), and the layout (flat, or keeping the source folders).

Exports never overwrite anything: identical files already in the destination are skipped (so an interrupted export can simply be run again), and other name clashes get a `-1` suffix, with an image and its RAW keeping matching names. Each export writes an `export-manifest.csv`. The destination can't be inside a photo folder, where the copies would be indexed again.

Stacks link photos taken at most `stacks.max_gap_seconds` apart that look alike (`stacks.min_similarity`) in `config.yaml`; adjust and re-run `archive index` (fast, no re-embedding) if they are too eager or too strict.

### Keyboard

| Where | Keys |
|---|---|
| Anywhere | `/` search · `Ctrl+Z` undo the last flag change · `Esc` close / clear selection |
| Grid | click select · `Ctrl`/`Shift`+click add / range · drag to select · arrows move (`Shift` extends) · `Ctrl+A` select all · `Enter` or double-click open · `P` / `X` / `U` flag the selection · `C` compare the selection · `S` stacks · `H` hide rejected · `R` review stacks |
| Loupe | `←` / `→` previous / next · `P` / `X` / `U` flag (and go to the next photo) |
| Compare | click or `1`–`9` keep · `Enter` pick kept, reject rest · `Shift+X` reject all · `P` / `X` / `U` flag the focused photo · arrows focus · `Z` zoom · `N` / `B` next / back (review) |

## Tags

Tags live in `vocabulary.yaml`, grouped into families (`subject`, `scene`, `look`). An entry is a plain name, or a name with alternative phrases:

```yaml
subject:
  - boats
  - cats: [a cat, a kitten, a cat sleeping]
```

A photo scores a tag by its best-matching phrase. Tags within a family compete through a softmax, so keep them from overlapping (prefer "cats", "dogs", "birds" over a general "animals"). Thresholds per family (`min_prob`, `max_tags`) are in `config.yaml`. After editing either file, run:

```sh
venv/bin/archive tag      # re-tags using stored embeddings; takes seconds
```

## Development

Run the API and the Vite dev server side by side; Vite proxies `/api`, `/thumbs` and `/previews` to port 8000.

```sh
venv/bin/archive serve --reload
cd web && npm run dev
```

Without the dev server, run `npm run build` after changing anything in `web/src/`; `archive serve` serves the built files from `web/dist`.

Run tests with `venv/bin/pytest`. They use synthetic images and a fake encoder, so no model download is needed.

## Layout

```text
config.yaml        source folders, model, thresholds (from config.example.yaml)
vocabulary.yaml    tag families, tags, and alternative phrases
selections.sqlite3 your pick/reject flags (user data; back it up)
data/              derived data (SQLite, thumbnails, embeddings); safe to delete
src/archive/       indexer, CLI and API server
web/               Svelte + Tailwind UI (Vite, no SvelteKit)
tests/
```
