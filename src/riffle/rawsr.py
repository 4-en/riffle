"""Bayer RAW → ×2 linear DNG with Riffle's Bayer models (trained in experiments/raw_sr).

The model replaces demosaicing and upscaling in one step: it takes the RAW's packed Bayer
data (R, G1, G2, B at half the resolution) and returns RGB, or black and white, at twice the
RAW's resolution. The DNG opens in darktable like the RAW: linear camera RGB with the
camera's colour matrix, white balance and EXIF; a black-and-white result is neutral grey in
it. Whole frames, upright; RGGB sensors only (the models are trained on an OM-5 Mark II).

- Input as in training: white balance; where a channel reached the white level, all channels
  capped at the lowest one's clip level (else a clipped green turns magenta); for black and
  white, the mix gains after that; scaled to the peak, gamma 2.2. In tiles, on the GPU.
- Noise: the model is told the share of the noise to remove (``denoise``, 0.05-1), scaled by
  the ISO. Lower keeps the RAW's own noise and the fine texture it hides.
- Black and white: the model learnt one mix, (R + 2G + B) / 4 of white-balanced camera RGB,
  roughly panchromatic film. Other mixes come from scaling the input channels, as a colour
  filter in front of the lens does: ``FILTERS``, the photo's luminance (as the eye sees
  brightness), or own weights.

Checkpoints: ``editing.raw_upscale`` and ``editing.raw_upscale_mono`` in the config, a path or
a Hub file ("org/repo:file.pt"); by default ``models/raw-upscale.pt`` and
``models/raw-upscale-mono.pt`` in Riffle's data folder (``riffle paths``). Whether a
checkpoint is colour or black and white is read from it.
"""

from __future__ import annotations

import importlib.util
import struct
import threading
from pathlib import Path

import numpy as np

GAMMA = 2.2
TILE, PAD = 256, 24  # packed px per tile, and the overlap around it
KINDS = ("rgb", "mono")
SETTINGS = {"rgb": "raw_upscale", "mono": "raw_upscale_mono"}  # the config's editing: keys
DEFAULT_FILES = {"rgb": "raw-upscale.pt", "mono": "raw-upscale-mono.pt"}

# Black and white: the model's own mix (each quad's mean), and the input gains it was
# trained with (R and B down to this share of the largest, G to this).
MONO_MIX = np.array([0.25, 0.5, 0.25])
MIX_GAIN_MIN, MIX_G_GAIN_MIN = 0.005, 0.2
# Approximate transmission (R, G, B) of black-and-white filters, on camera RGB
FILTERS = {
    "none": (1.0, 1.0, 1.0),
    "yellow": (1.0, 0.9, 0.35),
    "orange": (1.0, 0.6, 0.15),
    "red": (1.0, 0.3, 0.05),
    "green": (0.5, 1.0, 0.4),
    "blue": (0.3, 0.5, 1.0),
}

# Swin2SR classical ×4 (caidas/swin2SR-classical-sr-x4-64), the architecture the models were
# fine-tuned from; written out so the checkpoint alone is enough (no download).
ARCH = dict(
    image_size=64, patch_size=1, num_channels=3, num_channels_out=3, embed_dim=180,
    depths=[6] * 6, num_heads=[6] * 6, window_size=8, mlp_ratio=2.0, qkv_bias=True,
    hidden_dropout_prob=0.0, attention_probs_dropout_prob=0.0, drop_path_rate=0.1,
    hidden_act="gelu", use_absolute_embeddings=False, layer_norm_eps=1e-5, upscale=4,
    img_range=1.0, resi_connection="1conv", upsampler="pixelshuffle", path_norm=True,
)


class RawUpscaleError(ValueError):
    pass


# ---- availability ------------------------------------------------------------------------


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def checkpoint_spec(cfg, kind: str) -> str:
    """The configured checkpoint (a path or a Hub file), else the default path."""
    from . import paths

    spec = (cfg.editing.get(SETTINGS[kind]) or "").strip()
    return spec or str(paths.data_dir() / "models" / DEFAULT_FILES[kind])


