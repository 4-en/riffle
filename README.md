# Photo Archive

A local tool for searching and browsing a large photo archive by what is in the pictures. It uses CLIP embeddings for text and similar-image search, and zero-shot subject, scene, and look tags for grouping. There's no training, no labelling, and nothing leaves your machine.

> **Status:** MVP implemented; evaluation pending. See [`development_plan`](development_plan) for the design.

## Features

- Text search ("red lanterns at night") and "find similar" from any photo
- Subject, scene, and look tags from an editable vocabulary file, with alternative phrases per tag
- Tag sidebar that narrows with your filter: only tags present in the current selection are shown, with counts
- Duplicate grouping via perceptual hash
- RAW files tracked by matching filenames (never read or modified)
- Basic EXIF: date, camera, lens, GPS
- Simple local web UI, including adding photo folders and running indexing from the browser

Your originals are never modified. All derived data lives in `data/` and can be deleted and regenerated at any time.

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
venv/bin/archive index    # scan, thumbnail, embed, tag, and group duplicates (incremental)
```

The server picks up new embeddings without a restart. Other options: `archive --config PATH ...`, `archive -v ...` for debug logging, and `archive serve --host/--port/--reload`.

The folder browser can list any directory the server can read, so keep `serve` bound to `127.0.0.1` (the default).

### Keyboard

`/` focuses search, `←`/`→` move between photos in the detail view, and `Esc` closes the detail view or dialog.

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
data/              derived data (SQLite, thumbnails, embeddings); safe to delete
src/archive/       indexer, CLI and API server
web/               Svelte + Tailwind UI (Vite, no SvelteKit)
tests/
```
