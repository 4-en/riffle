# RAW to upscaled linear DNG (proof of concept)

A standalone experiment, not part of Riffle: take one Bayer RAW, correct its lens, cut
out the crop set on its JPEG, upscale only that ×2, and write a linear DNG that a RAW
developer (darktable) opens like the original.

## Use

```sh
venv/bin/pip install rawpy tifffile lensfunpy     # not in Riffle's dependencies
venv/bin/python experiments/raw_sr/raw_to_dng.py IN.ORF -o OUT.dng --model swin2sr \
    [--crop 0.3,0.1,0.4,0.3 --angle 3 | --riffle]
venv/bin/python experiments/raw_sr/compare.py IN.ORF OUT.dng [MORE.dng …] --out DIR --crop 0.6,0.2,260
```

- `raw_to_dng.py`:
  - `--model swin2sr | swinir | realesrgan | lanczos | none` (none: scale 1)
  - `--crop x,y,w,h` (fractions of the JPEG's frame after turning and straightening),
    `--angle` (degrees clockwise), `--rot90`, `--flip`: a crop as Riffle's editor stores it;
    `--riffle` reads the photo's own from Riffle's edit list (read only); `--jpeg` if the
    JPEG is not beside the RAW
  - `--no-lens` (no corrections), `--chroma N` (colour smoothing, default 3; 0 off),
    `--demosaic AHD | AAHD | DHT | DCB | PPG | VNG`, `--median N`
- `compare.py`: renders the RAW and each DNG with `darktable-cli` (a throwaway config and
  an in-memory library, default processing); prints each rendering's mean colour, and its
  pixel-by-pixel ΔE from the RAW's once the two are matched (SIFT); writes 100 % crops of
  the same place side by side (`crops.png`).

## How it works

1. **Read**: LibRaw (rawpy) demosaics to linear camera RGB, 16 bit, black level off, scaled
   to the white level, no white balance or gamma, in the sensor's orientation.
2. **Lens**: lensfun (by the lens in the EXIF) corrects vignetting and lateral chromatic
   aberration; then the image is turned upright.
3. **Geometry from the JPEG**: with the camera's JPEG beside the RAW, a radial model (scale,
   rotation, offset, two distortion terms) is fitted to SIFT matches between the two. That
   is the camera's own distortion correction, and it maps the JPEG's pixels to the RAW's.
   The crop (Riffle's geometry: quarter turns, straightening, crop, flip) is chained with
   it, and the crop plus a 32 px margin is resampled once (Lanczos) out of the linear RAW.
   Without a JPEG, lensfun corrects the distortion as well.
4. **Colour smoothing**: a 3 px median over the colour differences (YCrCb), against the
   speckle LibRaw's demosaicing leaves at fine edges.
5. **Upscale**: white-balanced and gamma-encoded (2.2) for the model, in 256 px tiles
   overlapping by 24 px, back to linear camera RGB; the margin is trimmed.
6. **Write**: DNG 1.4, LinearRaw, `ColorMatrix1` (LibRaw's XYZ D65 → camera), `AsShotNeutral`
   (camera white balance), black 0, white 65535, make and model, Orientation 1; then an EXIF
   IFD with the RAW's exposure, date, lens and maker note. (tifffile does not write the
   ExifIFD tag: a placeholder, 34666, is renamed afterwards. ORF files start with `IIRO`,
   so the RAW's IFDs are read directly.)

## Findings (OM-5 Mark II `.orf`, 20 MP, ISO 200, M.Zuiko 12–100 mm at 12 mm, RTX 3090)

- **Colour and metadata work.** darktable opens the DNG as a RAW from the camera's white
  balance; unscaled and uncorrected, its rendering lines up with the ORF's to 0.55 px, mean
  colour within 1.6 ΔE, pixel ΔE median 3.8. exiv2 reads camera, exposure, lens (standard
  EXIF and the decoded Olympus maker note) and date.
- **darktable does not correct the ORF's lens by default** (the DNG and the ORF render with
  the same geometry). The camera's JPEG is distortion-corrected.
- **lensfun's distortion profile disagreed with the camera.** Against the camera's JPEG,
  with a fit on the centre, the uncorrected RAW drifts inward (−32 px at 35–60 % of the
  half diagonal, −59 px at 60–80 %), lensfun's correction outward (+17, +39, +57 px): it
  corrects about 1.6 times as much. Correcting in the sensor's orientation instead of
  upright made no difference; counting straight line segments on a building shot at 12 mm
  did not tell which is right. So the JPEG's geometry is used whenever there is a JPEG.
- **The fitted camera geometry fits**: 3,081 of 3,095 matches, residual 0.5 px in the
  centre and 0.7 px in the corners. The DNG rendered by darktable drifts 1 px at most from
  the JPEG across the frame.
- **The crop lands exactly**: a crop of 40 × 30 % of the frame, straightened 3°, upscaled
  ×2, matches Riffle's crop of the JPEG (`edits.apply_geometry`) at scale 2.0035 (the RAW
  is 0.15 % larger than the JPEG), rotation 0.000°, offset under 1 px, 0.76 px residual.
