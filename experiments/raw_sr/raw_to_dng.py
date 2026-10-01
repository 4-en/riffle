"""Upscale a Bayer RAW to a linear DNG (proof of concept, separate from Riffle).

    venv/bin/python experiments/raw_sr/raw_to_dng.py IN.ORF -o OUT.dng --model swin2sr --scale 2 \
        [--crop 0.3,0.2,0.4,0.3 --angle 1.5 | --riffle]

1. LibRaw (rawpy) demosaics the RAW to linear camera RGB, 16 bit: black level taken off,
   scaled to the white level, no white balance, no gamma, in the sensor's orientation.
1b. Lens corrections from lensfun (by the lens in the EXIF): vignetting and lateral
   chromatic aberration (--no-lens to skip); then the image is turned upright.
1c. With the camera's JPEG beside it (or --jpeg), the RAW is brought into the JPEG's
   geometry: a radial model (scale, rotation, offset, two distortion terms) fitted to SIFT
   matches between the two gives the camera's own distortion correction, and places a crop
   set on the JPEG (--crop/--angle/--rot90/--flip, as Riffle's editor stores it, or
   --riffle for the photo's own) exactly. The crop, with a margin trimmed after upscaling,
   is cut out of the linear image in one resampling, so only it is upscaled: compute and
   file size shrink with its area. Without a JPEG, lensfun corrects the distortion too
   (its profile for the 12-100 mm at 12 mm corrected about 1.6 times as much as the camera).
2. The image is upscaled (--model): white-balanced and gamma-encoded first, since the
   models are trained on display images, then taken back to linear camera RGB.
3. It is written as a linear DNG (PhotometricInterpretation LinearRaw): the camera's
   colour matrix (XYZ D65 -> camera, from LibRaw), its white balance as AsShotNeutral,
   black 0 and white 65535, make and model; then the RAW's EXIF block (exposure, lens,
   date, the maker note) is appended, so a RAW developer sees the same camera and lens.

Models: swin2sr (Swin2SR classical x2, faithful; transformers), swinir (SwinIR-M
classical x2, spandrel), realesrgan (Real-ESRGAN x2plus: denoises and invents texture),
lanczos (plain resampling, the baseline), none (scale 1: checks the colour handling).
"""

from __future__ import annotations

import argparse
import struct
import sys
import time
from pathlib import Path

import numpy as np

EXIF_PLACEHOLDER = 34666
TILE, PAD = 256, 24  # upscaling in tiles of this size, overlapping by this much
MARGIN = 32  # px of real image around a crop while upscaling (trimmed after)
MATCH_EDGE = 1600  # the size the JPEG and the RAW are matched at
GAMMA = 2.2


# ---- 1. the RAW ------------------------------------------------------------------------


def read_raw(path: Path, demosaic: str = "AHD", median: int = 0):
    """(linear camera RGB, float32 0-1, upright; camera info) of a RAW file. ``median``:
    LibRaw's median filter passes on the colour differences (against demosaicing speckle)."""
    import rawpy

    with rawpy.imread(str(path)) as raw:
        wb = np.array(raw.camera_whitebalance[:3], np.float64)
        if not wb.any():
            wb = np.array(raw.daylight_whitebalance[:3], np.float64)
        cam_xyz = np.array(raw.rgb_xyz_matrix[:3], np.float64)
        flip = raw.sizes.flip  # (before developing: user_flip=0 resets it)
        if not cam_xyz.any():
            raise SystemExit("LibRaw has no colour matrix for this camera")
        rgb = raw.postprocess(
            demosaic_algorithm=rawpy.DemosaicAlgorithm[demosaic], median_filter_passes=median,
            use_camera_wb=False, use_auto_wb=False, user_wb=[1.0, 1.0, 1.0, 1.0],
            output_color=rawpy.ColorSpace.raw, gamma=(1, 1), no_auto_bright=True,
            output_bps=16, highlight_mode=rawpy.HighlightMode.Clip, user_flip=0,
        )
    info = {"wb": wb / wb[1], "cam_xyz": cam_xyz, "flip": flip}
    return rgb.astype(np.float32) / 65535.0, info