def availability(cfg, kind: str) -> dict:
    """Whether RAW upscaling (``kind`` rgb or mono) can run here, and why not."""
    spec = checkpoint_spec(cfg, kind)
    missing = [m for m in ("torch", "transformers", "rawpy", "tifffile") if not _has(m)]
    reason = ""
    if missing:
        reason = f'needs the raw extra: pip install -e ".[raw]" (missing {", ".join(missing)})'
    elif not Path(spec).expanduser().is_file() and ":" not in spec:
        reason = f"no checkpoint at {spec} (copy it there, or set editing.{SETTINGS[kind]} in the config)"
    return {"available": not reason, "reason": reason, "checkpoint": spec}


# ---- the model ---------------------------------------------------------------------------


def _build(out_channels: int):
    """The Bayer model: Swin2SR with a 4-channel first convolution (R, G1, G2, B), a 1- or
    3-channel last one, and the noise input (an offset on the first convolution's features)."""
    import torch
    from torch import nn
    from transformers import Swin2SRConfig, Swin2SRForImageSuperResolution

    class NoiseEmbedding(nn.Module):
        def __init__(self, dim: int, hidden: int = 64):
            super().__init__()
            self.net = nn.Sequential(nn.Linear(1, hidden), nn.SiLU(), nn.Linear(hidden, dim))

        def forward(self, noise):
            return self.net(noise.reshape(-1, 1).float())[:, :, None, None]

    class BayerSwin2SR(nn.Module):
        def __init__(self):
            super().__init__()
            base = Swin2SRForImageSuperResolution(Swin2SRConfig(**ARCH))
            dim = base.swin2sr.first_convolution.out_channels
            base.swin2sr.first_convolution = nn.Conv2d(4, dim, 3, 1, 1)
            last = base.upsample.final_convolution
            base.upsample.final_convolution = nn.Conv2d(last.in_channels, out_channels, 3, 1, 1)
            base.swin2sr.register_buffer("mean", torch.zeros(1, 4, 1, 1))
            self.register_buffer("output_mean", torch.zeros(1, out_channels, 1, 1))
            self.swin2sr, self.upsample = base.swin2sr, base.upsample
            self.noise_embed = NoiseEmbedding(dim)
            self._noise = None
            self.swin2sr.first_convolution.register_forward_hook(self._add_noise)

        def _add_noise(self, module, args, out):
            return out if self._noise is None else out + self.noise_embed(self._noise).to(out.dtype)

        def forward(self, x, noise=None):
            H, W = x.shape[2:]
            self._noise = noise
            try:
                rec = self.upsample(self.swin2sr(x)[0])
            finally:
                self._noise = None
            rec = rec / self.swin2sr.img_range + self.output_mean
            return rec[:, :, : H * 4, : W * 4]

    return BayerSwin2SR()


_lock = threading.Lock()
_models: dict[str, tuple] = {}  # checkpoint path -> (model, kind, conditioned on noise)


def _device() -> str:
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def load(spec: str):
    """(model, kind, noise_conditioned) for a checkpoint; loaded once and kept."""
    import torch

    from .editing import _file

    path = str(_file(spec))
    with _lock:
        if path not in _models:
            state = torch.load(path, map_location="cpu", weights_only=True)
            out = state["upsample.final_convolution.weight"].shape[0]
            if out not in (1, 3) or state["swin2sr.first_convolution.weight"].shape[1] != 4:
                raise RawUpscaleError(f"not a Bayer upscaling checkpoint: {path}")
            model = _build(out)
            missing, unexpected = model.load_state_dict(state, strict=False)
            if unexpected or any(not k.startswith("noise_embed.") for k in missing):
                raise RawUpscaleError(f"checkpoint does not match the model: {path}")
            model = model.to(_device()).eval()
            _models[path] = (model, "mono" if out == 1 else "rgb", not missing)
        return _models[path]


# ---- the RAW -----------------------------------------------------------------------------


def read_packed(path: Path) -> tuple[np.ndarray, dict]:
    """(4, H/2, W/2) R G1 G2 B, black 0, white 1, in the sensor's orientation; and the
    as-shot white balance (G = 1), colour matrix and LibRaw's flip code."""
    import rawpy

    with rawpy.imread(str(path)) as raw:
        if raw.raw_type != rawpy.RawType.Flat:
            raise RawUpscaleError("not a Bayer RAW")
        pattern = raw.raw_pattern
        desc = raw.color_desc.decode()
        if pattern is None or pattern.shape != (2, 2) or "".join(desc[c] for c in pattern.flat) != "RGGB":
            raise RawUpscaleError("only RGGB Bayer sensors are supported")
        img = raw.raw_image_visible.astype(np.float32)
        black = np.array(raw.black_level_per_channel, np.float32)
        white = float(raw.white_level)
        wb = np.array(raw.camera_whitebalance[:3], np.float64)
        if not wb.any():
            wb = np.array(raw.daylight_whitebalance[:3], np.float64)
        info = {"wb": wb / wb[1], "cam_xyz": np.array(raw.rgb_xyz_matrix[:3], np.float64), "flip": raw.sizes.flip}
    H, W = img.shape[0] // 2 * 2, img.shape[1] // 2 * 2
    packed = [np.clip((img[dy:H:2, dx:W:2] - black[c]) / (white - black[c]), 0, 1)
              for (dy, dx), c in zip([(0, 0), (0, 1), (1, 0), (1, 1)], pattern.flat)]
    return np.stack(packed), info