- **Cropping first pays**: that crop (12 % of the frame) took 30 s to upscale with Swin2SR
  and is 60 MB, against 219 s and 493 MB for the whole frame.
- **Speckle**: LibRaw's AHD (and DHT, DCB, AAHD, median passes) leaves coloured speckle at
  leaf edges against the sky that darktable's own rendering of the ORF does not have, and
  upscaling sharpens it. The colour-only median removes most of it without losing detail.
- **Models** (whole frame ×2, 7824 × 10480):

  | Model | Time | Result |
  |---|---|---|
  | Lanczos | 2 s | the baseline: softer, faithful |
  | Swin2SR classical ×2 | 219 s | faithful, somewhat crisper than Lanczos |
  | SwinIR-M classical ×2 | 224 s | about the same as Swin2SR |
  | Real-ESRGAN ×2plus | 28 s | cleanest look, but smoothed and redraws leaf texture; mean colour off by 3.2 ΔE |

- **Size**: uncompressed. darktable did not open a deflate-compressed 16-bit DNG (the DNG
  spec allows deflate for floating point only); lossless JPEG would need `imagecodecs`,
  float16 + deflate is the other option. Neither was tried.

## Open

1. A better demosaic (LibRaw lacks RCD and AMaZE, which darktable uses), or getting the
   demosaiced image from darktable itself. A model that takes the Bayer data directly
   (below) would replace it.
2. RAWs without a JPEG: lensfun's distortion profile (see above) or the correction data in
   the maker note, which darktable can use for Olympus.
3. fp16 inference; smaller files.
4. In Riffle: an export option "RAWs as ×2 linear DNG, with the photo's crop".

## Plan: a Bayer → ×2 RGB model, trained on this camera

Today the image is demosaiced (LibRaw), then upscaled by a model trained on display
images. A model that takes the Bayer data and returns RGB at twice the size does both at
once, from the most accurate input there is, and learns this sensor's noise and colour.

