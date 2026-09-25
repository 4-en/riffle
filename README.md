# Photo Archive

A local tool for searching and browsing a large photo archive by what is in the pictures. It uses CLIP embeddings for text and similar-image search, and zero-shot subject and scene tags for grouping. There's no training, no labelling, and nothing leaves your machine.

> **Status:** early development. See [`development_plan`](development_plan) for the design.

## Features

- Text search ("red lanterns at night") and "find similar" from any photo
- Subject and scene tags from an editable vocabulary file
- Duplicate grouping via perceptual hash
- RAW files tracked by matching filenames (never read or modified)
- Basic EXIF: date, camera, lens, GPS
- Simple local web UI

Your originals are never modified. All derived data lives in `data/` and can be deleted and regenerated at any time.

## Requirements

- Python 3.12+
- Node.js 20+ (to build the UI)
- A GPU or Apple Silicon is recommended but not required

## Setup

```sh
python3.12 -m venv venv            # or reuse the existing venv/
venv/bin/pip install -e ".[dev]"   # add ,heic for HEIC support: ".[dev,heic]"
cd web && npm install && npm run build && cd ..
cp config.example.yaml config.yaml   # then set your photo folders
```

## Usage

```sh
venv/bin/archive index    # scan, thumbnail, embed, tag, and group duplicates (incremental)
venv/bin/archive serve    # open http://localhost:8000
```

You can also manage folders from the UI: **Library** (top right) lets you browse the disk, add or remove photo folders, and run indexing in the background with progress. Added folders are saved to `sources` in `config.yaml`. The folder browser can list any directory the server can read, so keep `serve` bound to `127.0.0.1` (the default).

To change tags, edit `vocabulary.yaml` and thresholds in `config.yaml`, then run:

```sh
venv/bin/archive tag      # re-tags using stored embeddings; takes seconds
```

## Development

Run the API and the Vite dev server side by side; Vite proxies `/api`, `/thumbs` and `/previews` to port 8000.

```sh
venv/bin/archive serve --reload
cd web && npm run dev
```

Run tests with `venv/bin/pytest`. They use synthetic images and a fake encoder, so no model download is needed.

## Layout

```text
config.yaml        source folders, model, thresholds
vocabulary.yaml    subject and scene tag lists
data/              derived data (SQLite, thumbnails, embeddings); safe to delete
src/archive/       indexer, CLI and API server
web/               Svelte + Tailwind UI (Vite, no SvelteKit)
tests/
```