def check(path: Path) -> str:
    """Why this RAW cannot be upscaled ("" if it can), from its header only."""
    import rawpy

    try:
        with rawpy.imread(str(path)) as raw:
            if raw.raw_type != rawpy.RawType.Flat:
                return "not a Bayer RAW"
            pattern = raw.raw_pattern
            desc = raw.color_desc.decode()
            if pattern is None or pattern.shape != (2, 2) or "".join(desc[c] for c in pattern.flat) != "RGGB":
                return "only RGGB Bayer sensors are supported"
    except Exception as e:  # not a RAW LibRaw reads
        return f"cannot read it as a RAW ({e.__class__.__name__})"
    return ""


def output_size(path: Path) -> tuple[int, int]:
    """(width, height) of the upright ×2 result."""
    import rawpy

    with rawpy.imread(str(path)) as raw:
        h, w, flip = raw.sizes.height, raw.sizes.width, raw.sizes.flip  # the visible area
    w, h = w // 2 * 4, h // 2 * 4
    return (h, w) if flip in (5, 6) else (w, h)


def expected_bytes(path: Path) -> int:
    """About the size of the DNG (uncompressed 16-bit RGB, plus headers)."""
    w, h = output_size(path)
    return w * h * 6 + 64 * 1024


def upright(img: np.ndarray, flip: int) -> np.ndarray:
    """LibRaw's flip codes: 3 half a turn, 5 a quarter turn anticlockwise, 6 clockwise
    (a view: the DNG writer copies it once anyway)."""
    return {3: np.rot90(img, 2), 5: np.rot90(img, 1), 6: np.rot90(img, -1)}.get(flip, img)


