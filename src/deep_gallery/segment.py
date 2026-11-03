"""Finding things by text and masking them (for removing or replacing them in many photos).

Two models: an open-vocabulary detector finds boxes for the words (Grounding DINO), and a
segmenter cuts each box's object out as a mask (SAM 2.1). A model setting names both,
"detector+segmenter", as Hugging Face repos or local folders; editing.py holds the
catalogue and keeps the pair loaded while it is used.

``find`` works on an image of any size (the models scale it themselves; the editor passes
a preview) and returns one mask per object found, with its box, score and label.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter

BOX_THRESHOLD = 0.3  # the detector's confidence for a box…
TEXT_THRESHOLD = 0.25  # …and for the words it matched
MAX_OBJECTS = 40


@dataclass
class Found:
    mask: np.ndarray  # bool, the image's size
    box: tuple[float, float, float, float]  # x0, y0, x1, y1
    score: float
    label: str


def load(spec: str, device: str):
    """(detector processor, detector, segmenter processor, segmenter) for "det+seg"."""
    import torch
    from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor, Sam2Model, Sam2Processor

    det, _, seg = spec.partition("+")
    if not seg:
        raise ValueError("a find model is 'detector+segmenter', e.g. IDEA-Research/grounding-dino-base+facebook/sam2.1-hiera-large")
    dtype = torch.float16 if device == "cuda" else torch.float32
    return (
        AutoProcessor.from_pretrained(det),
        AutoModelForZeroShotObjectDetection.from_pretrained(det).to(device).eval(),
        Sam2Processor.from_pretrained(seg),
        Sam2Model.from_pretrained(seg, dtype=dtype).to(device).eval(),
    )


def _query(text: str) -> str:
    """Grounding DINO's form: lower case, each thing ending with a full stop."""
    parts = [p.strip().lower() for p in text.replace(";", ",").replace(".", ",").split(",") if p.strip()]
    return " ".join(f"{p}." for p in parts)


def find(loaded, image: Image.Image, text: str, threshold: float = BOX_THRESHOLD, device: str = "cpu") -> list[Found]:
    """Every object ``text`` names in ``image`` ("people, cars"), best first."""
    import torch

    det_proc, det, seg_proc, seg = loaded
    image = image.convert("RGB")
    query = _query(text)
    if not query:
        raise ValueError("say what to find")
    with torch.inference_mode():
        inputs = det_proc(images=image, text=query, return_tensors="pt").to(device)
        out = det(**inputs)
        res = det_proc.post_process_grounded_object_detection(
            out, inputs.input_ids, threshold=threshold, text_threshold=TEXT_THRESHOLD, target_sizes=[image.size[::-1]]
        )[0]
    boxes = res["boxes"].float().cpu().numpy()
    scores = res["scores"].float().cpu().numpy()
    labels = res.get("text_labels") or res.get("labels") or [""] * len(boxes)
    if not len(boxes):
        return []
    order = np.argsort(-scores)[:MAX_OBJECTS]
    boxes, scores = boxes[order], scores[order]
    labels = [str(labels[i]) for i in order]

    with torch.inference_mode():
        s_in = seg_proc(images=image, input_boxes=[[b.tolist() for b in boxes]], return_tensors="pt").to(device)
        s_in["pixel_values"] = s_in["pixel_values"].to(seg.dtype)
        s_out = seg(**s_in, multimask_output=False)
        masks = seg_proc.post_process_masks(s_out.pred_masks.float().cpu(), s_in["original_sizes"].cpu())[0]
    masks = masks.numpy().reshape(len(boxes), image.height, image.width) > 0
    return [Found(m, tuple(float(v) for v in b), float(s), l) for m, b, s, l in zip(masks, boxes, scores, labels)]


def combined(found: list[Found], size: tuple[int, int], grow: float = 0.01) -> Image.Image:
    """One mask of everything found, grown by ``grow`` × the long side (edges, halos and
    shadows at the border are then part of what gets filled)."""
    import cv2

    m = np.zeros((size[1], size[0]), np.uint8)
    for f in found:
        # Each object whole: holes inside it (a print on a bag) are part of it.
        contours, _ = cv2.findContours(f.mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(m, contours, -1, 255, thickness=cv2.FILLED)
    im = Image.fromarray(m)
    px = round(max(size) * grow)
    if px >= 1:
        im = im.filter(ImageFilter.MaxFilter(2 * px + 1))
    return im
