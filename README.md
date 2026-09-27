# Riffle

A local tool for finding the best photos in a large archive and exporting them for editing, sharing, or printing.

You search and browse by image content, cull by picking and rejecting photos, compare bursts side by side, and copy the picks (with their RAWs, if you like) to a new folder. Search and tags use CLIP embeddings. Nothing is trained on your behalf, nothing needs labelling, and nothing leaves your machine.

## Features

- **Search** by text (`red lanterns at night`) or by example ("find similar"). Exclude terms with a minus (`street -people`), match either of several with `|` (`beach | lake`).
- **Tags** for subject, scene, and look, from an editable vocabulary with alternative phrases per tag. Click a tag to include it, Alt+click (or hover and click **−**) to exclude it.
- **Your tags**: select a few photos, right-click → **Create tag**, and photos like them get the tag. Works like the other tags in the sidebar and in every filter.
- **Filters** for flag, folder, date, camera, lens, focal length, aperture, ISO, orientation, and location. Counts reflect the other active filters; filters that can't narrow the result are hidden.
- **Grouping** by day, month, year, place, region, country, or folder, with a calendar or map overview.
- **Culling**: pick or reject one photo at a time in the loupe or many at once in the grid, with undo.
- **Stacks**: bursts and near-identical shots are grouped automatically and compared side by side, with sharpness, clipping warnings, and a suggested keeper.
- **Taste model**: once you have flagged enough, Riffle can sort by what you tend to keep. It only orders photos; it never flags them.
- **Curate**: drafts a small, varied selection (photo book, exhibition) from the current filters, steered by size, variety, time and place spread, search, colour, and style.
- **Export** of picks as images, images + RAWs, or RAWs only. Copies only; originals are never touched.
- **Location from your phone**: a Google Timeline, Records.json, or GPX export places photos without GPS, and can add the position to exported copies.
- Duplicate detection (perceptual hash), RAW matching by file name, EXIF metadata.

Originals are read-only. Everything Riffle derives lives in a cache folder and can be deleted and rebuilt. The one thing you create by hand, your pick/reject flags, lives separately in `selections.sqlite3`; it survives re-indexing, deleting the cache, and moving files. Back it up.

## Install

### Download