**Why RGB out, not a ×2 Bayer RAW.** Upscaling and demosaicing are the same problem:
estimating values the sensor did not record. A model that outputs a ×2 Bayer image
estimates full colour internally, keeps one channel per pixel, and leaves a demosaicer to
estimate the other two again. And a mosaic cannot be warped (rotating or undistorting it
mixes its colours), so with RAW out the crop could only be an axis-aligned cut; with RGB
out, the camera-geometry fit and the exact crop apply to the ×2 result as now. (RAW-to-RAW
is what most open research does: the NTIRE 2024 and 2025 RAW super-resolution challenges,
[BSRAW](https://arxiv.org/abs/2312.15487); Adobe's "Super Resolution" is Bayer → ×2 linear DNG.)

**Training data from the ORFs themselves** (1,533 in the library):
- Target: the Bayer data binned 2×2 (R, the mean of the two greens, B): full-colour pixels
  at half resolution with no demosaicing in them, in linear camera RGB.
- Input: the target scaled down ×2 again, sampled back into the RGGB pattern, with this
  sensor's noise added (shot and read noise, estimated from flat areas or from dark frames
  with the lens cap on), and random blur, since a downscaled image is crisper per pixel
  than a native one, where the lens spreads detail over pixels. This gap between
  synthetic and real input is the main risk.
- Patches of 128–256 px, highlights clipped as the sensor clips them.

**Real pairs from High Res Shot.** On a tripod, the same static scene shot normally and in
High Res Shot (80 MP, about ×2 linear; handheld 50 MP, about ×1.6), aligned: real input
and target with this lens's and sensor's detail. A few dozen scenes are enough to fine-tune
the synthetically trained model, and to measure honestly whether it recovers detail or
invents it, which cannot be judged from a single image.

**Model.** Swin2SR classical ×2 (already used here), its first convolution replaced to take
the packed Bayer input (4 channels, R, G, G, B, at half resolution), so its pretrained
layers are kept; fine-tuned rather than trained from scratch. L1 loss on gamma-encoded
values (so shadows count), fp16 training on the RTX 3090. A run: hours to a day or two.

**Steps.**
1. Data pipeline: binning, mosaicking, the noise model (estimated from the ORFs), patches;
   a held-out set of photos never trained on.
2. Fine-tune Swin2SR on the synthetic pairs; compare on the held-out photos, rendered in
   darktable, against the current pipeline (LibRaw, colour smoothing, Swin2SR) and Lanczos.
3. Shoot a handful of High Res Shot pairs; test on real detail, then fine-tune on them.
4. In `raw_to_dng.py`: the model replaces demosaicing, colour smoothing and upscaling. The
   Bayer crop keeps to the 2×2 grid, with a margin; the camera-geometry fit, lens
   corrections and the exact crop are applied to the ×2 RGB afterwards.

**Expectations.** Against the current pipeline, mostly cleaner fine edges and colour and no
demosaicing speckle, not a different class of image. A single RAW holds real new detail
for perhaps ×1.3–1.5; beyond that a faithful model stays soft and a less faithful one
invents detail. The High Res Shot pairs show where that limit lies for this camera and lens.

## Training data (step 1)

```sh
venv/bin/python experiments/raw_sr/prepare_dataset.py --data-dir ~/Pictures/photos
venv/bin/python experiments/raw_sr/train.py --save-val-image [--monochrome]
venv/bin/python experiments/raw_sr/upscale_raw.py IN.ORF [-o OUT.dng] [--model rgb | mono] \
    [--crop x,y,w,h] [--denoise F] [--luminance] [--filter yellow | orange | red | green | blue | --mix r,g,b]
```

`upscale_raw.py` runs a trained model (default `checkpoints/best_psnr.pt`, or
`checkpoints_mono/`) on the packed Bayer data and writes a ×2 linear DNG with the RAW's colour
matrix, white balance and EXIF; a monochrome result is neutral grey in it. `--crop` takes
fractions of the upright frame; the image is turned upright after the model (turning the
mosaic first would move its colours within the quad). No lens corrections or Riffle crop yet.
**Noise level.** The models take the input's noise level as a second input (log2 of its
variance relative to one pixel at ISO 200: in training the noise gain × ISO / 200, at
inference ISO / 200 × `--denoise`). Without it the model has to guess, and since training
noise averages 1.45× a real RAW's, it smoothed subtle texture (a bee's eye) as noise. It is
a learned offset on the first convolution's features (what a constant input channel adds),
zero at first, so older checkpoints load and behave as before (`train.py --init`).
As a plain noise level, the model learnt to ignore it: the noise is visible in the input, so
the number added nothing (`--denoise` 0.1 vs 1 changed the output by a third of an 8-bit
step). So it means the noise to remove: in half the samples (`--partial-denoise`) part of the
input's added noise also goes into the target, interpolated per colour from its sites
(`dataset.keep_noise`), and the model is told only the rest. `--denoise` is the share of the
photo's noise variance to remove: 1 all, lower keeps the RAW's own noise and the fine texture
it hides. The patches' own noise cannot be split off, so training always removes it: after
the Lanczos shift, input blur and averaging that is 0.25 of a pixel's variance without blur,
0.06 at σ 1, 0.02 at σ 2 (`dataset.residual_noise`). So `--denoise` down to about 0.05 is
trained. (Until this was counted exactly, 0.21 was assumed for every blur: inputs had less
noise than intended, and the lowest trained `--denoise` was 0.2.)

Clipped highlights are made neutral before the model (and in training): where a channel
reached the white level, all channels are capped at the lowest one's clip level. Without
that, a clipped green turns magenta after white balance, and the model reproduced it.
For monochrome this happens before the mix gains: capped after them, clipped white sat at
the weakest gained channel's level (blue at 0.08× under the luminance mix), and overexposed
areas came out medium grey with an outline.
A 300 × 300 px crop of the RAW takes about 1 s on the RTX 3090.

