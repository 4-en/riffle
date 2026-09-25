"""Shared image loading: optional HEIC support, orientation, sRGB conversion."""

from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image, ImageCms, ImageOps

log = logging.getLogger(__name__)

# Archive originals can be large (stitched panoramas, upscaled exports).
Image.MAX_IMAGE_PIXELS = 400_000_000

try:  # optional dependency
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass

_SRGB = ImageCms.createProfile("sRGB")


def _to_srgb(im: Image.Image) -> Image.Image:
    icc = im.info.get("icc_profile")
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            mode = "RGB" if im.mode not in ("RGB", "CMYK") else im.mode
            if im.mode != mode:
                im = im.convert(mode)
            return ImageCms.profileToProfile(im, src, _SRGB, outputMode="RGB")
        except Exception as e:  # noqa: BLE001 - fall back to a plain conversion
            log.debug("ICC conversion failed: %s", e)
    return im


def _to_rgb(im: Image.Image) -> Image.Image:
    if im.mode in ("I;16", "I;16B", "I;16L", "I"):
        # 16-bit greyscale: scale to 8 bits before converting.
        im = im.point(lambda v: v / 256).convert("L")
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.getchannel("A"))
        return bg
    return im.convert("RGB") if im.mode != "RGB" else im


def open_image(path: Path, max_edge: int | None = None) -> Image.Image:
    """Load an image upright, in 8-bit sRGB, optionally downsampled so the long
    edge is at most ``max_edge``. The source file is only read."""
    with Image.open(path) as im:
        if max_edge and im.format == "JPEG":
            im.draft("RGB", (max_edge, max_edge))  # fast DCT-domain downscale
        im.load()
        icc = im.info.get("icc_profile")
        im = ImageOps.exif_transpose(im)
        if icc:
            im.info["icc_profile"] = icc
        if max_edge and max(im.size) > max_edge * 2:
            # Cheap pre-shrink keeps colour conversion fast on huge images.
            im.thumbnail((max_edge * 2, max_edge * 2), Image.Resampling.BOX)
            if icc:
                im.info["icc_profile"] = icc
        im = _to_srgb(im)
        return _to_rgb(im)


def resize_long_edge(im: Image.Image, max_edge: int) -> Image.Image:
    if max(im.size) <= max_edge:
        return im.copy()
    out = im.copy()
    out.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    return out
