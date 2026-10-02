---
license: apache-2.0
library_name: pytorch
pipeline_tag: image-to-image
base_model: caidas/swin2SR-classical-sr-x4-64
tags:
  - super-resolution
  - demosaicing
  - raw
  - bayer
  - dng
  - swin2sr
  - photography
---

# Riffle RAW SR: Bayer RAW → ×2 linear DNG

Two models that take a camera RAW's Bayer data and return the image at twice the resolution, doing demosaicing and upscaling in one step: one in colour, one in black and white. The result is written as a linear DNG that opens in a RAW developer (tested with darktable) like the original RAW: linear camera RGB, with the camera's colour matrix, white balance and EXIF. They are made for [Riffle](https://github.com/4-en/image-organizer), a local photo organiser, and work on their own with the files here.

> **Experimental.** Check the results before relying on them, and keep your RAWs.
> - **Performance**: about 45 s per 20 MP RAW on an RTX 3090, minutes on a CPU; 3.5 GB of RAM; each DNG is about 490 MB (uncompressed 16-bit).
> - **Quality**: trained on synthetic pairs made from one camera's photos. Results can come out soft, or with artefacts the RAW does not have; the lens's chromatic aberration stays, and clipped highlights are made neutral white.
> - **Compatibility**: RGGB Bayer RAWs only. Trained on an OM-5 Mark II (20 MP, Four Thirds); other cameras are untested, and the noise setting is calibrated to that sensor. Whole frames; no lens corrections.

## Files

| File | |
|---|---|
| `riffle-raw-sr-rgb.safetensors` | colour model |
| `riffle-raw-sr-mono.safetensors` | black-and-white model |
| `rawsr.py` | model, RAW reading and DNG writing (Riffle's own module; runs on its own) |
| `upscale.py` | command line |
| `results/` | the evaluation: per patch (`patches.csv`) and means (`summary.json`) |
| `images/` | the comparisons below |

## Use

```sh
hf download <user>/riffle-raw-sr --local-dir riffle-raw-sr && cd riffle-raw-sr
pip install -r requirements.txt
python upscale.py P1234.ORF                        # → P1234.dng
python upscale.py P1234.ORF --denoise 0.5          # remove more of the noise
python upscale.py P1234.ORF --bw --filter orange   # black and white → P1234_bw.dng
```

- `--denoise` (0.05–1, default 0.3): the share of the noise the model removes, scaled by the ISO. Lower keeps the RAW's own grain and the fine texture it hides; 1 removes it all.
- `--bw`: like panchromatic black-and-white film by default; `--filter yellow | orange | red | green | blue`, `--luminance` (brightness as the eye sees it), or `--mix r,g,b` (own weights on white-balanced camera RGB).

From Python: `rawsr.convert("riffle-raw-sr-rgb.safetensors", "P1234.ORF", "P1234.dng", denoise=0.3)`.

In Riffle (`riffle upscale-raw`, or **RAWs → ×2 DNG** in the export), set the checkpoints in `config.yaml`:

```yaml
editing:
  raw_upscale: <user>/riffle-raw-sr:riffle-raw-sr-rgb.safetensors
  raw_upscale_mono: <user>/riffle-raw-sr:riffle-raw-sr-mono.safetensors
```

## How it works

- **Architecture**: Swin2SR classical ×4 (embedding 180, 6 × 6 transformer layers, window 8; 12 M parameters), fine-tuned from `caidas/swin2SR-classical-sr-x4-64`. The first convolution takes 4 channels (R, G1, G2, B), the last gives 3 (colour) or 1 (black and white). ×4 from the packed Bayer data is ×2 of the RAW.
- **Input**: the RAW's Bayer data packed as (4, H/2, W/2), black 0, white 1; white-balanced; where a channel reached the white level, all channels capped at the lowest one's clip level (else a clipped green turns magenta); scaled to its peak; gamma 2.2. Output: gamma 2.2 in the same scale.
- **Noise input**: log2 of the noise variance to remove, relative to one pixel at ISO 200 (ISO / 200 × `denoise`), as a learned offset on the first convolution's features. Trained with part of the noise kept in the target, so a lower value keeps the RAW's own noise, where it is, instead of inventing grain.
- **Black and white**: the model learns one mix, (R + 2G + B) / 4 of white-balanced linear camera RGB, roughly panchromatic film. Other mixes come from scaling the input channels before the model, as a colour filter in front of the lens does; it was trained with random channel gains for that.

## Training data

About 1,000 RAWs from one OM-5 Mark II (a personal library; not released), cut into patches. No demosaicing anywhere:

- **Target**: each 2 × 2 quad of the RAW as one RGB pixel (R, the mean of the two greens, B), so linear RGB at half the RAW's resolution, all measured. The four Bayer planes are first shifted a quarter pixel to the quad's centre, so the colours line up.
- **Input**: each 2 × 2 block of quads averaged per colour (what a sensor with pixels twice as large records), one colour kept per site in RGGB order: real sensor values, a quarter of the RAW's resolution.
- **Blur**: a downscaled image is crisper per pixel than a native one, where the lens spreads detail. Real ORFs matched the synthetic input at Gaussian σ 1.25–3.1 (median 2.4); training used 0.5–3.5 for the input and half of it for the target.
- **Noise**: measured from the camera's own RAWs (one pixel's variance 4.6 × 10⁻⁵ · level · ISO/200), added so each input site is as noisy as one real pixel, × 0.7–3.
- **Loss**: L1 and an L1 on Sobel gradients, on gamma-encoded values. A spectral loss that ignores sub-pixel position was tried and dropped: at low `denoise` it made coloured digital noise.

## Evaluation

**Held-out patches with ground truth**: {{N_PATCHES}} patches (256 × 256) from {{N_PHOTOS}} photos that were never trained on. The input is made as in training, with one real pixel's noise at each photo's ISO and blur σ 1.25–3.1 (the range measured on real RAWs); the ground truth is the training target. The baselines get the input demosaiced (OpenCV, edge-aware) and enlarge that; black and white is (R + 2G + B) / 4 of linear RGB for every method. PSNR and SSIM on gamma-encoded values, without an 8 px border.

Colour:

{{RGB_TABLE}}

Black and white:

{{BW_TABLE}}

Read these numbers with care. The test degradation is the one the models were trained on, and the ground truth keeps half the input blur, as the training target does, so this benchmark favours them by construction. The baselines were made for display images, not for a noisy, blurred Bayer input, and Real-ESRGAN draws texture that is plausible but not where it was, which costs PSNR. The real crops below, without ground truth, are the fairer comparison. One thing the numbers do show: the black-and-white model is no better than converting the colour model's output.

Held-out patches (left to right: the input demosaiced and shown at the output size, Real-ESRGAN, Swin2SR, Riffle at denoise 1.0, ground truth):

{{SYNTH_RGB}}

{{SYNTH_BW}}

**Held-out RAWs**: crops of real RAWs from the same held-out photos (no ground truth). The baselines start from LibRaw's AHD demosaic; Riffle runs at the default denoise 0.3. Every result is written as a DNG and developed by darktable with its default settings, so all are processed alike. The duck is ISO 4000, the others ISO 200; the flower is a 50 MP handheld High Res Shot.

{{REAL_RGB}}

{{REAL_BW}}

## Limitations

- One camera: other RGGB sensors should work but are untested; the noise input assumes this sensor's noise per ISO.
- Synthetic training pairs: the gap between them and real RAWs (blur, noise) is the main risk; check results on your own photos.
- Whole frames, no lens corrections, no crop; uncompressed DNGs (darktable does not read compressed 16-bit integer DNGs).
- Clipped highlights become neutral white; the lens's chromatic aberration is kept.

## License

The weights: Apache-2.0, like the Swin2SR weights they are fine-tuned from. The code (`rawsr.py`, `upscale.py`): MIT, from Riffle.

## Citation

Swin2SR, the architecture and starting weights:

```bibtex
@inproceedings{conde2022swin2sr,
  title={{S}win2{SR}: SwinV2 Transformer for Compressed Image Super-Resolution and Restoration},
  author={Conde, Marcos V and Choi, Ui-Jin and Burchi, Maxime and Timofte, Radu},
  booktitle={Proceedings of the European Conference on Computer Vision (ECCV) Workshops},
  year={2022}
}
```