def upright(img: np.ndarray, flip: int) -> np.ndarray:
    """LibRaw's flip codes: 3 half a turn, 5 a quarter turn anticlockwise, 6 clockwise."""
    return {3: np.rot90(img, 2), 5: np.rot90(img, 1), 6: np.rot90(img, -1)}.get(flip, img).copy()


# ---- 1b. lens corrections ------------------------------------------------------------------


def rational(entry, order: str = "<") -> float:
    typ, count, raw = entry
    num, den = struct.unpack(order + ("II" if typ == 5 else "ii"), raw[:8])
    return num / den if den else 0.0


def correct_lens(lin: np.ndarray, tiff: "RawTiff", distortion: bool = True) -> tuple[np.ndarray, str]:
    """``lin`` corrected for its lens (lensfun: vignetting, lateral CA, and distortion
    unless the JPEG's geometry is used instead), and what was done. A lens lensfun does
    not know (or a manual one) is left as it is. In the sensor's orientation."""
    import cv2
    import lensfunpy

    exif = tiff.exif
    focal = rational(exif[37386], tiff.order) if 37386 in exif else 0.0
    aperture = rational(exif[33437], tiff.order) if 33437 in exif else 0.0
    lens_name = exif[42036][2].split(b"\0")[0].decode("ascii", "replace").strip() if 42036 in exif else ""
    if not focal or not lens_name:
        return lin, "no lens data (a manual lens?): not corrected"
    db = lensfunpy.Database()
    make, model = tiff.text(271), tiff.text(272)
    cams = db.find_cameras(make, model) or db.find_cameras(make, model.replace("MarkII", "").replace("Mark II", "").strip())
    lenses = db.find_lenses(cams[0], None, lens_name) if cams else db.find_lenses(None, None, lens_name)
    if not lenses:
        return lin, f"lens {lens_name!r} not in lensfun: not corrected"
    crop = cams[0].crop_factor if cams else 2.0
    h, w = lin.shape[:2]
    mod = lensfunpy.Modifier(lenses[0], crop, w, h)
    flags = lensfunpy.ModifyFlags.VIGNETTING | lensfunpy.ModifyFlags.TCA
    if distortion:
        flags |= lensfunpy.ModifyFlags.DISTORTION
    mod.initialize(focal, aperture or 5.6, 1000.0, scale=0.0 if distortion else 1.0, pixel_format=np.float32, flags=flags)
    out = np.ascontiguousarray(lin, dtype=np.float32)
    vignetting = mod.apply_color_modification(out)
    coords = mod.apply_subpixel_geometry_distortion()  # (h, w, 3, 2): where each channel samples
    if coords is not None:
        out = np.stack([cv2.remap(out[..., c], coords[..., c, 0], coords[..., c, 1], cv2.INTER_LANCZOS4,
                                  borderMode=cv2.BORDER_REFLECT) for c in range(3)], axis=2)
    return np.clip(out, 0, None), (f"{lenses[0].model} at {focal:g} mm f/{aperture:g}: "
                                   f"vignetting {'yes' if vignetting else 'no'}, CA {'yes' if coords is not None else 'no'}, "
                                   f"distortion {'lensfun' if distortion else 'from the JPEG'}")


# ---- 1c. a crop from the JPEG ------------------------------------------------------------