`prepare_dataset.py` writes `data_binned/{train,val}/*.npz` (320 × 320 patches, 24 per RAW,
a fifth of them random rather than the most textured, so flat areas and their noise are
learnt too), `noise.csv` (per photo) and `noise.json` (the library's noise per ISO). About
0.65 MB per patch, 17 GB for 1,000 RAWs. Held-out photos go to `val/`.

What a patch is (`dataset.py`):
- **Planes.** The four Bayer planes, black 0, white 1, before white balance, each shifted a
  quarter of its pitch (Lanczos) to the centre of its 2×2 quad, so all four are sampled at
  the same points. No demosaicing.
- **Target.** Per quad: R, the mean of the two greens, B. Linear camera RGB at half the
  RAW's resolution.
- **Input.** Each 2×2 block of quads averaged per colour (what a sensor with pixels twice as
  large records), one colour kept per site in RGGB order. These are averages of real sensor
  pixels; the re-mosaic only drops colours, as a sensor does. Then blur and noise (below).
- Packed input (4, P/4, P/4) → target (3, P, P): ×2 over the mosaic, as at inference.
- Crops keep to the 4 px RGGB grid; rotations and flips are free, since the planes are
  aligned. Validation samples are fixed per index.

**Why not 2×2 binning of the packed planes** (the first version): it averages R, G1, G2 and B
over the same 4 × 4 pixels, which puts the colours a quarter of a site apart instead of
half. On a linear ramp, R and B landed 0.68 px from where a real mosaic samples them;
with the shifted planes, 0.04 px.

**Noise.** Each G1 is compared with the mean of its four diagonal G2 neighbours (cancels
linear gradients; 1.25× a pixel's variance in flat areas), in the flattest 10 % of each level
band, with a 3σ-clipped variance (a MAD is stepped by the 12-bit levels). Across 30 ORFs one
pixel's variance is 4.6 × 10⁻⁵ · s · ISO/200 (σ 0.0029 at s = 0.18, ISO 200); photos with
little flat area read up to 3× higher, so the library median per ISO is used. Read noise
was not measurable above s = 0.02. The input gets noise so that each site is as noisy as
one pixel, times a log-uniform gain of 0.7–3 (`--noise-gain`).

**Blur.** A downscaled image is crisper per pixel than a native one. On the same regions
(most textured quarter), the high-band to mid-band power of real native mosaics matches the
synthetic input at Gaussian σ 1.25–3.1 target px (median 2.4). The input is blurred by
σ 0.5–3.5 (`--blur`); the target by a fraction of that (`--target-blur`, default 0.5): 0
teaches full sharpening (and inventing detail where it cannot), 1 none (as soft as the
lens). This is the main setting to judge by eye and against High Res Shot pairs.

**Monochrome** (`train.py --monochrome`, `model_mono.py`). The target is one fixed mix,
(R + 2G + B) / 4 of white-balanced camera RGB, in linear light. Other mixes come from scaling
the input channels before the model, as a colour filter in front of the lens does:
`dataset.mix_gains`. In training, 70 % of samples (`--mix-prob`) get random channel gains
(R and B down to 0.005, G to 0.2 of the largest), applied after the noise as at inference.
- Default: the model's own mix, (R + 2G + B) / 4, roughly panchromatic black-and-white film,
  which sees blue more than the eye does (pale skies, hence the filters). `--luminance` for
  brightness as the eye sees it.
- Luminance: CIE Y from the camera matrix needs a negative blue weight in camera RGB
  (OM-5 II: 0.13 R + 1.09 G − 0.23 B), which gains cannot make. `dataset.luminance_weights`
  fits the closest non-negative weights on the photo's own colours (each ≥ 0.02); on 30 ORFs
  about 0.18 R + 0.82 G, 5 % off at the median, blues brighter and foliage darker.
- Filters (`FILTERS`: yellow, orange, red, green, blue) multiply the mix (film-like, or
  luminance) by an approximate transmission. On the luminance fit, red needs blue at 0.005 of
  the largest gain, the edge of the trained range, and the blue filter is weak (little blue
  to raise); on the film mix all of them are well inside it.

**Loss: texture without its exact position (optional).** L1 and the edge loss compare each pixel
with the same target pixel. Fine texture the input determines only to within a pixel is then
cheaper blurred than placed slightly off: on a test texture, a 1 px shift cost L1 0.046,
blurring it 0.035. Two options in `train.py`, off by default:
- `--coarse-l1`: L1 and edge loss on the 2×-downscaled output, plus `--anchor-weight` (0.25)
  × full-resolution L1. What the input determines must still match.
- `--spectral-weight`: L1 between the magnitude spectra of luma in overlapping windows
  (`--spectral-window`, 8 × 8, step 4), which do not change when content shifts within a
  window (the same test: 0.013 for the shift, 0.030 for the blur; 16 × 16: 0.007). The window
  sets how far texture can spread into smooth surroundings (about half a window: coarse L1
  hardly sees fine texture); 8 still holds 2–4 periods of the 2–4 px detail it is for. The target's own noise is subtracted from its power first (its expected
  variance per pixel comes from the dataset, through the target blur), so the model is not
  rewarded for making noise; on flat areas the estimate matches the target's noise (0.98× at
  the highest frequencies, without target blur).
Loss sizes after an epoch: coarse L1 0.005, edge 0.008, spectral 0.002, so a weight of about 3.

**Result: the spectral loss is not used.** Trained with `--coarse-l1 --spectral-weight 3`, it
barely changed the output at `--denoise 1`, and at 0.3 or 0.1 it made coloured digital noise.
Where the target keeps part of the noise, the spectral loss counts that noise as texture the
output should have, and since it ignores position, generating new noise satisfies it as well
as passing the photo's own through. It looks at luma only, and with `--coarse-l1` only the
anchor holds colour at full resolution, so the generated noise took any colour. With full
L1 the only way to match kept noise is the photo's own, where it is: the default model at
`--denoise` 0.1 brings back texture that follows the bee's eye. So the smoothness was mostly
the denoising, not L1's strictness about position. The options stay in `train.py`, off.