Builds for Windows, macOS (Apple silicon), and Linux are on the [Releases](https://github.com/4-en/riffle/releases) page. No Python needed: unpack and start `Riffle` (`Riffle.exe`, `Riffle.app`). It opens in your browser and has no window of its own.

- The first start downloads the CLIP model (about 0.6 GB). You can browse meanwhile; search and indexing start working once it's ready.
- About 300 MB to download, 1 GB unpacked.
- The Windows and Linux builds run the model on the CPU (macOS also uses the GPU), so they default to the faster ViT-B-16 model: about 11 photos per second on a desktop CPU. For an NVIDIA GPU, install from source instead.
- The builds are unsigned. On macOS, right-click → **Open**, or System Settings → Privacy & Security → **Open Anyway**, or `xattr -dr com.apple.quarantine Riffle.app`. On Windows, SmartScreen → **More info** → **Run anyway**.
- Problems are logged to `riffle.log` in the cache folder (see [Files](#files)).
- On macOS, opening the app again while it runs does nothing; keep the tab or bookmark it.
- The command-line subcommands work with the executable too, but their output goes to the log. Use a source install for command-line work.

### From source

Requires Python 3.12+ and Node.js 20.19+ or 22.12+. A GPU or Apple silicon helps but isn't required.

```sh
python3.12 -m venv venv
venv/bin/pip install -e ".[dev]"                  # HEIC support: ".[dev,heic]"
cd web && npm install && npm run build && cd ..   # builds the UI into src/riffle/web/
```

The default model (ViT-L-14) downloads on first use, about 1.7 GB.

## Usage

```sh
venv/bin/riffle            # start and open in the browser
venv/bin/riffle serve      # server only, http://127.0.0.1:8000 (--host, --port, --reload)
venv/bin/riffle index      # index from the command line (incremental)
venv/bin/riffle tag        # re-tag from stored embeddings after editing the vocabulary
venv/bin/riffle paths      # show where config, flags, and derived data live
```

`riffle` uses port 8000, or a free port if that is taken. Running it again while it runs opens another tab. It stops by itself 30 seconds after the last tab is closed; it never stops in its first minute or during indexing or export. `riffle serve` runs until stopped. Global options: `--config PATH`, `-v` for debug logging.

Open **Library** (top right) to add photo folders and run indexing. It opens by itself on first start. Folders are saved to `sources` in `config.yaml`.

The folder browser can list any directory the server can read, so keep the server bound to `127.0.0.1` (the default).

## Culling and export

A typical pass:

1. Tick **Stacks** above the grid so each burst shows as one tile. Hide rejects with **Flag → Picked + Unflagged** in the sidebar, or `H`.
2. **Review stacks** walks through every stack that still has unflagged photos. Mark the keeper(s) by clicking or with 1–9, then Enter: they are picked and the rest rejected.
   - The sharpness bar marks the sharpest shot.
   - ▲/▼ warn about blown highlights and crushed shadows.
   - **★ suggested** marks the likely keeper, based on sharpness, clipping, and a CLIP quality score; hover it for the reasons. `A` keeps it, and `Z` zooms all photos to the same spot to check focus.
3. Go through the rest in the grid or the loupe with `P` (pick), `X` (reject), `U` (unflag). The loupe advances automatically.
4. Check the selection with **Flag → Picked**, then **Export**. Choose the destination, the files (images, images + RAWs, RAWs only), and the layout (flat or keeping the source folders).

Undo any flag change with Ctrl+Z. **Unflag all** in the compare view resets a stack; **Library → Flags** unflags everything or the current filters.

Exports never overwrite anything:

- Identical files already in the destination are skipped, so an interrupted export can simply be rerun.
- Other name clashes get a `-1` suffix; an image and its RAW keep matching names.
- Each export writes `export-manifest.csv`.
- The destination can't be inside a photo folder.

Exported photos get a ↗ badge and can be filtered. **Only photos not exported before** in the export dialog skips them. **Library → Forget export history** clears that record; the exported files stay.

With a location history configured, exports add the position to photos without GPS (on by default):

- JPEG and PNG copies get it appended to their EXIF, so nothing else in the file changes.
- RAWs and other files get an `.xmp` sidecar instead.

Stacks link photos at most `stacks.max_gap_seconds` apart that are at least `stacks.min_similarity` alike. If they're too eager or too strict, adjust these and rerun `riffle index`; it doesn't re-embed. The right similarity depends on the model (see [Model](#model)).

## Taste model

**Library → Your taste → Calibrate** trains a small model on your flags; it takes a second or two. It needs at least 20 scenes with a pick (or an export) and 50 fully rejected scenes, and it must clearly beat chance on photos it wasn't trained on.

Once calibrated, **Sort → Likely keepers first** shows promising photos first, and **Likely rejects first** helps clear out misses. It rates scenes (a burst counts once), so this sort also turns on Stacks. Picking the best frame within a burst is left to sharpness and the suggested keeper.

The Library shows how well it works for you and how many flags changed since the last calibration. The model is stored in the cache folder and never flags anything by itself.

## Curate

Filter down to a trip, a timeframe, or a place, then press **Curate** for a draft of a small selection: good photos, but not ten of the same scene, moment, or spot.

- **Candidates**: picks and unflagged photos within the filters; a switch adds rejects. Each stack contributes one photo: its pick, or its best frame.
- **Quality**: your taste model (once calibrated), the CLIP quality score, exposure, and a bonus for picks.
- **Size**: 6, 12, 24, 48, or anything from 2 to 60.
- **Best ↔ Most varied**, **Spread over time**, **Spread over places** (uses coordinates when there are any).
- **Search**: leans the draft towards a text search, with the same syntax as the main search. It scores rather than filters, so other photos can still fill the draft. A search on the main page carries over when you open Curate.
- **Colour & light**: colour swatches (red, orange, yellow, green, teal, blue, purple, pink) and Dark / Bright, Soft / Punchy, Muted / Vivid. These use per-photo pixel statistics computed during indexing. A library indexed before this feature needs one **Index now**, about 30 s per 2,000 photos.
- **Style**: sliders for Scenic, Moody, Calm, Colourful, Golden light, People, and Abstract & details. They are CLIP prompt pairs under `styles:` in `vocabulary.yaml`, which you can edit or extend.

The draft is laid out as one block in capture order: rows of equal height that fill the width, without cropping. On hover, each photo shows why it was chosen and three actions:

- **✕** removes it for this draft; it is not rejected, and the next candidate takes its place.
- **Lock** keeps it when the settings change.
- **Alternatives** offers other frames from its stack and similar photos.

**Mark as picks** flags the draft (undo with Ctrl+Z); **Export** copies exactly these photos. The draft is remembered per filter set in the browser, and **Reset draft** starts over.

## Your tags

Tags taught by example: select one or more photos, right-click → **Create tag from N photos…** (or **Tag…** in the selection bar), and name it. A photo gets the tag when it is close enough to any one of the examples, so varied examples (the same dog on a beach, in snow, indoors) each count. With a single example, the tag holds that photo's closest matches, like "find similar".

- **Strict · Normal · Loose** sets how close is close enough. The dialog shows the photo count for each, and the photos at the edge of the tag.
- **Your tags** in the sidebar work like the other tags: click to include, Alt+click or **−** to exclude, **✎** to rename, change strictness, remove examples, or delete.
- To add examples, select photos, right-click, and pick the tag under **Add to a tag**.
- Tags are stored with your flags in `selections.sqlite3`, by file content, so they survive re-indexing, moved files, and model changes.

## Search syntax

| Query | Meaning |
|---|---|
| `boats at sunset` | photos matching the phrase |
| `street -people` | subtracts "people" from the search, which pushes photos with people down |
| `harbour -"cruise ships"` | quotes exclude a phrase |
| `beach \| lake` | either; each photo scores by its better match |
| `-people` | the photos least like "people" first |

Search ranks the whole filtered set; it doesn't cut off results. Hyphenated words (`black-and-white`) are left alone.

With **Names** on (the switch at the right end of the search box, on by default), photos whose file name contains all search words come first, then those whose folder does, each group in image order, with a small badge. Matching is by whole words (`cat` matches `cat_01.jpg`, not `catalogue.jpg`; a plural "s" is allowed), filler words like "at" and "the" are ignored, and camera names such as `IMG_4711` or `DSCF0012` contain no words, so out-of-camera files are unaffected.

## Location

Camera files rarely have GPS, but your phone usually knew where you were. Add a location history under **Library → Location history**:

- **Google Timeline** (current format): on Android, *Settings → Location → Timeline → Export Timeline data*, then copy `Timeline.json` to this computer.
- **Google Takeout** `Records.json` (older format), or **GPX** from a tracking app.

The file is read in place, and its path is stored under `location_history` in `config.yaml`. Each photo's capture time is converted to UTC with its EXIF time zone (or the timeline's own for that day) and matched against the history:

| Source | When | Typical accuracy |
|---|---|---|
| Camera GPS | the photo has GPS in its EXIF | a few metres |
| Timeline: stayed | the phone recorded a visit at that time | ~50 m |
| Timeline: on the move | interpolated along a recorded route | tens of metres to a few km |
| Timeline: nearby | a recorded position within 15 minutes | depends on the gap |

Places are named offline from a bundled GeoNames dataset (nearest town, region, country). Group by **Place / Region / Country**, filter by place or source, or use **Show place** in the photo view. **Map** shows the photos on a world map. It uses bundled country outlines, so it never contacts a map server; it is an overview, not a street map. `location.max_gap_minutes` and `location.min_population` tune matching and naming.

Matching relies on the camera clock, so a camera set to the wrong time places photos wrongly. Location history is sensitive: it stays on your machine, derived positions live in the cache folder, and nothing is written into your originals. Keep the export out of shared or synced folders; the repository's `.gitignore` excludes `Timeline.json`, `Records.json`, and `*.gpx`.

## Model

The CLIP model drives search, tags, stacks, Curate, and the quality hint. Set it under `model:` in `config.yaml`:

| | `name` / `pretrained` | Notes |
|---|---|---|
| Default | `ViT-L-14-quickgelu` / `dfn2b` | Good search and tags. Fast on a GPU, slow to index on a laptop CPU. |
| Faster | `ViT-B-16` / `dfn2b` | About 4× faster to index, 0.6 GB. Somewhat looser results. The default of the downloadable builds. |
| Max quality | `ViT-H-14-quickgelu` / `dfn5b` | Best results. About 2.5× slower than the default, ~4 GB of memory. |

Each model keeps its own embeddings and tags, so you can switch back and forth. The first `riffle index` after switching embeds every photo once. Similarity values differ between models, so also set `stacks.min_similarity`: 0.92 for ViT-L-14, about 0.90 for ViT-B-16.

## Tags

Tags are defined in `vocabulary.yaml` (next to your config), grouped into families (`subject`, `scene`, `look`). An entry is a name, optionally with alternative phrases:

```yaml
subject:
  - boats
  - cats: [a cat, a kitten, a cat sleeping]
```

A photo scores a tag by its best-matching phrase. Tags within a family compete through a softmax, so avoid overlaps: prefer "cats", "dogs", "birds" over a general "animals". Per-family thresholds (`min_prob`, `max_tags`) are in `config.yaml`. After editing either file, run `riffle tag` (seconds; reuses stored embeddings).

## Keyboard

| Where | Keys |
|---|---|
| Anywhere | `?` help · `/` search · `O` calendar / map · `Ctrl+Z` undo the last flag change · `Esc` close / clear selection |
| Grid | click select · `Ctrl`/`Shift`+click add / range · drag to select · arrows move (`Shift` extends) · `Ctrl+A` select all · `Enter` or double-click open · `P` / `X` / `U` flag · `C` compare selection · `S` stacks · `H` hide rejected · `R` review stacks · right-click for a menu |
| Loupe | `←` / `→` previous / next · `P` / `X` / `U` flag and advance |
| Compare | click or `1`–`9` keep · `A` keep suggested · `Enter` pick kept, reject rest (with nothing kept, press twice to reject all) · `Shift+X` reject all · `Shift+U` unflag all · `P` / `X` / `U` flag focused · arrows focus · `Z` zoom · `N` / `B` next / back (review) |
| Curate | `Esc` close · `←` / `→` step through the draft in the photo view |

## Files

`riffle paths` prints the exact locations. On Linux:

| What | Where | Notes |
|---|---|---|
| `config.yaml`, `vocabulary.yaml` | `~/.config/riffle/` | Created on first run from `src/riffle/defaults/` |
| `selections.sqlite3` (flags, export history, your tags) | `~/.local/share/riffle/` | Your data: back it up |
| Catalogue, thumbnails, previews, embeddings | `~/.cache/riffle/` | Derived; safe to delete, `riffle index` rebuilds it |
| `riffle.log` (downloadable builds only) | `~/.cache/riffle/` | The previous run's log is `riffle.log.1` |
| CLIP model weights | `~/.cache/huggingface/` | Downloaded once |

`XDG_CONFIG_HOME`, `XDG_DATA_HOME`, and `XDG_CACHE_HOME` are honoured. macOS uses `~/Library/Application Support/riffle` and `~/Library/Caches/riffle`; Windows uses `%APPDATA%\riffle` and `%LOCALAPPDATA%\riffle\Cache`.

Config lookup order: `--config PATH`, `$RIFFLE_CONFIG`, `./config.yaml` in the working directory, then the user config. `data_dir`, `selections`, and `vocabulary` in the config override the default locations.

## Development

Run the API and the Vite dev server side by side; Vite proxies `/api`, `/thumbs`, and `/previews` to port 8000:

```sh
venv/bin/riffle serve --reload
cd web && npm run dev
```

Without the dev server, run `npm run build` after changing `web/src/`. The build goes into `src/riffle/web/`, which the server serves and the Python package ships. Build it before `pip wheel` or `python -m build`.

Tests: `venv/bin/pytest`. They use synthetic images and a fake encoder, so no model download is needed.

Releases are built by GitHub Actions (`.github/workflows/release.yml`) with PyInstaller. Pushing a tag `v<version>` that matches `pyproject.toml` builds all three platforms and publishes a release; running the workflow manually only builds. For a local build: `pip install . pyinstaller`, build the UI, then `pyinstaller packaging/riffle.spec`. PyInstaller needs a Python built with a shared library, which the official installers and `actions/setup-python` provide.

## Project layout

```text
src/riffle/             indexer, CLI, API server
src/riffle/defaults/    default config.yaml and vocabulary.yaml
web/                    Svelte 5 + Tailwind 4 UI (Vite, no SvelteKit)
packaging/              PyInstaller entry point and spec
tests/
development_plan.md     design notes, measurements, open work
```
