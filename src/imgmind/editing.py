"""Editing tools and their models (the editor's Heal, Remove, Replace; Upscale on export).

Each tool has recommended models and accepts a custom one: a Hugging Face repo
("org/model", a diffusers pipeline), a file in a repo ("org/model:file.pt"), or a local
path (a checkpoint file, or a diffusers folder). Models load on first use and stay
loaded while the editor is used (``release_idle`` frees them after a while).

A retouch works on the masked region with some of its surroundings (the context), at
the model's working size, and returns a patch: the changed pixels over the mask's box,
with a feathered mask to paste them through (edits.py stores both). Nothing here writes
files.

- Heal: OpenCV's inpainting (Telea): no model, instant; for spots, dust, small blemishes.
- Remove: LaMa (big-lama), a model trained to fill holes with what would be behind them;
  loaded with spandrel, or as TorchScript (the format IOPaint uses).
- Replace: a diffusers inpainting pipeline (SDXL by default) with a prompt: 1-4 candidates.
- Edit with a prompt: an instruction-following model (Qwen-Image 2.1) told what to change.
  With a painted area it edits that part (with its context) and only the area changes;
  without one it edits the whole photo at about 1 megapixel (in one of the model's own aspect
  ratios), and the photo's own fine
  detail is put back wherever the edit left the picture as it was.
- Upscale: a spandrel super-resolution model (Real-ESRGAN ×4 by default), in tiles.
"""

from __future__ import annotations

import importlib.util
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