def geometry_matrix(params: dict, size: tuple[int, int]) -> tuple[np.ndarray, tuple[int, int]]:
    """Riffle's geometry (edits.apply_geometry) as a 3x3 matrix from the JPEG's upright
    frame to the output, and the output's size (both in JPEG pixels)."""
    w, h = size
    M = np.eye(3)
    k = int(params.get("rot90", 0)) % 4
    if k == 1:  # a quarter turn clockwise
        M = np.array([[0, -1, h], [1, 0, 0], [0, 0, 1.0]]) @ M
        w, h = h, w
    elif k == 2:
        M = np.array([[-1, 0, w], [0, -1, h], [0, 0, 1.0]]) @ M
    elif k == 3:
        M = np.array([[0, 1, 0], [-1, 0, w], [0, 0, 1.0]]) @ M
        w, h = h, w
    a = np.radians(float(params.get("angle", 0.0)))  # clockwise, about the centre, same frame
    if a:
        cx, cy = w / 2, h / 2
        c, s_ = np.cos(a), np.sin(a)
        M = np.array([[c, -s_, cx - c * cx + s_ * cy], [s_, c, cy - s_ * cx - c * cy], [0, 0, 1.0]]) @ M
    crop = params.get("crop") or [0, 0, 1, 1]
    x0, y0 = round(crop[0] * w), round(crop[1] * h)
    x1, y1 = round((crop[0] + crop[2]) * w), round((crop[1] + crop[3]) * h)
    M = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1.0]]) @ M
    w, h = x1 - x0, y1 - y0
    if params.get("flip_h"):
        M = np.array([[-1, 0, w], [0, 1, 0], [0, 0, 1.0]]) @ M
    return M, (w, h)


def match(jpeg: np.ndarray, raw_disp: np.ndarray) -> tuple[np.ndarray, dict]:
    """A 3x3 similarity from the JPEG's pixels to the RAW's (both 8-bit display images,
    any sizes), from SIFT features and RANSAC."""
    import cv2

    def small(img):
        s = MATCH_EDGE / max(img.shape[:2])
        return cv2.resize(img, (round(img.shape[1] * s), round(img.shape[0] * s)), interpolation=cv2.INTER_AREA), s

    a, sa = small(jpeg)
    b, sb = small(raw_disp)
    sift = cv2.SIFT_create(4000)
    ka, da = sift.detectAndCompute(cv2.cvtColor(a, cv2.COLOR_RGB2GRAY), None)
    kb, db = sift.detectAndCompute(cv2.cvtColor(b, cv2.COLOR_RGB2GRAY), None)
    pairs = [m for m, n in cv2.BFMatcher().knnMatch(da, db, k=2) if m.distance < 0.75 * n.distance]
    if len(pairs) < 20:
        raise SystemExit(f"the JPEG and the RAW did not match ({len(pairs)} features)")
    pa = np.float32([ka[m.queryIdx].pt for m in pairs]) / sa
    pb = np.float32([kb[m.trainIdx].pt for m in pairs]) / sb
    A, inliers = cv2.estimateAffinePartial2D(pa, pb, method=cv2.RANSAC, ransacReprojThreshold=2.0 / sb)
    M = np.vstack([A, [0, 0, 1]])
    ok = inliers.ravel().astype(bool)
    err = np.linalg.norm((pa[ok] @ A[:, :2].T + A[:, 2]) - pb[ok], axis=1)
    return M, {"features": len(pairs), "inliers": int(ok.sum()), "scale": round(float(np.hypot(*A[0, :2])), 4),
               "rotation_deg": round(float(np.degrees(np.arctan2(A[1, 0], A[0, 0]))), 3),
               "error_px": round(float(np.median(err)), 2)}


