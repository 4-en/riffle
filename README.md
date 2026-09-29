# Riffle

A local tool for finding the best photos in a large archive and exporting them for editing, sharing, or printing.

You search and browse by image content, cull by picking and rejecting photos, compare bursts side by side, and copy the picks (with their RAWs, if you like) to a new folder. Search and tags use CLIP embeddings. Nothing is trained on your behalf, nothing needs labelling, and nothing leaves your machine.

## Features

- **Search** by text (`red lanterns at night`) or by example ("find similar"). Exclude terms with a minus (`street -people`), match either of several with `|` (`beach | lake`).
- **Tags** for subject, scene, look, and kind of image (photograph, illustration, painting, document), from an editable vocabulary with alternative phrases per tag. Click a tag to include it, Alt+click (or hover and click **−**) to exclude it.
- **Your tags**: select a few photos, right-click → **Learn a tag**, and photos like them get the tag. Works like the other tags in the sidebar and in every filter.
- **Captions & tags**: a written caption and keywords per photo, typed or generated (from Riffle's own tags, or by JoyCaption, a local vision language model), edited one by one or in bulk, searchable, and exported as XMP (in the copies or as sidecars), `.txt` files, or `metadata.jsonl`. **Text in the photo** (signs, menus, documents; any script, e.g. Chinese) is read with an English translation, and searchable in both.
- **Filters** for flag, folder, date, camera, lens, focal length, aperture, ISO, resolution (megapixels), orientation, and location. Counts reflect the other active filters; filters that can't narrow the result are hidden.
- **Grouping** by day, month, year, place, region, country, or folder, with a calendar or map overview; or by **Similar** content (clusters of alike photos, Broad · Medium · Fine), named after their tags, with a map of the library by similarity.
- **Thumbnails** in three sizes (S · M · L next to Sort).
- **Culling**: pick or reject one photo at a time in the loupe or many at once in the grid, with undo.
- **Stacks**: bursts and near-identical shots are grouped automatically and compared side by side, with sharpness, clipping warnings, and a suggested keeper.
- **Taste model**: once you have flagged enough, Riffle can sort by what you tend to keep. It only orders photos; it never flags them.
- **Curate**: drafts a small, varied selection (photo book, exhibition) from the current filters, steered by size, variety, time and place spread, uniqueness, search, colour, and style.
- **Discover**: a walk through the library from one photo to others related in one way each (subject, light, colour, composition, place, time), with the path kept for a slideshow, export, or Curate.
- **Editing** (experimental): crop and straighten, heal, remove things or people, replace with a prompt, and upscale on export, without ever changing your files (unless you bake edits in).
- **Export** of picks, or of any selection, as images, images + RAWs, or RAWs only. Copies only; originals are never touched.
- **Location from your phone**: a Google Timeline, Records.json, or GPX export places photos without GPS, and can add the position to exported copies.
- Duplicate detection (perceptual hash), RAW matching by file name, EXIF metadata.

Originals are read-only. Everything Riffle derives lives in a cache folder and can be deleted and rebuilt. What you create by hand (pick/reject flags, your tags, captions and fixed tags) lives separately in `selections.sqlite3`; it survives re-indexing, deleting the cache, and moving files. Back it up.

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
venv/bin/pip install -e ".[dev]"                  # HEIC support: ".[dev,heic]"; generated captions: ".[dev,captions]"
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

Open **Settings** (top right, or Ctrl+,) → **Photo folders** to add folders; **Indexing** runs and follows the index. Settings opens by itself on first start. The default profile's folders are saved to `sources` in `config.yaml`; each other profile keeps its own list (see [Profiles](#profiles)).

The folder browser can list any directory the server can read, so keep the server bound to `127.0.0.1` (the default).

## Culling and export

A typical pass:

1. Tick **Stacks** above the grid so each burst shows as one tile. Hide rejects with **Flag → Picked + Unflagged** in the sidebar, or `H`.
2. **Review stacks** walks through every stack that still has unflagged photos. Mark the keeper(s) by clicking or with 1–9, then Enter: they are picked and the rest rejected.
   - The sharpness bar marks the sharpest shot.
   - ▲/▼ warn about blown highlights and crushed shadows.
   - **★ suggested** marks the likely keeper, based on sharpness, clipping, and a CLIP quality score; hover it for the reasons. `A` keeps it, and `Z` zooms all photos to the same spot to check focus.
3. Go through the rest in the grid or the loupe with `P` (pick), `X` (reject), `U` (unflag). The loupe advances automatically.
4. Check the selection with **Flag → Picked**, then **Export**. Choose the destination, the files (images, images + RAWs, RAWs only), and the layout (flat or keeping the source folders). To export some photos without flagging them, select them and click **Export…** in the selection bar (or right-click → **Export N photos…**).

Undo any flag change with Ctrl+Z. **Unflag all** in the compare view resets a stack; **Settings → Flags & exports** unflags everything or the current filters.

Exports never overwrite anything:

- Identical files already in the destination are skipped, so an interrupted export can simply be rerun.
- Other name clashes get a `-1` suffix; an image and its RAW keep matching names.
- Each export writes `export-manifest.csv`.
- The destination can't be inside a photo folder.

Exported photos get a ↗ badge and can be filtered. **Only photos not exported before** in the export dialog skips them. **Settings → Flags & exports → Forget export history** clears that record; the exported files stay.

With a location history configured, exports add the position to photos without GPS (on by default):

- JPEG and PNG copies get it appended to their EXIF, so nothing else in the file changes.
- RAWs and other files get an `.xmp` sidecar instead.

Stacks link photos at most `stacks.max_gap_seconds` apart that are at least `stacks.min_similarity` alike. If they're too eager or too strict, adjust these and rerun `riffle index`; it doesn't re-embed. The right similarity depends on the model (see [Model](#model)).

## Taste model

**Settings → Your taste → Calibrate** trains a small model on your flags; it takes a second or two. It needs at least 20 scenes with a pick (or an export) and 50 fully rejected scenes, and it must clearly beat chance on photos it wasn't trained on.

Once calibrated, **Sort → Likely keepers first** shows promising photos first, and **Likely rejects first** helps clear out misses. It rates scenes (a burst counts once), so this sort also turns on Stacks. Picking the best frame within a burst is left to sharpness and the suggested keeper.

Once calibrated, it keeps itself up to date: when Riffle starts (and when you switch profiles), it recalibrates if flags changed since, and keeps the previous model if the new one would not pass the check. That page shows how well it works for you and how many flags changed since the last calibration. The model is stored in the cache folder and never flags anything by itself.

## Curate

Filter down to a trip, a timeframe, or a place, then press **Curate** for a draft of a small selection: good photos, but not ten of the same scene, moment, or spot.

- **Candidates**: picks and unflagged photos within the filters; a switch adds rejects. Each stack contributes one photo: its pick, or its best frame.
- **Quality**: your taste model (once calibrated), the CLIP quality score, exposure, and a bonus for picks.
- **Size**: 6, 12, 24, 48, or anything from 2 to 60.
- **Best ↔ Most varied**, **Spread over time**, **Spread over places** (uses coordinates when there are any).
- **Search**: leans the draft towards a text search, with the same syntax as the main search. It scores rather than filters, so other photos can still fill the draft; **Search influence** sets how much. A search on the main page carries over when you open Curate.
- **Common ↔ Unique**: towards photos unlike anything else in the library (their own stack does not count), or towards typical ones. Most varied is different: it only spreads the draft itself.
- **Like the locked photos**: the rest of the draft towards or away from the photos you locked. **Surprise** gives less obvious photos a chance; **Shuffle** draws again.
- **Colour & light**: swatches for the main colour and for an accent colour (an intense colour that may cover only a small part, like a red balloon in a blue sky), and Dark / Bright, Soft / Punchy, Muted / Vivid. These use per-photo pixel statistics computed during indexing. A library indexed before this feature needs one **Index now**, about 30 s per 2,000 photos.
- **Style**: sliders for Scenic, Moody, Calm, Colourful, Golden light, People, and Abstract & details. They are CLIP prompt pairs under `styles:` in `vocabulary.yaml`, which you can edit or extend.

The draft is laid out as one block in capture order: rows of equal height that fill the width, without cropping. On hover, each photo shows why it was chosen and three actions:

- **✕** removes it for this draft; it is not rejected, and the next candidate takes its place.
- **Lock** keeps it when the settings change.
- **Alternatives** offers other frames from its stack and similar photos.

**Order** shows the same draft by date, quality, similarity (alike photos next to each other), colour, light, or place (the shortest route, or a zigzag across the map); drag photos to arrange them yourself. **Mark as picks** flags the draft (undo with Ctrl+Z); **Export** copies exactly these photos, numbered in the shown order if you like. The draft is remembered per filter set in the browser, and **Reset draft** starts over.

## Discover

**Discover** in the top bar starts at a random photo (newer ones more likely), **Discover** in the photo view at that photo. It sits in the middle, with up to nine branches to photos related to it in one way each:

- **Echoes**: the same subject elsewhere, the same light and mood, a colour echo, an accent echo, a similar composition, a tag of yours.
- **Contrasts**: the same subject in opposite light, complementary colours, one trait mirrored (city → nature, night → daylight…).
- **Context**: the same day, the same place, nearby places (placed at their compass direction, north up; a walk that keeps going one way keeps its heading).

Click a photo to walk on. The trail along the bottom leads back (Backspace goes one step); **Shuffle** (R) shows other photos for the same branches. Branches you follow often come first, and the walk's drift ("drifting towards night · blue") nudges the next steps; **Reset learning** forgets both. Rejected photos and the weakest ones are left out unless you include rejects; **Within the current filters** limits the walk to them.

The walk is the result: **Replay** plays it as a slideshow, **Pick walk** flags it, **Export walk…** copies it with the files numbered in walk order, and **Curate walk…** opens Curate with its photos locked in.

## Your tags

Tags taught by example: select one or more photos, right-click → **Learn a tag from N photos…** (or **Learn tag…** in the selection bar), and name it. With one to three examples, a photo gets the tag when it is close enough to any one of them; with a single example, the tag holds that photo's closest matches, like "find similar".

- **From four examples on**, the tag learns what the examples share (a small classifier on the AI model's view of the photos), rather than taking every photo close to one of them. Varied examples (the same subject in different light, places, styles) teach it best.
- **The slider** sets how like the examples a photo must be, from *More, broader* to *Fewer, surer*, with the photo count as you move it; **Strict · Normal · Loose** are points on it. No setting suits every tag: a common trait usually wants it further left, one particular person or character further right.
- **The photos either side of the edge** show below it, large: *just inside* (click the ones that don't belong: the tag learns from them) and *just outside* (click the ones that do: they become examples). A few rounds sharpen a tag. **↺** on a marked photo takes it back.
- **Your tags** in the sidebar work like the other tags: click to include, Alt+click or **−** to exclude, **✎** to rename, change strictness, remove examples, or delete.
- To add examples, select photos, right-click, and pick the tag under **Add to a tag**.
- Tags are stored with your flags in `selections.sqlite3`, by file content, so they survive re-indexing, moved files, and model changes.

## Captions and tags

Select photos and click **Caption…** in the selection bar (or right-click → **Caption N photos…**, or **Add…** in the photo view). Each photo gets a caption and a list of **fixed tags**: written on the photo, unlike the tags Riffle computes.

- **Edit** a caption in place (saved when you click away); add tags with Enter or a comma, remove with **×**, drag to reorder. On wide screens the photos sit in several columns.
- **New tags** go after the existing ones or before them (e.g. a trigger word first), both when generating and when adding a tag to all.
- **Check** photos (the box on each, Shift+click for a range, or *all* / *this page* / *without caption or tags*) to work on just those; with nothing checked, everything on the left applies to all of them. The panel lists their tags: add a tag to them, replace one with another everywhere (onto an existing tag: merged), remove one, or clear all tags or captions. **Ctrl+Z** undoes.
- **Generate**:
  - **Riffle's tags**: the sidebar tags and your learned tags as keywords. Instant.
  - **Scene phrases**: the subjects and settings of the Similar grouping's vocabulary that stand out for each photo. Instant; rougher.
  - **JoyCaption**: a caption (short, medium, detailed, or your own prompt) and/or keywords from a vision language model that runs locally. Needs `pip install -e ".[captions]"` and an NVIDIA GPU; downloads 16 GB on first use; about 4 s per photo on an RTX 3090.
  - **Read text**: the text in the photo, as written and in its own script (Chinese, Japanese, …), with an English translation, in its own two fields on each photo. Qwen3-VL-8B, same requirements as JoyCaption (17 GB download); about 2 s per photo with text, 1.3 s without. After editing the text, **Translate the current text again** updates the translation. **Skip photos that probably have no text** first leaves out photos that CLIP says have none: about 20% faster, but it misses some small text (1 in 20 signs in testing, and many signatures on illustrations), so it's off by default; skipped photos are marked and can be read later.
  - **Caption, keywords and text**: all three in one pass of Qwen3-VL, about 4 s per photo, roughly 20% faster than running them separately. For text alone, **Read text** is much quicker on photos without any.
  - **Existing**: keep what's there and add new tags; replace what was generated but keep what you typed; or replace everything. By default only photos still missing a caption or tags are done.
- **Fixed tags** in the sidebar filter like the other tags. Search puts photos whose tag, caption, or read text (or its translation) contains the search words first, after file and folder name matches (the **Names** switch turns all of these off). Chinese or Japanese words match anywhere in the text: `北京` finds `欢迎来到北京`.
- **Export** (off by default): the caption and tags into the copies' XMP (`dc:description`, `dc:subject`, which photo apps read; a sidecar where that is not possible), as `.xmp` sidecars, as a `.txt` with the image's name (tags, the caption, or both; optionally with spaces instead of `_`), or as one `metadata.jsonl` (Hugging Face imagefolder, with the read text as `ocr_text` and `ocr_translation`). **Text in the photo and its translation** adds the read text to the others as well: after the caption in the XMP description, or on their own lines in the `.txt`.
- Stored in `selections.sqlite3` by file content, per profile.

## Editing (experimental)

Select photos and click **Edit…** (or **✎ Edit…** in the photo view). Edits never change your files: each photo keeps an edit list on top of its untouched original, and the library, search, and exports show the edited version.

- **Brush**, then **Heal** (spots, dust, small blemishes; OpenCV, instant), **Remove** (objects and people, filled with what would be behind them; LaMa), or **Replace** (describe what should be there; Stable Diffusion XL inpainting, 1–4 candidates to choose from).
- **Edit with a prompt** (Qwen-Image 2.1): say what to change ("remove the tray at the bottom right", "make it golden hour"). With nothing painted it edits the whole photo; with an area painted only that area changes, and the rest stays pixel for pixel. The model works at about 1 megapixel, in the aspect ratio of its own closest to the photo's; the photo's own fine detail is put back wherever the edit left it unchanged. Results vary with the wording and the seed, so ask for 2–4 candidates.
- **Crop**: quarter turns, straightening, a crop with aspect presets, a flip.
- **View**: the result; hold `\` for the original (also in the photo view).
- **Steps**: every edit can be turned off or deleted; **Restore original** turns them all off.
- **Bake into file…** writes the edits into the file itself, after a warning. The original is kept in `originals-backup/` in your data folder unless you switch that off; your flags, tags and captions move with it to the new file content.
- **Export** writes edited photos with their edits, or as originals; **Upscale ×2 / ×4** enlarges every exported image (Real-ESRGAN). RAW files are never edited.

Each tool offers recommended models and accepts another: a Hugging Face repository, a file in one (`org/repo:file.pt`), or a local path (Settings are stored under `editing:` in `config.yaml`). Heal and Crop work out of the box; the AI tools need `pip install -e ".[edit]"`, and Replace and Edit with a prompt an NVIDIA GPU. Models download on first use (LaMa 0.2 GB, SDXL inpainting 6.9 GB, Real-ESRGAN 67 MB, Qwen-Image 2.1 about 23 GB).

Qwen-Image 2.1 needs a diffusers newer than 0.40 (until one is released: `pip install git+https://github.com/huggingface/diffusers`). By default it uses a 4-bit GGUF of the official weights with the official text encoder, which waits in system memory while the image model runs (about 18 GB of RAM): about a minute per candidate on an RTX 3090. The Q8_0 file, the full model, or any GGUF or safetensors transformer file can be chosen instead.

Edits are shared by all profiles and stored with your data (`edits.sqlite3` and `edits/` next to `selections.sqlite3`), by file content like the flags.

## Profiles

A profile is a separate set of your own data: its photo folders, picks and rejects, export history, your tags, captions, and the taste model. Use one per project, e.g. a photo book next to your general culling, or one for photographs and one for illustrations. Settings are shared.

- **Folders per profile.** A profile shows only its own folders, and everything that looks at "the library" follows: the grid, search, tag counts, Similar and its map, Discover, Curate's uniqueness, and the taste model. Indexing covers every profile's folders once and they share the index, so adding a folder another profile already has (**Settings → Photo folders → From your other profiles**) is instant.
- **External drives and network folders:** a folder that cannot be reached (unplugged, not mounted, or an empty mount point) is shown as *offline*. Its photos stay as they are, with their thumbnails, previews and AI data, until it is back; opening or exporting an original then says it cannot be reached. If you deleted or moved the folder instead, **Forget…** next to it in Settings → Photo folders removes it from every profile and drops its photos' data (add the new place as a folder: moved photos are recognised by content and keep their flags and tags).
- **Removing a folder** from a profile only hides it there. A folder no profile shows keeps its thumbnails and AI data, so adding it back is instant; **Settings → Files → Clean up** deletes that data.
- **Settings → Profiles** lists them with their counts. A new profile starts empty (no folders yet), or copies any of its photo folders, flags, export history, your tags, and captions from the current one (e.g. keep your tags but start the picks from scratch).
- Once there is more than one, the top bar shows the active profile with a menu to switch; other open tabs follow.
- Switching waits until indexing or an export has finished; an export records its history in the profile it started in.
- Each profile is one SQLite file. The default is `selections.sqlite3`; the others are in `profiles/` next to it. Deleting one moves its file to `profiles/deleted/`.

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

Camera files rarely have GPS, but your phone usually knew where you were. Add a location history under **Settings → Locations**:

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

The CLIP model drives search, tags, stacks, Curate, and the quality hint. Choose it in **Settings → AI model**, or set it under `model:` in `config.yaml`:

| | `name` / `pretrained` | Notes |
|---|---|---|
| Default | `ViT-L-14-quickgelu` / `dfn2b` | Good search and tags. Fast on a GPU, slow to index on a laptop CPU. |
| Faster | `ViT-B-16` / `dfn2b` | About 4× faster to index, 0.6 GB. Somewhat looser results. The default of the downloadable builds. |
| Max quality | `ViT-H-14-quickgelu` / `dfn5b` | Best results. About 2.5× slower than the default, ~4 GB of memory. |
| Nature: BioCLIP 2.5 | `hf-hub:imageomics/bioclip-2.5-vith14` (no `pretrained`) | Plants, animals, fungi: tells species apart (female mallards next to the drakes) and finds them by common or scientific name. Weaker on everyday scenes, styles and the quality hint. 3.9 GB. |
| Nature: BioCLIP 2 | `hf-hub:imageomics/bioclip-2` | The same focus, smaller (1.7 GB). |

Any other model OpenCLIP can load works too (Settings → AI model → *Another OpenCLIP model*), including Hugging Face repositories in OpenCLIP's format (`hf-hub:<repo>`).

Each model keeps its own embeddings and tags, so you can switch back and forth. Switching in Settings reads every photo with the new model first, in the background, while the current one stays in use; a model used before switches at once. Similarity values differ between models, so switching also sets `stacks.min_similarity`: 0.92 for ViT-L-14, 0.90 for ViT-B-16, and for other models a value fitted to your camera bursts (photos taken within two seconds of each other). When editing `config.yaml` by hand, run `riffle index` and set it yourself.

## Tags

Tags are defined in `vocabulary.yaml` (next to your config), grouped into families (`subject`, `scene`, `look`, `kind`). An entry is a name, optionally with alternative phrases:

```yaml
subject:
  - boats
  - cats: [a cat, a kitten, a cat sleeping]
```

A photo scores a tag by its best-matching phrase. Tags within a family compete through a softmax, so avoid overlaps: prefer "cats", "dogs", "birds" over a general "animals". Per-family thresholds (`min_prob`, `max_tags`) are in `config.yaml`. After editing either file, run `riffle tag` (seconds; reuses stored embeddings).

The `kind` family (photograph, illustration or drawing, painting, document) says what sort of image a file is, so illustrations or scans can be filtered in or out. It only tags when confident (`min_prob` 0.7). Its phrases are scored without the "a photo of …" templates (`family_templates:` in `vocabulary.yaml`), which confused it: with them, 167 ordinary photos counted as screenshots.

## Similar grouping

**Group → Similar** clusters the photos in the current view by content: a trip gets its own themes (waterfront, streets, palaces, birds…), the whole library broader ones. **Broad · Medium · Fine** sets how finely. No group takes more than a share of the view (a fifth at Broad, a tenth at Medium, a twentieth at Fine); larger ones are split further, so filtering to illustrations groups them by subject rather than keeping them together. Clusters are named after one of your own (learned) tags when most of their photos have it; then after fixed tags that most of a cluster has and the rest of the view mostly doesn't, a character first ("Illustrations: nia (xenoblade) · chest jewel"; tags about the file, like `artist name`, and booru staples like `1girl` are skipped); otherwise from a separate list of about 500 everyday concepts (`cluster_names.yaml`, not tags), by the phrase that sets each cluster apart from the rest of the view ("sheep", "old town streets", "birds in flight · a blue sky"), or by the kind of image when the view mixes kinds, with what sets each apart from the others of that kind ("Illustrations: swimmers", "Illustrations: figurines"); largest first; photos that fit no group of four or more are under **Other**.

**Grid | Map** switches to a map of the photos by similarity: alike photos sit close together, coloured by cluster, with the cluster names over their regions. Zoom in and the dots become thumbnails; click a photo to open it, a name to open that group in the grid. The layout is computed once for the whole library (a few seconds; cached until the next index), so photos keep their places when you filter.

**Selecting on the maps.** Both maps have **Pan · Box · Lasso** tools (Shift-drag draws a box without switching; Ctrl adds to the selection). On the Similar map this selects the photos inside the shape; on the world map, the places inside it and all their photos, with **Only these places** to turn them into a filter. The selection bar then works as in the grid: pick, reject, compare, create a tag. `P` / `X` / `U` flag the selection. Clusters follow what the model sees, content and scene, not events: for those, group by day or place.

## Keyboard

| Where | Keys |
|---|---|
| Anywhere | `?` help · `Ctrl+,` settings (`1`–`8` switch pages) · `/` search · `O` calendar / map · `Ctrl+Z` undo the last flag change · `Esc` close / clear selection |
| Grid | click select · `Ctrl`/`Shift`+click add / range · drag to select · arrows move (`Shift` extends) · `Ctrl+A` select all · `Enter` or double-click open · `P` / `X` / `U` flag · `C` compare selection · `S` stacks · `H` hide rejected · `R` review stacks · right-click for a menu |
| Loupe | `←` / `→` previous / next · `P` / `X` / `U` flag and advance |
| Compare | click or `1`–`9` keep · `A` keep suggested · `Enter` pick kept, reject rest (with nothing kept, press twice to reject all) · `Shift+X` reject all · `Shift+U` unflag all · `P` / `X` / `U` flag focused · arrows focus · `Z` zoom · `N` / `B` next / back (review) |
| Curate | `Esc` close · `←` / `→` step through the draft in the photo view |
| Discover | `1`–`9` follow a branch · `Backspace` back · `R` shuffle · `Esc` close · replay: `←` / `→` step, `Space` pause |

## Files

`riffle paths` prints the exact locations. On Linux:

| What | Where | Notes |
|---|---|---|
| `config.yaml`, `vocabulary.yaml` | `~/.config/riffle/` | Created on first run from `src/riffle/defaults/` |
| `selections.sqlite3` (flags, export history, your tags) | `~/.local/share/riffle/` | Your data: back it up. Other profiles: `profiles/*.sqlite3` next to it |
| Catalogue, thumbnails, previews, embeddings | `~/.cache/riffle/` | Derived; safe to delete, `riffle index` rebuilds it (every profile's folders) |
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

Tests: `venv/bin/pytest -n auto` (in parallel; plain `venv/bin/pytest` works too). They use synthetic images and a fake encoder, so no model download is needed.

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