def clip_highlights(packed: np.ndarray, wb4: np.ndarray) -> np.ndarray:
    """The packed data white-balanced, with clipped highlights made neutral: in quads with a
    clipped channel (and their neighbours) every channel capped at the lowest one's clip
    level, as darktable's "clip highlights" does."""
    import cv2

    out = packed * wb4
    clipped = cv2.dilate((packed >= 0.999).any(axis=0).astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    out[:, clipped] = np.minimum(out[:, clipped], float(wb4.min()))
    return out


# ---- black and white ---------------------------------------------------------------------


def luminance_weights(packed_wb: np.ndarray, raw_path: Path, floor: float = 0.02) -> np.ndarray:
    """Weights on white-balanced camera RGB for the photo's luminance (brightness as the eye
    sees it). CIE Y from the camera matrix needs a negative blue weight in camera RGB, which
    input gains cannot make, so this is the closest non-negative mix (each ≥ floor, sum 1) on
    the photo's own colours."""
    import rawpy
    from scipy.optimize import nnls

    with rawpy.imread(str(raw_path)) as raw:
        cam_xyz = np.array(raw.rgb_xyz_matrix[:3], np.float64)
        wb = np.array(raw.camera_whitebalance[:3], np.float64)
        if not wb.any():
            wb = np.array(raw.daylight_whitebalance[:3], np.float64)
    y = np.linalg.inv(cam_xyz)[1] / (wb / wb[1])
    y /= y.sum()
    rgb = np.stack([packed_wb[0], 0.5 * (packed_wb[1] + packed_wb[2]), packed_wb[3]]).reshape(3, -1).T
    rgb = rgb[:: max(1, len(rgb) // 200_000)].astype(np.float64)
    Y = rgb @ y
    keep = (Y > 0.03) & (rgb.max(1) < 0.95 * rgb.max())
    rgb, Y = rgb[keep], Y[keep]
    if len(Y) < 100:
        return MONO_MIX.copy()
    s = np.sqrt(Y)[:, None]
    A = np.vstack([rgb / s, 1e3 * np.ones((1, 3))])
    b = np.concatenate([(Y - rgb.sum(1) * floor) / s[:, 0], [1e3 * (1 - 3 * floor)]])
    v, _ = nnls(A, b)
    return floor + v


def mix_gains(mix) -> np.ndarray:
    """Input gains (R, G, B) that make the black-and-white model output ``mix`` (weights on
    white-balanced camera RGB), normalised so a neutral grey keeps its brightness."""
    mix = np.asarray(mix, np.float64)
    if (mix < 0).any() or not mix.any():
        raise RawUpscaleError("a black-and-white mix needs non-negative weights")
    k = mix / MONO_MIX
    k /= MONO_MIX @ k
    rel = k / k.max()
    if min(rel[0], rel[2]) < MIX_GAIN_MIN or rel[1] < MIX_G_GAIN_MIN:
        raise RawUpscaleError(f"this mix is outside what the model was trained for (gains {np.round(k, 4).tolist()})")
    return k.astype(np.float32)


# ---- running it --------------------------------------------------------------------------


def _tiled(disp: np.ndarray, run) -> np.ndarray:
    """``run`` (a BCHW tensor -> 4× its size, 3 channels) over overlapping tiles of ``disp``
    (H, W, 4)."""
    import torch

    H, W = disp.shape[:2]
    out = np.zeros((H * 4, W * 4, 3), np.float32)
    for y in range(0, H, TILE):
        for x in range(0, W, TILE):
            x0, y0 = max(0, x - PAD), max(0, y - PAD)
            x1, y1 = min(W, x + TILE + PAD), min(H, y + TILE + PAD)
            tile = disp[y0:y1, x0:x1]
            ph, pw = (-tile.shape[0]) % 8, (-tile.shape[1]) % 8  # the window size divides the input
            if ph or pw:
                tile = np.pad(tile, ((0, ph), (0, pw), (0, 0)), mode="reflect")
            t = torch.from_numpy(np.ascontiguousarray(tile)).permute(2, 0, 1)[None].to(_device())
            with torch.inference_mode():
                r = run(t)[0].float().permute(1, 2, 0).cpu().numpy()
            ox, oy = (x - x0) * 4, (y - y0) * 4
            w, h = (min(W, x + TILE) - x) * 4, (min(H, y + TILE) - y) * 4
            out[y * 4 : y * 4 + h, x * 4 : x * 4 + w] = r[oy : oy + h, ox : ox + w]
    return out


def bw_mix(packed_wb: np.ndarray, raw_path: Path, base: str = "film", filter: str = "none", mix=None) -> np.ndarray:
    """The black-and-white mix: own weights, or a base (``film``: the model's own mix;
    ``luminance``: the photo's, as the eye sees brightness) times a filter's transmission."""
    if mix is not None:
        return np.asarray(mix, np.float64)
    if filter not in FILTERS:
        raise RawUpscaleError(f"filter must be one of {', '.join(FILTERS)}")
    if base not in ("film", "luminance"):
        raise RawUpscaleError("base must be film or luminance")
    weights = luminance_weights(packed_wb, raw_path) if base == "luminance" else MONO_MIX
    return weights * np.asarray(FILTERS[filter])


def upscale_raw(
    cfg, src: Path, dst: Path, *, kind: str = "rgb", denoise: float = 0.3,
    base: str = "film", filter: str = "none", mix=None,
) -> dict:
    """Write ``src`` (a RAW) as a ×2 linear DNG at ``dst``. ``kind``: rgb or mono (black and
    white, with ``base``, ``filter`` or ``mix``: see bw_mix). Returns what was done."""
    import torch

    if kind not in KINDS:
        raise RawUpscaleError(f"kind must be one of {', '.join(KINDS)}")
    if not 0 < denoise <= 1:
        raise RawUpscaleError("denoise is the share of the noise to remove: above 0, up to 1")
    ok = availability(cfg, kind)
    if not ok["available"]:
        raise RawUpscaleError(ok["reason"])
    model, model_kind, conditioned = load(ok["checkpoint"])
    if model_kind != kind:
        raise RawUpscaleError(f"the {kind} checkpoint ({ok['checkpoint']}) is a {model_kind} model")

    packed, info = read_packed(Path(src))
    wb = info["wb"]
    wb4 = np.array([wb[0], wb[1], wb[1], wb[2]], np.float32)[:, None, None]
    x_in = clip_highlights(packed, wb4)
    del packed
    gains = np.ones(3, np.float32)
    if kind == "mono":
        weights = bw_mix(x_in, Path(src), base, filter, mix)
        gains = mix_gains(weights)
        x_in *= gains[[0, 1, 1, 2]][:, None, None]  # after the highlights: white stays white
    head = float(max(1.0, x_in.max()))
    disp = np.moveaxis(np.power(np.clip(x_in / head, 0, 1), 1 / GAMMA), 0, -1).astype(np.float32)
    del x_in

    tiff = _raw_tiff(Path(src))
    iso = tiff.iso() if tiff else None
    noise = None
    if conditioned:
        level = np.log2((iso or 200) / 200 * max(denoise, 0.01))
        noise = torch.tensor([level], dtype=torch.float32, device=_device())
    use_half = _device() == "cuda"

    def run(t):
        with torch.autocast("cuda", dtype=torch.float16, enabled=use_half):
            r = model(t, noise).float().clamp(0, 1)
        return r.expand(-1, 3, -1, -1) if r.shape[1] == 1 else r

    # In place where it can be: a 20 MP RAW's result is about 1 GB as float32
    lin = _tiled(disp, run)
    del disp
    np.power(lin, GAMMA, out=lin)
    lin *= head
    if kind == "mono":
        lin[...] = lin[..., :1]  # grey: equal after the white balance
    lin /= wb[None, None, :].astype(np.float32)
    lin = upright(lin, info["flip"])

    dst = Path(dst)
    write_dng(dst, lin, info, tiff.text(271) if tiff else "", tiff.text(272) if tiff else "")
    if tiff:
        append_exif(dst, {c: v for c, v in tiff.exif.items() if c in EXIF_TAGS}, tiff.order)
    return {
        "size": (lin.shape[1], lin.shape[0]), "kind": kind, "iso": iso,
        "denoise": denoise if conditioned else None,
        "mix": [round(float(v), 3) for v in MONO_MIX * gains] if kind == "mono" else None,
    }


# ---- the DNG -----------------------------------------------------------------------------

EXIF_PLACEHOLDER = 34666  # tifffile does not write ExifIFD (34665): renamed afterwards
# EXIF tags copied from the RAW: (code, TIFF type). Types: 2 ASCII, 3 SHORT, 4 LONG,
# 5 RATIONAL, 7 UNDEFINED, 10 SRATIONAL.
EXIF_TAGS = {
    33434: 5, 33437: 5, 34850: 3, 34855: 3, 34864: 3, 36864: 7, 36867: 2, 36868: 2, 36880: 2, 36881: 2,
    37377: 10, 37378: 5, 37380: 10, 37381: 5, 37383: 3, 37384: 3, 37385: 3, 37386: 5, 37500: 7,
    41987: 3, 41988: 5, 41989: 3, 41990: 3, 42033: 2, 42034: 5, 42035: 2, 42036: 2,
}
SIZES = {2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 10: 8}


def _srational(values, den: int = 10000) -> list[int]:
    out = []
    for v in values:
        out += [int(round(float(v) * den)), den]
    return out


def write_dng(path: Path, lin: np.ndarray, info: dict, make: str, model: str) -> None:
    """A linear DNG (LinearRaw, uncompressed 16-bit: darktable does not open deflate for
    16-bit integers) with the camera's colour matrix and white balance."""
    import tifffile

    data = np.empty(lin.shape, np.uint16)
    for y in range(0, lin.shape[0], 512):  # in bands: no full-size float copy
        band = np.clip(lin[y : y + 512], 0, 1) * 65535 + 0.5
        data[y : y + 512] = band.astype(np.uint16)
    neutral = 1.0 / info["wb"]  # the camera's white balance, as the camera colour of neutral grey
    make, model = make or "Unknown", model or "Camera"
    tags = [
        (50706, "B", 4, (1, 4, 0, 0), True),  # DNGVersion
        (50707, "B", 4, (1, 1, 0, 0), True),  # DNGBackwardVersion
        (271, "s", 0, make, True),
        (272, "s", 0, model, True),
        (50708, "s", 0, f"{make} {model}", True),  # UniqueCameraModel
        (274, "H", 1, 1, True),  # Orientation: upright already
        (50721, "2i", 9, _srational(info["cam_xyz"].ravel()), True),  # ColorMatrix1
        (50778, "H", 1, 21, True),  # CalibrationIlluminant1: D65
        (50728, "2I", 3, _srational(neutral, 1_000_000), True),  # AsShotNeutral
        (50714, "I", 1, 0, True),  # BlackLevel
        (50717, "I", 1, 65535, True),  # WhiteLevel
        (EXIF_PLACEHOLDER, "I", 1, 0, True),  # at the next free code, so the IFD stays sorted
    ]
    tifffile.imwrite(
        path, data, photometric=34892, planarconfig="contig", subfiletype=0, metadata=None,
        software="Riffle", rowsperstrip=64, extratags=tags,
    )


class RawTiff:
    """IFD0 and the EXIF sub-IFD of a TIFF-based RAW (ORF's header is "IIRO", not TIFF's,
    so tifffile does not open it): {code: (type, count, raw bytes)}."""

    def __init__(self, path: Path):
        with open(path, "rb") as f:
            self.data = f.read()
        if self.data[:2] not in (b"II", b"MM"):
            raise ValueError("not TIFF-based")
        self.order = "<" if self.data[:2] == b"II" else ">"
        self.ifd0 = self._ifd(struct.unpack(self.order + "I", self.data[4:8])[0])
        ptr = self.ifd0.get(34665)
        self.exif = self._ifd(struct.unpack(self.order + "I", ptr[2])[0]) if ptr else {}

    def _ifd(self, at: int) -> dict[int, tuple]:
        d, o = self.data, self.order
        out = {}
        for i in range(struct.unpack(o + "H", d[at : at + 2])[0]):
            e = at + 2 + 12 * i
            code, typ, count = struct.unpack(o + "HHI", d[e : e + 8])
            if typ not in SIZES:
                continue
            size = SIZES[typ] * count
            v = e + 8 if size <= 4 else struct.unpack(o + "I", d[e + 8 : e + 12])[0]
            out[code] = (typ, count, d[v : v + size])
        return out

    def text(self, code: int) -> str:
        return self.ifd0[code][2].split(b"\0")[0].decode("ascii", "replace").strip() if code in self.ifd0 else ""

    def iso(self) -> int | None:
        entry = self.exif.get(0x8827)
        return struct.unpack(self.order + "H", entry[2][:2])[0] if entry else None


def _raw_tiff(path: Path) -> RawTiff | None:
    """The RAW's TIFF structure, for its EXIF; None for RAWs that are not TIFF-based (CR3, RAF)."""
    try:
        return RawTiff(path)
    except Exception:
        return None


def _swap(typ: int, raw: bytes, src: str, dst: str) -> bytes:
    """A value's bytes in another byte order (per element; ASCII and UNDEFINED as they are)."""
    if src == dst or typ in (2, 7):
        return raw
    fmt = {3: "H", 4: "I", 5: "I", 10: "i"}[typ]
    n = len(raw) // struct.calcsize(fmt)
    return struct.pack(dst + fmt * n, *struct.unpack(src + fmt * n, raw))


def append_exif(dng: Path, exif: dict, src_order: str) -> None:
    """Append an EXIF IFD at the end of ``dng`` and point IFD0's ExifIFD tag at it."""
    import tifffile

    with tifffile.TiffFile(dng) as tf:
        dst = tf.byteorder
        pointer_at = tf.pages[0].tags[EXIF_PLACEHOLDER].valueoffset
    with open(dng, "r+b") as f:
        f.seek(0, 2)
        start = f.tell() + (f.tell() % 2)
        items = sorted(exif.items())
        head = 2 + 12 * len(items) + 4
        body, blob = bytearray(struct.pack(dst + "H", len(items))), bytearray()
        for code, (typ, count, raw) in items:
            raw = _swap(typ, raw, src_order, dst)
            if len(raw) <= 4:
                value = raw.ljust(4, b"\0")
            else:
                value = struct.pack(dst + "I", start + head + len(blob))
                blob += raw + (b"\0" if len(raw) % 2 else b"")
            body += struct.pack(dst + "HHI", code, typ, count) + value
        body += struct.pack(dst + "I", 0)
        f.seek(start)
        f.write(bytes(body) + bytes(blob))
        f.seek(pointer_at - 8)  # the entry: code, type, count, value
        f.write(struct.pack(dst + "HHII", 34665, 4, 1, start))