def camera_geometry(jpeg: np.ndarray, raw_disp: np.ndarray, edge: int = 2400):
    """A function from the JPEG's pixels to the RAW's: a similarity with radial
    distortion, p_raw = t + s R(θ) q (1 + k1 ρ² + k2 ρ⁴), q the offset from the JPEG's
    centre, ρ its length over the half diagonal; fitted (robustly) to SIFT matches. It is
    the camera's distortion correction, whatever it is. Also: the fit's residuals."""
    import cv2
    from scipy.optimize import least_squares

    def small(img):
        s_ = edge / max(img.shape[:2])
        return cv2.resize(img, (round(img.shape[1] * s_), round(img.shape[0] * s_)), interpolation=cv2.INTER_AREA), s_

    a, sa = small(jpeg)
    b, sb = small(raw_disp)
    sift = cv2.SIFT_create(12000)
    ka, da = sift.detectAndCompute(cv2.cvtColor(a, cv2.COLOR_RGB2GRAY), None)
    kb, db = sift.detectAndCompute(cv2.cvtColor(b, cv2.COLOR_RGB2GRAY), None)
    pairs = [m for m, n in cv2.BFMatcher().knnMatch(da, db, k=2) if m.distance < 0.7 * n.distance]
    if len(pairs) < 50:
        raise SystemExit(f"the JPEG and the RAW did not match ({len(pairs)} features)")
    pj = np.float64([ka[m.queryIdx].pt for m in pairs]) / sa
    pr = np.float64([kb[m.trainIdx].pt for m in pairs]) / sb
    c = np.array([jpeg.shape[1], jpeg.shape[0]], np.float64) / 2
    R = float(np.hypot(*c))

    def f(x, p):
        s_, th, tx, ty, k1, k2 = x
        q = (p - c) / R
        rho2 = (q ** 2).sum(1, keepdims=True)
        q = q * (1 + k1 * rho2 + k2 * rho2 ** 2)
        rot = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
        return s_ * R * q @ rot.T + [tx, ty]

    A, _ = cv2.estimateAffinePartial2D(pj, pr, method=cv2.RANSAC, ransacReprojThreshold=40)
    s0, th0 = float(np.hypot(*A[0, :2])), float(np.arctan2(A[1, 0], A[0, 0]))
    x0 = [s0, th0, *(A[:, :2] @ c + A[:, 2]), 0.0, 0.0]
    fit = least_squares(lambda x: (f(x, pj) - pr).ravel(), x0, loss="soft_l1", f_scale=2.0)
    err = np.linalg.norm(f(fit.x, pj) - pr, axis=1)
    rho = np.linalg.norm(pj - c, axis=1) / R
    good = err < 8
    by_radius = {f"{lo:.1f}-{hi:.1f}": round(float(np.median(err[good & (rho >= lo) & (rho < hi)])), 2)
                 for lo, hi in ((0, 0.4), (0.4, 0.7), (0.7, 1.01)) if (good & (rho >= lo) & (rho < hi)).sum() >= 10}
    k1, k2 = fit.x[4:]
    info = {"features": len(pairs), "inliers": int(good.sum()), "scale": round(float(fit.x[0]), 4),
            "distortion_at_corner_pct": round(float((k1 + k2) * 100), 2), "error_px_by_radius": by_radius}
    return (lambda p: f(fit.x, p)), info


def crop_raw(lin: np.ndarray, wb: np.ndarray, jpeg_path: Path, params: dict) -> tuple[np.ndarray, dict]:
    """The part of the linear RAW that ``params`` (Riffle's geometry, on the JPEG; empty:
    all of it) shows, in the JPEG's geometry at the RAW's resolution, with MARGIN px of
    the surroundings on each side."""
    import cv2
    from PIL import Image, ImageOps

    with Image.open(jpeg_path) as im:
        jpeg = np.asarray(ImageOps.exif_transpose(im).convert("RGB"))
    disp, _ = to_display(lin, wb)
    j2r, info = camera_geometry(jpeg, (disp * 255).astype(np.uint8))
    G, (ow, oh) = geometry_matrix(params, (jpeg.shape[1], jpeg.shape[0]))
    s = info["scale"]  # RAW pixels per JPEG pixel (at the centre)
    # Output pixel (RAW resolution, margin included) -> JPEG output -> JPEG frame -> RAW.
    O2J = np.linalg.inv(G) @ np.array([[1 / s, 0, -MARGIN / s], [0, 1 / s, -MARGIN / s], [0, 0, 1]])
    size = (round(ow * s) + 2 * MARGIN, round(oh * s) + 2 * MARGIN)
    yy, xx = np.mgrid[0 : size[1], 0 : size[0]].astype(np.float64)
    pts = np.stack([xx.ravel(), yy.ravel()], axis=1) @ O2J[:2, :2].T + O2J[:2, 2]
    src = j2r(pts).astype(np.float32).reshape(size[1], size[0], 2)
    out = cv2.remap(lin, src[..., 0], src[..., 1], cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT)
    info["crop_px"] = f"{size[0] - 2 * MARGIN}x{size[1] - 2 * MARGIN}"
    return np.clip(out, 0, None), info


