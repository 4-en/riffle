# Photo Archive

A local tool for searching and browsing a large photo archive by what is in the pictures. It uses CLIP embeddings for text and similar-image search, and zero-shot subject and scene tags for grouping. There's no training, no labelling, and nothing leaves your machine.

> **Status:** early development. See [`China_Photo_Archive_MVP_Plan.md`](China_Photo_Archive_MVP_Plan.md) for the design.

## Features

- Text search ("red lanterns at night") and "find similar" from any photo
- Subject and scene tags from an editable vocabulary file
- Duplicate grouping via perceptual hash
- RAW files tracked by matching filenames (never read or modified)
- Basic EXIF: date, camera, lens, GPS
- Simple local web UI

Your originals are never modified. All derived data lives in `data/` and can be deleted and regenerated at any time.

## Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Node.js 20+ (to build the UI)
- A GPU or Apple Silicon is recommended but not required

## Setup

```sh
uv sync
cd web && npm install && npm run build && cd ..
cp config.example.yaml config.yaml   # then set your photo folders
```

## Usage

```sh
uv run archive index    # scan, thumbnail, embed, tag, and group duplicates (incremental)
uv run archive serve    # open http://localhost:8000
```

To change tags, edit `vocabulary.yaml` and thresholds in `config.yaml`, then run:

```sh
uv run archive tag      # re-tags using stored embeddings; takes seconds
```

## Development

Run the API and the Vite dev server side by side; Vite proxies `/api`, `/thumbs` and `/previews` to port 8000.

```sh
uv run archive serve --reload
cd web && npm run dev
```

Run tests with `uv run pytest`.

## Layout

```text
config.yaml        source folders, model, thresholds
vocabulary.yaml    subject and scene tag lists
data/              derived data (SQLite, thumbnails, embeddings); safe to delete
src/archive/       indexer, CLI and API server
web/               Svelte + Tailwind UI (Vite, no SvelteKit)
tests/
```