CONTEXT = 1.5  # the context around the mask's box, as a factor of its size…
MIN_CONTEXT = 512  # …but at least this many pixels on the long side
# The models' working sizes, as areas: a context up to this many pixels is used 1:1 (more
# context costs no resolution, so a small mask is given at least this much of the scene);
# a larger one is scaled down to it.
LAMA_AREA = 1024 * 1024
DIFFUSION_AREA = 1024 * 1024  # SDXL, FLUX
SD15_AREA = 512 * 512  # Stable Diffusion 1.5
# Qwen-Image 2.1's sizes (its model card), about 4 megapixels, one per aspect ratio. An image
# is edited at the shape closest to its own (stretched a little to fit, and back afterwards),
# at half the size: on a 24 GB card the full size took 5 minutes, followed the instruction
# worse and showed stripes from the tiled decode, where half took a minute and kept the rest
# of the photo intact.
QWEN_NATIVE = ((2048, 2048), (2400, 1792), (1792, 2400), (2528, 1696), (1696, 2528), (2752, 1536), (1536, 2752))
QWEN_SIZES = tuple((w // 2 // 32 * 32, h // 2 // 32 * 32) for w, h in QWEN_NATIVE)
# The instruction model does not see the mask: it redraws what it is shown, and a crop that
# is mostly the thing to change leaves it too little to understand (it kept a tray it was
# told to remove, and redrew it larger across the mask's edge). So a painted area is edited
# with the whole photo around it, unless it is small: then a crop of a few times its size.
INSTRUCT_SMALL = 0.25  # a painted box under this fraction of the photo's width and height…
INSTRUCT_CONTEXT, INSTRUCT_MIN_CONTEXT = 4.0, 1024  # …gets this much context (at least this many px)
DETAIL_TOLERANCE = 10.0  # 0-255: where the edit differs from the photo by less, its detail is kept
QWEN_BASE = "Qwen/Qwen-Image-2.1"  # the text encoder, VAE and scheduler for a single-file transformer
QWEN_GGUF = "abenzerps/Qwen-Image-2.1-Uncensored-GGUF"  # its `base` branch: the official weights
TILE, TILE_PAD = 512, 32  # upscaling in tiles, overlapping by this much
IDLE_SECONDS = 300
VIEW_EDGE = 1600  # the editor's view of a photo: mask blur is given in its pixels
DEFAULT_BLUR = 3.2  # (0.2 % of the long side)
# Replace below full denoising starts from the image with the mask's area filled so:
# original (what is there), heal (OpenCV, from the edges), remove (LaMa), noise (the
# surroundings' colours with noise: new detail, the colours kept).
FILLS = ("original", "heal", "remove", "noise")
NOISE = 32  # 0-255: the noise fill's spread…
NOISE_CELL = 8  # …in blotches this many pixels across (a latent cell of SD's VAE): per-pixel
# noise is read as fine texture, and at middling denoising a whole picture is painted into it
STEPS = {"inpaint": 30, "instruct": 30}  # sampling steps by default (Qwen's example uses 40; 30 is a quarter faster)


@dataclass(frozen=True)
class ModelChoice:
    key: str
    label: str
    spec: str  # "builtin", "org/repo", "org/repo:file", "org/repo@revision:file", or a path
    note: str
    size_gb: float = 0.0
    gpu: bool = False  # needs an NVIDIA GPU (CUDA)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Tool:
    key: str
    label: str
    description: str
    kind: str  # opencv | fill (LaMa-like) | diffusion | instruct | upscale | segment
    models: tuple[ModelChoice, ...]
    prompt: bool = False
    mask_optional: bool = False  # runs on the whole photo when nothing is painted


TOOLS = {
    t.key: t
    for t in (
        Tool("heal", "Heal", "Spots, dust and small blemishes, filled from their surroundings.", "opencv",
             (ModelChoice("opencv", "OpenCV (Telea)", "builtin", "No model; instant."),)),
        Tool("remove", "Remove", "Objects and people, filled with what would be behind them.", "fill",
             (ModelChoice("lama", "LaMa", "fashn-ai/LaMa:big-lama.pt", "Fast and clean for removing things; no prompt.", 0.2),)),
        Tool("inpaint", "Replace", "Paint over an area and describe what should be there.", "diffusion",
             (ModelChoice("sdxl", "SDXL inpainting", "diffusers/stable-diffusion-xl-1.0-inpainting-0.1",
                          "Good quality at 1024 px.", 6.9, True),
              ModelChoice("sd15", "Stable Diffusion 1.5 inpainting", "stable-diffusion-v1-5/stable-diffusion-inpainting",
                          "Smaller and faster, lower quality.", 4.3, True),
              ModelChoice("flux-fill", "FLUX.1 Fill", "black-forest-labs/FLUX.1-Fill-dev",
                          "The best results; very large (offloaded on a 24 GB GPU); needs a Hugging Face login and its licence.", 34, True)),
             prompt=True),
        Tool("instruct", "Edit with a prompt",
             "Say what to change. Paint an area to change only that, or nothing to edit the whole photo.", "instruct",
             (ModelChoice("qwen-q4", "Qwen-Image 2.1 (Q4_K_M)", f"{QWEN_GGUF}@base:qwen-image-2.1-Q4_K_M.gguf",
                          "The official weights, 4-bit; about a minute an edit on an RTX 3090 (the text encoder waits in system memory).", 23, True),
              ModelChoice("qwen-q8", "Qwen-Image 2.1 (Q8_0)", f"{QWEN_GGUF}@base:qwen-image-2.1-Q8_0.gguf",
                          "The official weights, 8-bit: closer to full quality, 3 GB larger.", 26, True),
              ModelChoice("qwen-full", "Qwen-Image 2.1 (full)", QWEN_BASE,
                          "Unquantised (bf16); offloaded to system memory on a 24 GB GPU, so slower.", 33, True)),
             prompt=True, mask_optional=True),
        Tool("segment", "Find", "Find things by name and mask them, for removing or replacing them in many photos.", "segment",
             (ModelChoice("gdino-sam2", "Grounding DINO + SAM 2.1",
                          "IDEA-Research/grounding-dino-base+facebook/sam2.1-hiera-large",
                          "Finds what the words name, then cuts it out precisely.", 3.7),
              ModelChoice("gdino-sam2-small", "Grounding DINO tiny + SAM 2.1 small",
                          "IDEA-Research/grounding-dino-tiny+facebook/sam2.1-hiera-small",
                          "Smaller and faster; misses more.", 0.9))),
        Tool("upscale", "Upscale", "Enlarge on export (×2 or ×4).", "upscale",
             (ModelChoice("realesrgan", "Real-ESRGAN ×4", "lllyasviel/Annotators:RealESRGAN_x4plus.pth",
                          "A good general upscaler for photos.", 0.07),)),
    )
}


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _cuda() -> bool:
    try:
        import torch

        return torch.cuda.is_available()
    except Exception:
        return False


def resolve(tool: str, choice: str | None) -> tuple[str, str]:
    """(label, spec) for a tool's model setting: a catalogue key, or a custom spec."""
    t = TOOLS[tool]
    choice = (choice or "").strip()
    for m in t.models:
        if choice in ("", m.key):
            return m.label, m.spec
    return choice, choice


def availability(tool: str, choice: str | None = None) -> dict:
    """Whether a tool can run with this model here, and why not."""
    t = TOOLS[tool]
    _, spec = resolve(tool, choice)
    reason = ""
    if t.kind == "opencv" and not _has("cv2"):
        reason = "needs OpenCV: pip install opencv-python-headless"
    elif t.kind == "segment" and not (_has("torch") and _has("transformers")):
        reason = 'needs the edit extra: pip install -e ".[edit]"'
    elif t.kind in ("fill", "upscale") and not (_has("torch") and _has("spandrel")):
        reason = 'needs the edit extra: pip install -e ".[edit]"'
    elif t.kind in ("diffusion", "instruct"):
        if not _has("diffusers"):
            reason = 'needs the edit extra: pip install -e ".[edit]"'
        elif not _cuda():
            reason = "needs an NVIDIA GPU (CUDA)"
        elif t.kind == "instruct" and not _has_qwen21():
            reason = "needs a diffusers with Qwen-Image 2.1: pip install git+https://github.com/huggingface/diffusers"
        elif t.kind == "instruct" and spec.endswith(".gguf") and not _has("gguf"):
            reason = 'needs gguf: pip install -e ".[edit]"'
    return {"available": not reason, "reason": reason, "spec": spec}


# ---- loading ---------------------------------------------------------------------------

_lock = threading.Lock()
_loaded: dict[tuple[str, str], list] = {}  # (tool, spec) -> [model, last used]


def _has_qwen21() -> bool:
    try:
        import diffusers

        return hasattr(diffusers, "QwenImage21Pipeline")
    except Exception:
        return False


def _file(spec: str) -> Path:
    """A local path, or a file from a Hub repo ("org/repo:file", "org/repo@revision:file")."""
    p = Path(spec).expanduser()
    if p.exists():
        return p
    if ":" in spec:
        from huggingface_hub import hf_hub_download

        repo, name = spec.split(":", 1)
        repo, _, revision = repo.partition("@")
        return Path(hf_hub_download(repo, name, revision=revision or None))
    raise FileNotFoundError(f"not a file, and not 'repo:file': {spec}")


def _is_file_spec(spec: str) -> bool:
    return Path(spec).expanduser().is_file() or ":" in spec


def _dequantize_loose(model, dtype) -> None:
    """GGUF weights stay packed and are unpacked by the linear layers that use them; other
    layers (the norms, stored as bf16 in the file) would read the packed bytes, so they
    are unpacked once here (a few small vectors)."""
    import torch
    from diffusers.quantizers.gguf.utils import GGUFParameter, dequantize_gguf_tensor

    for module in model.modules():
        if "GGUF" in type(module).__name__:
            continue
        for name, param in list(module.named_parameters(recurse=False)):
            if isinstance(param, GGUFParameter):
                value = dequantize_gguf_tensor(param).to(dtype)
                setattr(module, name, torch.nn.Parameter(value.as_subclass(torch.Tensor), requires_grad=False))


def _load_instruct(spec: str):
    """Qwen-Image 2.1. A single-file transformer (a GGUF quantisation, or safetensors) is
    combined with the official repo's text encoder, VAE and scheduler; either way the
    parts are offloaded to system memory between uses."""
    import torch
    from diffusers import QwenImage21Pipeline

    dtype = torch.bfloat16
    if not _is_file_spec(spec):
        pipe = QwenImage21Pipeline.from_pretrained(spec, torch_dtype=dtype)
        pipe.enable_model_cpu_offload()
        pipe.set_progress_bar_config(disable=True)
        return ("instruct", pipe)

    from diffusers import QwenImage21Transformer2DModel

    path = _file(spec)
    kwargs = {}
    if path.suffix == ".gguf":
        from diffusers import GGUFQuantizationConfig

        kwargs["quantization_config"] = GGUFQuantizationConfig(compute_dtype=dtype)
    transformer = QwenImage21Transformer2DModel.from_single_file(
        str(path), config=QWEN_BASE, subfolder="transformer", torch_dtype=dtype, **kwargs,
    )
    if path.suffix == ".gguf":
        _dequantize_loose(transformer, dtype)
    pipe = QwenImage21Pipeline.from_pretrained(QWEN_BASE, transformer=transformer, torch_dtype=dtype)
    # Each part moves to the GPU while it runs: at the native size (about 34,000 tokens with
    # the photo) the transformer needs most of a 24 GB card, the text encoder runs once.
    pipe.enable_model_cpu_offload()
    pipe.vae.enable_tiling()  # decoding a whole image at once needs several GB more than the rest
    pipe.set_progress_bar_config(disable=True)
    return ("instruct", pipe)


def _device() -> str:
    return "cuda" if _cuda() else "cpu"


def _load(tool: str, spec: str):
    t = TOOLS[tool]
    if t.kind == "opencv":
        return None
    if t.kind in ("fill", "upscale"):
        import torch

        import warnings

        path = _file(spec)
        # LaMa ships as TorchScript: spandrel's attempt and the fallback both go through
        # torch.jit.load, whose deprecation notice is noise here.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            try:
                import spandrel

                model = spandrel.ModelLoader().load_from_file(path).to(_device()).eval()
                return ("spandrel", model)
            except Exception:
                if t.kind != "fill":
                    raise
                return ("torchscript", torch.jit.load(str(path), map_location=_device()).eval())
    if t.kind == "instruct":
        return _load_instruct(spec)
    if t.kind == "segment":
        from . import segment

        return segment.load(spec, _device())
    # diffusion
    import torch
    from diffusers import AutoPipelineForInpainting

    p = Path(spec).expanduser()
    kwargs = {"torch_dtype": torch.float16}
    _quiet_empty_fp32_notice()
    if p.is_file():
        from diffusers import StableDiffusionXLInpaintPipeline

        pipe = StableDiffusionXLInpaintPipeline.from_single_file(str(p), **kwargs)
    else:
        try:
            pipe = AutoPipelineForInpainting.from_pretrained(spec, variant="fp16", **kwargs)
        except Exception:  # no fp16 variant
            pipe = AutoPipelineForInpainting.from_pretrained(spec, **kwargs)
    if "flux" in spec.lower():
        pipe.enable_model_cpu_offload()  # too large for 24 GB otherwise
    else:
        pipe = pipe.to("cuda")
    pipe.set_progress_bar_config(disable=True)
    return ("diffusers", pipe)


class _EmptyFp32Notice:
    """Loading a single checkpoint file, diffusers casts each part with ``.to()`` and warns
    that "modules … should be kept in float32: []": with none listed, nothing is affected."""

    def filter(self, record) -> bool:
        return "should be kept in float32: []" not in record.getMessage()


def _quiet_empty_fp32_notice() -> None:
    import logging

    log_ = logging.getLogger("diffusers.models.modeling_utils")
    if not any(isinstance(f, _EmptyFp32Notice) for f in log_.filters):
        log_.addFilter(_EmptyFp32Notice())


def model_for(tool: str, choice: str | None):
    """The loaded model (loading it on first use); None for the builtin tool."""
    _, spec = resolve(tool, choice)
    key = (tool, spec)
    with _lock:
        hit = _loaded.get(key)
        if hit is None:
            # One model per tool at a time (a switch frees the previous one), and one
            # large generative model at a time: two would not fit on the GPU together.
            large = ("diffusion", "instruct")
            for other in [k for k in _loaded if k[0] == tool
                          or (TOOLS[tool].kind in large and TOOLS[k[0]].kind in large)]:
                _loaded.pop(other)
            _free_gpu()
            hit = _loaded[key] = [_load(tool, spec), time.time()]
        hit[1] = time.time()
        return hit[0]


def release_idle(seconds: float = IDLE_SECONDS) -> int:
    """Free models unused for ``seconds``; returns how many."""
    with _lock:
        old = [k for k, (_, used) in _loaded.items() if time.time() - used > seconds]
        for k in old:
            _loaded.pop(k)
    if old:
        _free_gpu()
    return len(old)


def _free_gpu() -> None:
    try:
        import gc

        import torch

        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


# ---- retouching ----------------------------------------------------------------------


@dataclass
class Result:
    patch: Image.Image  # the changed pixels over ``bbox``
    mask: Image.Image  # feathered, the same size: how the patch is pasted
    bbox: tuple[int, int, int, int]  # x, y, w, h in the image


def _box(mask: np.ndarray, pad: int) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    h, w = mask.shape
    x0, y0 = max(0, xs.min() - pad), max(0, ys.min() - pad)
    x1, y1 = min(w, xs.max() + 1 + pad), min(h, ys.max() + 1 + pad)
    return int(x0), int(y0), int(x1 - x0), int(y1 - y0)


def _context(box, size, least: int = MIN_CONTEXT, factor: float = CONTEXT) -> tuple[int, int, int, int]:
    """The box grown by ``factor`` (each side at least ``least``), within the image."""
    x, y, w, h = box
    W, H = size
    cw = min(W, max(int(w * factor), least))
    ch = min(H, max(int(h * factor), least))
    cx = min(max(0, x + w // 2 - cw // 2), W - cw)
    cy = min(max(0, y + h // 2 - ch // 2), H - ch)
    return cx, cy, cw, ch


def _grow(ctx, size, area: int) -> tuple[int, int, int, int]:
    """The context ``ctx`` grown around its centre, within the image, until it covers
    ``area`` pixels (or the whole image)."""
    cx, cy, cw, ch = ctx
    W, H = size
    if cw * ch >= area:
        return ctx
    k = (area / (cw * ch)) ** 0.5
    nw, nh = min(W, round(cw * k)), min(H, round(ch * k))
    # A side stopped by the image's edge leaves the rest of the area to the other.
    if nw == W:
        nh = min(H, -(-area // nw))
    elif nh == H:
        nw = min(W, -(-area // nh))
    cx = min(max(0, cx + cw // 2 - nw // 2), W - nw)
    cy = min(max(0, cy + ch // 2 - nh // 2), H - nh)
    return cx, cy, nw, nh


def work_area(kind: str, loaded) -> int:
    if kind == "fill":
        return LAMA_AREA
    pipe = loaded[1] if isinstance(loaded, tuple) else loaded
    name = type(pipe).__name__
    return DIFFUSION_AREA if ("XL" in name or "Flux" in name) else SD15_AREA


def _fit(im: Image.Image, area: int, multiple: int = 8) -> Image.Image:
    """``im`` scaled down to at most ``area`` pixels, both sides a multiple of ``multiple``."""
    s = min(1.0, (area / (im.width * im.height)) ** 0.5)
    w = max(multiple, int(im.width * s) // multiple * multiple)
    h = max(multiple, int(im.height * s) // multiple * multiple)
    return im.resize((w, h), Image.Resampling.LANCZOS)


def retouch(
    tool: str, image: Image.Image, mask: Image.Image, choice: str | None = None,
    prompt: str = "", negative: str = "", seed: int = 0, candidates: int = 1,
    steps: int = 30, strength: float = 0.99, blur: float | None = None, fill: str = "original",
    fill_choice: str | None = None,
) -> list[Result]:
    """Run a retouching tool on the masked part of ``image`` (the full-size, retouched
    original). Returns one result, or ``candidates`` for a prompt."""
    t = TOOLS[tool]
    image = image.convert("RGB")
    m = np.asarray(mask.convert("L").resize(image.size, Image.Resampling.BILINEAR)) > 127
    feather = max(3, round(max(image.size) * 0.004))
    # The mask's blur where the result meets the photo: in the editor's 1600 px, scaled.
    radius = (DEFAULT_BLUR if blur is None else max(0.0, float(blur))) * max(image.size) / VIEW_EDGE
    box = _box(m, max(2 * feather, int(np.ceil(3 * radius))))
    if box is None:
        if not t.mask_optional:
            raise ValueError("paint over what to change first")
        # The whole photo: the patch is all of it.
        out = _instruct(model_for(tool, choice), image, prompt, seed, candidates, steps)
        full = Image.new("L", image.size, 255)
        return [Result(o, full, (0, 0, image.width, image.height)) for o in out]
    loaded = model_for(tool, choice) if t.kind in ("fill", "diffusion") else None
    if t.kind != "instruct":
        cx, cy, cw, ch = _context(box, image.size)
        if loaded is not None:
            cx, cy, cw, ch = _grow((cx, cy, cw, ch), image.size, work_area(t.kind, loaded))
    elif box[2] < INSTRUCT_SMALL * image.width and box[3] < INSTRUCT_SMALL * image.height:
        cx, cy, cw, ch = _context(box, image.size, INSTRUCT_MIN_CONTEXT, INSTRUCT_CONTEXT)
    else:
        cx, cy, cw, ch = 0, 0, image.width, image.height
    crop = image.crop((cx, cy, cx + cw, cy + ch))
    mask_c = Image.fromarray((m[cy : cy + ch, cx : cx + cw] * 255).astype(np.uint8))
    hard = mask_c.filter(ImageFilter.MaxFilter(2 * (feather // 2) + 1))  # a little beyond the stroke

    if t.kind == "opencv":
        import cv2

        bgr = cv2.cvtColor(np.asarray(crop), cv2.COLOR_RGB2BGR)
        out = cv2.inpaint(bgr, np.asarray(hard), max(3, feather), cv2.INPAINT_TELEA)
        filled = [Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB))]
    elif t.kind == "fill":
        filled = [_fill(loaded, crop, hard)]
    elif t.kind == "diffusion":
        filled = _diffuse(loaded, crop, hard, prompt, negative, seed, candidates, steps, strength,
                          fill, fill_choice)
    elif t.kind == "instruct":
        filled = [Image.composite(o, crop, hard) for o in _instruct(model_for(tool, choice), crop, prompt, seed, candidates, steps)]
    else:
        raise ValueError(f"{tool} is not a retouching tool")

    # The patch: the mask's box (in the context's coordinates), pasted through a soft mask.
    bx, by, bw, bh = box[0] - cx, box[1] - cy, box[2], box[3]
    soft = hard.filter(ImageFilter.GaussianBlur(radius)) if radius > 0 else hard
    soft_box = soft.crop((bx, by, bx + bw, by + bh))
    return [Result(f.crop((bx, by, bx + bw, by + bh)), soft_box, box) for f in filled]


def retouch_regions(
    tool: str, image: Image.Image, mask: Image.Image, choice: str | None = None, *, prompt: str = "",
    negative: str = "", seed: int = 0, steps: int | None = None, strength: float = 0.99, blur: float | None = None,
    fill: str = "original", fill_choice: str | None = None, progress=None,
) -> Result | None:
    """``retouch`` for a mask of many separate things (people across a street): each area
    (parts close together count as one) is done on its own, with its own context, so a
    small one keeps its resolution; the results make one patch over them all."""
    import cv2

    image = image.convert("RGB")
    m = (np.asarray(mask.convert("L").resize(image.size, Image.Resampling.NEAREST)) > 127).astype(np.uint8)
    if not m.any():
        return None
    near = max(3, round(max(image.size) * 0.02))  # closer than this: one area
    joined = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * near + 1, 2 * near + 1)))
    n, labels = cv2.connectedComponents(joined)
    work = image.copy()
    soft = np.zeros((image.height, image.width), np.uint8)
    areas = range(1, n)
    for k in progress(areas, total=n - 1, desc="areas") if progress else areas:
        part = Image.fromarray(((labels == k) & (m > 0)).astype(np.uint8) * 255)
        res = retouch(tool, work, part, choice, prompt=prompt, negative=negative, seed=seed + k, candidates=1,
                      steps=steps or STEPS.get(tool, 30), strength=strength, blur=blur, fill=fill,
                      fill_choice=fill_choice)[0]
        x, y, w, h = res.bbox
        work.paste(res.patch, (x, y), res.mask)
        soft[y : y + h, x : x + w] = np.maximum(soft[y : y + h, x : x + w], np.asarray(res.mask))
    ys, xs = np.nonzero(soft)
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    return Result(work.crop((x0, y0, x1, y1)), Image.fromarray(soft[y0:y1, x0:x1]), (x0, y0, x1 - x0, y1 - y0))


def _fill(loaded, crop: Image.Image, mask: Image.Image) -> Image.Image:
    import torch

    kind, model = loaded
    small = _fit(crop, LAMA_AREA)
    small_mask = mask.resize(small.size, Image.Resampling.NEAREST)
    dev = _device()
    x = torch.from_numpy(np.array(small)).permute(2, 0, 1)[None].float().to(dev) / 255
    mk = torch.from_numpy((np.asarray(small_mask) > 127).astype(np.float32))[None, None].to(dev)
    with torch.no_grad():
        out = model(x, mk)
    arr = (out[0].permute(1, 2, 0).float().cpu().numpy() * 255).clip(0, 255).astype(np.uint8)
    result = Image.fromarray(arr).resize(crop.size, Image.Resampling.LANCZOS)
    # Outside the mask the crop stays exactly as it was (the scaling blurred it).
    return Image.composite(result, crop, mask)


def prefill(image: Image.Image, mask: Image.Image, fill: str, seed: int = 0, fill_choice: str | None = None) -> Image.Image:
    """``image`` with the mask's area filled as ``fill`` says (FILLS): what denoising
    below 1 starts from there."""
    if fill == "original" or fill not in FILLS:
        return image
    if fill == "remove":
        return _fill(model_for("remove", fill_choice), image, mask)
    import cv2

    m = np.asarray(mask.convert("L"))
    radius = max(3, round(max(image.size) * 0.01))
    healed = cv2.inpaint(np.asarray(image.convert("RGB")), (m > 127).astype(np.uint8) * 255, radius, cv2.INPAINT_TELEA)
    if fill == "noise":
        rng = np.random.default_rng(seed)
        h, w = healed.shape[:2]
        cells = rng.normal(0, NOISE, (max(1, h // NOISE_CELL), max(1, w // NOISE_CELL), 3)).astype(np.float32)
        noise = cv2.resize(cells, (w, h), interpolation=cv2.INTER_LINEAR)
        healed = (healed.astype(np.float32) + noise).clip(0, 255).astype(np.uint8)
    return Image.composite(Image.fromarray(healed), image, mask)


def _diffuse(loaded, crop, mask, prompt, negative, seed, candidates, steps, strength, fill="original", fill_choice=None) -> list[Image.Image]:
    import torch

    _, pipe = loaded
    small = _fit(crop, work_area("diffusion", loaded))
    small_mask = mask.resize(small.size, Image.Resampling.NEAREST)
    if strength < 1 and fill in ("heal", "remove"):  # (the same start for every candidate)
        small = prefill(small, small_mask, fill, seed, fill_choice)
    out = []
    for k in range(max(1, min(4, candidates))):
        g = torch.Generator("cuda" if _cuda() else "cpu").manual_seed(int(seed) + k)
        start = prefill(small, small_mask, "noise", int(seed) + k) if strength < 1 and fill == "noise" else small
        kwargs = dict(prompt=prompt or "", image=start, mask_image=small_mask, generator=g,
                      num_inference_steps=int(steps), width=small.width, height=small.height)
        if negative and "flux" not in type(pipe).__name__.lower():
            kwargs["negative_prompt"] = negative
        if "flux" not in type(pipe).__name__.lower():
            kwargs["strength"] = float(strength)
        img = pipe(**kwargs).images[0].resize(crop.size, Image.Resampling.LANCZOS)
        out.append(Image.composite(img.convert("RGB"), crop, mask))
    return out


def _instruct(loaded, image: Image.Image, prompt: str, seed: int, candidates: int, steps: int) -> list[Image.Image]:
    """Edit ``image`` as ``prompt`` says: 1-4 candidates at ``image``'s size."""
    import torch

    if not prompt.strip():
        raise ValueError("say what to change")
    _, pipe = loaded
    w, h = native_size(image.size)
    src = image.resize((w, h), Image.Resampling.LANCZOS)
    out = []
    for k in range(max(1, min(4, candidates))):
        g = torch.Generator("cuda" if _cuda() else "cpu").manual_seed(int(seed) + k)
        with torch.inference_mode():
            edited = pipe(prompt=prompt, image=src, width=w, height=h, num_inference_steps=int(steps), generator=g,
                          output_resolution=max(w, h) if w == h else int((w * h) ** 0.5)).images[0]
        out.append(keep_detail(image, edited.convert("RGB")))
    return out


def native_size(size: tuple[int, int]) -> tuple[int, int]:
    """The working size (QWEN_SIZES) closest to ``size``'s aspect ratio."""
    ratio = np.log(size[0] / size[1])
    return min(QWEN_SIZES, key=lambda s: abs(np.log(s[0] / s[1]) - ratio))


def keep_detail(original: Image.Image, edited: Image.Image, tolerance: float = DETAIL_TOLERANCE) -> Image.Image:
    """``edited`` (made at a lower resolution) brought to ``original``'s size, with the
    original's fine detail (what the lower resolution lost) put back where the edit left
    the picture as it was, fading out where it changed it."""
    size = original.size
    orig = np.asarray(original, np.float32)
    small = original.resize(edited.size, Image.Resampling.LANCZOS)
    low = np.asarray(small.resize(size, Image.Resampling.LANCZOS), np.float32)
    up = np.asarray(edited.resize(size, Image.Resampling.LANCZOS), np.float32)
    # How much the edit changed each place, at the edit's resolution (a little smoothed).
    diff = np.abs(np.asarray(edited, np.float32) - np.asarray(small, np.float32)).mean(axis=2)
    diff = Image.fromarray(diff.clip(0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.5))
    change = np.asarray(diff.resize(size, Image.Resampling.BILINEAR), np.float32)
    keep = np.clip(1.0 - change / (2 * tolerance), 0.0, 1.0)[..., None]
    out = up + keep * (orig - low)
    return Image.fromarray(out.clip(0, 255).round().astype(np.uint8))


# ---- upscaling -------------------------------------------------------------------------


def upscale(image: Image.Image, factor: int = 2, choice: str | None = None) -> Image.Image:
    """Enlarge ``image`` by 2 or 4 with a super-resolution model, in overlapping tiles
    (a whole photo at ×4 would not fit in memory); ×2 is the model's ×4, halved."""
    import torch

    _, model = model_for("upscale", choice)
    scale = getattr(model, "scale", 4)
    dev = _device()
    src = np.asarray(image.convert("RGB"), dtype=np.float32) / 255
    H, W = src.shape[:2]
    out = np.zeros((H * scale, W * scale, 3), np.float32)
    for y in range(0, H, TILE):
        for x in range(0, W, TILE):
            x0, y0 = max(0, x - TILE_PAD), max(0, y - TILE_PAD)
            x1, y1 = min(W, x + TILE + TILE_PAD), min(H, y + TILE + TILE_PAD)
            t = torch.from_numpy(src[y0:y1, x0:x1]).permute(2, 0, 1)[None].to(dev)
            with torch.no_grad():
                r = model(t)[0].permute(1, 2, 0).float().cpu().numpy()
            # Keep the tile's own part (its padding overlaps the neighbours).
            ox, oy = (x - x0) * scale, (y - y0) * scale
            tw, th = (min(W, x + TILE) - x) * scale, (min(H, y + TILE) - y) * scale
            out[y * scale : y * scale + th, x * scale : x * scale + tw] = r[oy : oy + th, ox : ox + tw]
    big = Image.fromarray((out.clip(0, 1) * 255).round().astype(np.uint8))
    if factor != scale:
        big = big.resize((W * factor, H * factor), Image.Resampling.LANCZOS)
    return big