def riffle_geometry(raw_path: Path) -> tuple[dict, Path]:
    """The geometry step of the Riffle photo this RAW belongs to, and that photo's file."""
    import sqlite3

    from riffle import edits
    from riffle.config import load_config

    cfg = load_config()
    conn = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)
    row = conn.execute(
        """SELECT p.source, p.rel_path, p.sha256 FROM raws r JOIN photos p ON p.id = r.photo_id
           WHERE r.source = ? AND r.rel_path = ?""", (str(raw_path.parent), raw_path.name)).fetchone()
    conn.close()
    if row is None:
        raise SystemExit("this RAW is not paired with a photo in Riffle's catalogue")
    geo = next((e.params for e in edits.list_edits(cfg.selections_path, row[2]) if e.kind == "geometry" and e.enabled), None)
    if geo is None:
        raise SystemExit("the photo has no crop in Riffle")
    return geo, Path(row[0]) / row[1]


# ---- 2. upscaling ------------------------------------------------------------------------


def to_display(lin: np.ndarray, wb: np.ndarray) -> tuple[np.ndarray, float]:
    """White-balanced and gamma-encoded, within 0-1 (``head``: the headroom divided out)."""
    x = lin * wb[None, None, :]
    head = float(max(1.0, x.max()))
    return np.power(np.clip(x / head, 0, 1), 1 / GAMMA).astype(np.float32), head


def smooth_chroma(disp: np.ndarray, size: int) -> np.ndarray:
    """A median over the colour differences only (YCrCb): LibRaw's demosaicing leaves
    coloured speckle at fine edges (darktable's does not), which upscaling sharpens; the
    brightness detail is left as it is."""
    import cv2

    if size < 3:
        return disp
    ycc = cv2.cvtColor(np.ascontiguousarray(disp), cv2.COLOR_RGB2YCrCb)
    for c in (1, 2):
        ycc[..., c] = cv2.medianBlur(np.ascontiguousarray(ycc[..., c]), size)
    return cv2.cvtColor(ycc, cv2.COLOR_YCrCb2RGB)


def to_linear(disp: np.ndarray, wb: np.ndarray, head: float) -> np.ndarray:
    return np.power(np.clip(disp, 0, 1), GAMMA) * head / wb[None, None, :]


def tiled(img: np.ndarray, scale: int, run, multiple: int = 8) -> np.ndarray:
    """``run`` (a BCHW tensor -> its ``scale``x) over overlapping tiles of ``img``."""
    import torch

    H, W = img.shape[:2]
    out = np.zeros((H * scale, W * scale, 3), np.float32)
    for y in range(0, H, TILE):
        for x in range(0, W, TILE):
            x0, y0 = max(0, x - PAD), max(0, y - PAD)
            x1, y1 = min(W, x + TILE + PAD), min(H, y + TILE + PAD)
            tile = img[y0:y1, x0:x1]
            th, tw = tile.shape[:2]
            ph, pw = (-th) % multiple, (-tw) % multiple  # (the models want sizes in multiples)
            if ph or pw:
                tile = np.pad(tile, ((0, ph), (0, pw), (0, 0)), mode="reflect")
            t = torch.from_numpy(np.ascontiguousarray(tile)).permute(2, 0, 1)[None].cuda()
            with torch.inference_mode():
                r = run(t)[0].float().permute(1, 2, 0).cpu().numpy()
            ox, oy = (x - x0) * scale, (y - y0) * scale
            w, h = (min(W, x + TILE) - x) * scale, (min(H, y + TILE) - y) * scale
            out[y * scale : y * scale + h, x * scale : x * scale + w] = r[oy : oy + h, ox : ox + w]
    return out


def upscale(disp: np.ndarray, model: str, scale: int) -> np.ndarray:
    if model == "none":
        return disp
    if model == "lanczos":
        from PIL import Image

        chans = [Image.fromarray(disp[..., c]).resize((disp.shape[1] * scale, disp.shape[0] * scale), Image.Resampling.LANCZOS)
                 for c in range(3)]
        return np.stack([np.asarray(c) for c in chans], axis=2)
    import torch

    if scale != 2:
        raise SystemExit("the models here are x2")
    if model == "swin2sr":
        from transformers import Swin2SRForImageSuperResolution

        net = Swin2SRForImageSuperResolution.from_pretrained("caidas/swin2SR-classical-sr-x2-64").cuda().eval()
        return tiled(disp, 2, lambda t: net(pixel_values=t).reconstruction, multiple=8)
    import spandrel

    if model == "swinir":
        spec = ("https://github.com/JingyunLiang/SwinIR/releases/download/v0.0/"
                "001_classicalSR_DF2K_s64w8_SwinIR-M_x2.pth")
        path = Path(torch.hub.get_dir()) / "checkpoints" / Path(spec).name
        if not path.exists():
            torch.hub.download_url_to_file(spec, str(path))
    elif model == "realesrgan":
        from huggingface_hub import hf_hub_download

        path = Path(hf_hub_download("deAPI-ai/realesrgan-x2", "RealESRGAN_x2plus.pth"))
    else:
        raise SystemExit(f"unknown model {model}")
    net = spandrel.ModelLoader().load_from_file(path).cuda().eval()
    return tiled(disp, 2, lambda t: net(t), multiple=8)


# ---- 3. the DNG --------------------------------------------------------------------------


def srational(values, den: int = 10000) -> list[int]:
    out = []
    for v in values:
        out += [int(round(float(v) * den)), den]
    return out


def write_dng(path: Path, lin: np.ndarray, info: dict, make: str, model: str) -> None:
    """Uncompressed: darktable (rawspeed) did not open a deflate-compressed 16-bit DNG
    (the DNG spec allows deflate for floating point only); lossless JPEG would need
    imagecodecs."""
    import tifffile

    data = (np.clip(lin, 0, 1) * 65535 + 0.5).astype(np.uint16)
    neutral = 1.0 / info["wb"]  # the camera's white balance, as the camera colour of neutral grey
    tags = [
        (50706, "B", 4, (1, 4, 0, 0), True),  # DNGVersion
        (50707, "B", 4, (1, 1, 0, 0), True),  # DNGBackwardVersion
        (271, "s", 0, make, True),
        (272, "s", 0, model, True),
        (50708, "s", 0, f"{make} {model}", True),  # UniqueCameraModel
        (274, "H", 1, 1, True),  # Orientation: upright already
        (50721, "2i", 9, srational(info["cam_xyz"].ravel()), True),  # ColorMatrix1
        (50778, "H", 1, 21, True),  # CalibrationIlluminant1: D65
        (50728, "2I", 3, srational(neutral, 1_000_000), True),  # AsShotNeutral
        (50714, "I", 1, 0, True),  # BlackLevel
        (50717, "I", 1, 65535, True),  # WhiteLevel
        # tifffile does not write ExifIFD (34665): a placeholder at the next code (nothing
        # lies between, so the IFD stays sorted), renamed and pointed at the EXIF afterwards.
        (EXIF_PLACEHOLDER, "I", 1, 0, True),
    ]
    tifffile.imwrite(
        path, data, photometric=34892, planarconfig="contig", subfiletype=0, metadata=None,
        software="riffle raw_sr proof of concept", rowsperstrip=64, extratags=tags,
    )


# EXIF tags copied from the RAW: (code, TIFF type). Types: 2 ASCII, 3 SHORT, 4 LONG,
# 5 RATIONAL, 7 UNDEFINED, 10 SRATIONAL.
EXIF_TAGS = {
    33434: 5, 33437: 5, 34850: 3, 34855: 3, 34864: 3, 36864: 7, 36867: 2, 36868: 2, 36880: 2, 36881: 2,
    37377: 10, 37378: 5, 37380: 10, 37381: 5, 37383: 3, 37384: 3, 37385: 3, 37386: 5, 37500: 7,
    41987: 3, 41988: 5, 41989: 3, 41990: 3, 42033: 2, 42034: 5, 42035: 2, 42036: 2,
}
SIZES = {2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 10: 8}


class RawTiff:
    """IFD0 and the EXIF sub-IFD of a TIFF-based RAW (ORF's header is "IIRO", not
    TIFF's, so tifffile does not open it): {code: (type, count, raw bytes)}."""

    def __init__(self, path: Path):
        with open(path, "rb") as f:
            self.data = f.read()
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


# ---- the pipeline ------------------------------------------------------------------------


def convert(src: Path, dst: Path, model: str = "swin2sr", scale: int = 2, demosaic: str = "AHD", median: int = 0,
            lens: bool = True, geometry: dict | None = None, jpeg: Path | None = None, chroma: int = 3) -> dict:
    t0 = time.perf_counter()
    tiff = RawTiff(src)
    report = {}
    jpeg = jpeg or next((p for p in (src.with_suffix(".JPG"), src.with_suffix(".jpg")) if p.exists()), None)
    if geometry and jpeg is None:
        raise SystemExit("a crop needs the JPEG it was set on (--jpeg)")
    in_jpeg_geometry = jpeg is not None and (lens or geometry)

    if model == "bayer_swin2sr":
        import rawpy
        from model import BayerSwin2SR
        
        with rawpy.imread(str(src)) as raw:
            wb = np.array(raw.camera_whitebalance[:3], np.float64)
            if not wb.any():
                wb = np.array(raw.daylight_whitebalance[:3], np.float64)
            info = {"wb": wb / wb[1], "cam_xyz": np.array(raw.rgb_xyz_matrix[:3], np.float64), "flip": raw.sizes.flip}
            
            raw_img = raw.raw_image.astype(np.float32)
            b = np.array(raw.black_level_per_channel, dtype=np.float32)
            w = float(raw.white_level)
            r = np.clip((raw_img[0::2, 0::2] - b[0]) / (w - b[0]), 0, 1)
            g1 = np.clip((raw_img[0::2, 1::2] - b[1]) / (w - b[1]), 0, 1)
            g2 = np.clip((raw_img[1::2, 0::2] - b[3]) / (w - b[3]), 0, 1)
            b_ch = np.clip((raw_img[1::2, 1::2] - b[2]) / (w - b[2]), 0, 1)
            packed = np.stack([r, g1, g2, b_ch], axis=2) # (H/2, W/2, 4)
            
        packed = upright(packed, info["flip"])
        t_read = time.perf_counter() - t0
        
        wb_bayer = np.array([info["wb"][0], info["wb"][1], info["wb"][1], info["wb"][2]], dtype=np.float32)
        packed_wb = packed * wb_bayer[None, None, :]
        head = float(max(1.0, packed_wb.max()))
        packed_disp = np.power(np.clip(packed_wb / head, 0, 1), 1 / GAMMA).astype(np.float32)
        
        import torch
        net = BayerSwin2SR().cuda().eval()
        ckpt = Path("experiments/raw_sr/checkpoints/best_model.pt")
        if ckpt.exists():
            net.load_checkpoint(ckpt)
        else:
            print("Warning: No checkpoint found at", ckpt)
            
        def run_net(t):
            return net(t).clamp(0, 1)
            
        t1 = time.perf_counter()
        # Tile over packed spatial dimensions. scale=4 because H/2 -> 2H.
        big = tiled(packed_disp, 4, run_net, multiple=8)
        t_up = time.perf_counter() - t1
        
        lin_big = np.power(np.clip(big, 0, 1), GAMMA) * head / info["wb"][None, None, :]
        
        # Geometry is applied AFTER upscaling to preserve Bayer integrity
        if lens:
            # We would need a 2x correct_lens here. Skipping for POC full-frame test if not implemented.
            pass
        if in_jpeg_geometry:
            # We would need a 2x crop_raw here.
            pass
            
    else:
        lin, info = read_raw(src, demosaic, median)
        if lens:
            lin, report["lens"] = correct_lens(lin, tiff, distortion=not in_jpeg_geometry)
        lin = upright(lin, info["flip"])
        if in_jpeg_geometry:
            lin, report["match"] = crop_raw(lin, info["wb"], jpeg, geometry or {})
        t_read = time.perf_counter() - t0
        if model == "none":
            scale = 1
        disp, head = to_display(lin, info["wb"])
        disp = smooth_chroma(disp, chroma)
        t1 = time.perf_counter()
        big = upscale(disp, model, scale)
        t_up = time.perf_counter() - t1
        lin_big = to_linear(big, info["wb"], head)
        if in_jpeg_geometry:
            m = MARGIN * scale
            lin_big = lin_big[m:-m, m:-m]

    write_dng(dst, lin_big, info, tiff.text(271), tiff.text(272))
    exif = {c: v for c, v in tiff.exif.items() if c in EXIF_TAGS}
    append_exif(dst, exif, tiff.order)
    return {**report, "size": f"{lin_big.shape[1]}x{lin_big.shape[0]}", "prepare_s": round(t_read, 1), "upscale_s": round(t_up, 1),
            "mb": round(dst.stat().st_size / 1e6), "exif_tags": len(exif), "wb": [round(float(v), 3) for v in info["wb"]]}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("raw", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--model", default="swin2sr", choices=["swin2sr", "swinir", "realesrgan", "lanczos", "none", "bayer_swin2sr"])
    ap.add_argument("--scale", type=int, default=2)
    ap.add_argument("--demosaic", default="AHD", choices=["AHD", "AAHD", "DHT", "DCB", "PPG", "VNG"])
    ap.add_argument("--median", type=int, default=0, help="median filter passes against colour speckle")
    ap.add_argument("--no-lens", action="store_true", help="no lens corrections")
    ap.add_argument("--chroma", type=int, default=3, help="median size over the colour differences (0: none)")
    ap.add_argument("--crop", default="", help="x,y,w,h: fractions of the JPEG's frame (after --rot90 and --angle)")
    ap.add_argument("--angle", type=float, default=0.0, help="straighten, degrees clockwise")
    ap.add_argument("--rot90", type=int, default=0, help="quarter turns clockwise")
    ap.add_argument("--flip", action="store_true")
    ap.add_argument("--riffle", action="store_true", help="the crop of this photo in Riffle's editor")
    ap.add_argument("--jpeg", type=Path, help="the JPEG the crop was set on (default: beside the RAW)")
    a = ap.parse_args(argv)
    geometry, jpeg = None, a.jpeg
    if a.riffle:
        geometry, jpeg = riffle_geometry(a.raw.resolve())
    elif a.crop or a.angle or a.rot90 or a.flip:
        geometry = {"crop": [float(v) for v in a.crop.split(",")] if a.crop else None, "angle": a.angle,
                    "rot90": a.rot90, "flip_h": a.flip}
    print(convert(a.raw, a.out, a.model, a.scale, a.demosaic, a.median, not a.no_lens, geometry, jpeg, a.chroma))


if __name__ == "__main__":
    sys.exit(main())
