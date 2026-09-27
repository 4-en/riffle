"""Generated captions and fixed tags (stored in the selections DB, see selections.py).

Methods, photos first:

- ``riffle``: the photo's vocabulary tags and learned tags as keywords (no model).
- ``phrases``: the phrases of the cluster-name vocabulary that stand out for the
  photo: similarity minus the library's mean similarity (CLIP text vectors).
- ``joycaption``: a caption and/or keywords from JoyCaption Beta One (an 8B vision
  language model; needs the ``captions`` extra and an NVIDIA GPU).
- ``ocr``: the text in the photo, in its own script, with an English translation,
  from Qwen3-VL-8B (same requirements). Stored apart from the caption.
- ``qwen``: a caption, keywords, and the text in one pass of Qwen3-VL-8B: about 20%
  faster than three prompts (the image is read once), but slower than ``ocr`` alone
  when only the text is wanted (it always writes a caption).

For illustrations, low-key: ``wd`` (the WD EVA02 tagger: booru tags, ONNX, also on
the CPU) and JoyCaption's Danbooru preset, optionally kept to a master tag list
(``TagList``). Experiment §14.3 of the development plan chose these.

The model methods run as a background job (``run_captioning``): one model per run,
loaded in the job's thread and released afterwards. Results are stored in batches,
so a cancelled or failed run keeps what it finished.
"""

from __future__ import annotations

import csv
import gc
import importlib.util
import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

WD_REPO = "SmilingWolf/wd-eva02-large-tagger-v3"
JOY_REPO = "fancyfeast/llama-joycaption-beta-one-hf-llava"
QWEN_REPO = "Qwen/Qwen3-VL-8B-Instruct"
DANBOORU_URL = "https://raw.githubusercontent.com/DominikDoom/a1111-sd-webui-tagcomplete/main/tags/danbooru.csv"
BATCH = 8  # photos per stored batch

JOY_SYSTEM = "You are a helpful image captioner."
CAPTION_PROMPTS = {
    "short": "Write a short caption for this photo in one sentence: the main subject and the setting.",
    "medium": "Write a descriptive caption for this photo in a formal tone within 50 words: the subject, the setting, and the light.",
    "detailed": "Write a long descriptive caption for this photo in a formal tone: the subject, the setting, the light, the colours, and the composition.",
}
KEYWORD_PROMPT = (
    "Write a list of 10 to 20 keywords for this photo: the subject, the setting, the light, and the mood. "
    "Lowercase, comma-separated. Output only the keywords."
)
BOORU_PROMPT = (
    "Generate only comma-separated Danbooru tags (lowercase_underscores). Strict order: `artist:`, `copyright:`, "
    "`character:`, `meta:`, then general tags. Include counts (1girl), appearance, clothing, accessories, pose, "
    "expression, actions, background. Use precise Danbooru syntax. No extra text."
)
KEYWORD_MAX = 15  # JoyCaption ignores "10 to 20" and runs on to 30-40, the last ones filler
NOT_KEYWORDS = {"photograph", "photo", "image", "picture", "photography"}
PREFIXES = re.compile(r"^(artist|copyright|meta|character|general|rating):\s*", re.I)
BOILERPLATE = re.compile(r"^(artist|copyright|meta):", re.I)


@dataclass
class Method:
    key: str
    label: str
    group: str  # photo | illustration
    outputs: tuple[str, ...]  # caption, tags, text (read from the photo)
    needs: str  # shown when unavailable
    download: str = ""  # model size to download on first use
    repo: str | None = None
    model: bool = False  # runs a model (as a background job)

    def status(self) -> dict:
        ok, reason = True, ""
        if self.key == "wd" and not _has("onnxruntime"):
            ok, reason = False, 'needs the captions extra: pip install -e ".[captions]"'
        elif self.key in ("joycaption", "ocr", "qwen"):
            if not _has("transformers"):
                ok, reason = False, 'needs the captions extra: pip install -e ".[captions]"'
            elif not _cuda():
                ok, reason = False, "needs an NVIDIA GPU (CUDA)"
        return {
            "key": self.key,
            "label": self.label,
            "group": self.group,
            "outputs": list(self.outputs),
            "available": ok,
            "reason": reason,
            "download": self.download if self.repo and not _cached(self.repo) else "",
        }


METHODS = {
    m.key: m
    for m in (
        Method("riffle", "Riffle's tags", "photo", ("tags",), ""),
        Method("phrases", "Scene phrases", "photo", ("tags",), "needs the AI model"),
        Method("joycaption", "JoyCaption", "photo", ("caption", "tags"), "", "16 GB", JOY_REPO, True),
        Method("ocr", "Read text", "photo", ("text",), "", "17 GB", QWEN_REPO, True),
        Method("qwen", "Caption, keywords and text", "photo", ("caption", "tags", "text"), "", "17 GB", QWEN_REPO, True),
        Method("wd", "WD tagger", "illustration", ("tags",), "", "1.2 GB", WD_REPO, True),
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


def _cached(repo: str) -> bool:
    from huggingface_hub import try_to_load_from_cache

    name = "model.onnx" if repo == WD_REPO else "config.json"
    return isinstance(try_to_load_from_cache(repo, name), str)


# ---- cheap methods (no model) -----------------------------------------------------


def riffle_tags(vocabulary: dict[int, list[str]], learned: dict[int, list[str]], ids: list[int]) -> dict[int, dict]:
    """Keywords from what Riffle already knows: vocabulary tags, then learned tags."""
    return {i: {"tags": [*vocabulary.get(i, []), *learned.get(i, [])]} for i in ids}


PHRASE_MARGIN = 0.04  # above the library's mean similarity to the phrase
PHRASE_WITHIN = 0.02  # raw similarity within this of the photo's best phrase
PHRASE_MAX = 4


def _bare(phrase: str) -> str:
    return re.sub(r"^(a|an|the) ", "", phrase)


def phrase_tags(E: np.ndarray, phrases: list[str], vecs: np.ndarray, baseline: np.ndarray) -> list[list[str]]:
    """Per embedding row: the vocabulary phrases that stand out for it (distinctive
    against the library, and about as close as its best phrase), best first,
    without articles. Near-alternatives of one thing ("crows", "ravens") compete,
    so only phrases close to the best one are kept."""
    raw = E @ vecs.T
    score = raw - baseline
    out = []
    for r, s in zip(raw, score):
        order = np.argsort(-s)
        best = r.max()
        out.append([_bare(phrases[j]) for j in order[: PHRASE_MAX * 3] if s[j] >= PHRASE_MARGIN and r[j] >= best - PHRASE_WITHIN][:PHRASE_MAX])
    return out


# ---- master tag list ----------------------------------------------------------------


def _key(tag: str) -> str:
    return re.sub(r"[\s_]+", "_", tag.strip().casefold())


@dataclass
class TagList:
    """Known tags with aliases. ``filter`` keeps generated tags that are on the list
    (exactly, by an alias, or by spelling: plural, hyphen/underscore/space) in the
    list's spelling and drops the rest; never by similarity (it mapped "serene" to
    ">:)" in the experiment)."""

    name: str
    canonical: dict[str, str] = field(default_factory=dict)  # key -> tag as listed

    @classmethod
    def load(cls, path: Path) -> TagList:
        tl = cls(path.stem)
        aliases: list[tuple[str, str]] = []
        with open(path, newline="", encoding="utf-8") as fh:
            if path.suffix.lower() != ".csv":
                for line in fh:
                    if line.strip() and not line.startswith("#"):
                        tl.canonical.setdefault(_key(line), line.strip())
                return tl
            rows = list(csv.reader(fh))
        if rows and rows[0][:2] == ["tag_id", "name"]:  # WD selected_tags.csv
            for r in rows[1:]:
                if len(r) > 2 and r[2] in ("0", "4"):  # general and characters
                    tl.canonical.setdefault(_key(r[1]), r[1])
            return tl
        for r in rows:  # tagcomplete: tag,category,count,"alias1,alias2"
            if not r or not r[0].strip():
                continue
            tl.canonical.setdefault(_key(r[0]), r[0].strip())
            if len(r) > 3 and r[3]:
                aliases += [(a, r[0].strip()) for a in r[3].split(",") if a.strip()]
        for alias, tag in aliases:
            tl.canonical.setdefault(_key(alias), tag)
        return tl

    def find(self, tag: str) -> str | None:
        k = _key(tag)
        for c in (k, k.replace("-", "_"), k.replace("_", "-"), re.sub(r"ies$", "y", k), re.sub(r"es$", "", k), re.sub(r"s$", "", k)):
            if c in self.canonical:
                return self.canonical[c]
        return None

    def filter(self, tags: list[str]) -> list[str]:
        return [t for t in (self.find(x) for x in tags) if t]


def taglists(root: Path, hub_wd: bool = True) -> dict[str, Path]:
    """Installed lists: files in ``<config>/taglists`` and the WD tagger's list."""
    out = {}
    d = root / "taglists"
    if d.is_dir():
        for p in sorted(d.iterdir()):
            if p.suffix.lower() in (".csv", ".txt"):
                out[p.stem] = p
    if hub_wd:
        from huggingface_hub import try_to_load_from_cache

        wd = try_to_load_from_cache(WD_REPO, "selected_tags.csv")
        if isinstance(wd, str):
            out.setdefault("wd-tagger", Path(wd))
    return out


def character_tags(root: Path) -> frozenset[str]:
    """Tags known to name characters (category 4 in the WD tagger's list and in a
    downloaded Danbooru list), lowercased with spaces, for naming clusters."""
    out = set()
    for path in taglists(root).values():
        if path.suffix.lower() != ".csv":
            continue
        try:
            with open(path, newline="", encoding="utf-8") as fh:
                rows = csv.reader(fh)
                first = next(rows, [])
                wd = first[:2] == ["tag_id", "name"]  # WD: tag_id,name,category,count
                for r in rows if wd else [first, *rows]:
                    name, category = (r[1], r[2]) if wd else (r[0], r[1] if len(r) > 1 else "")
                    if category == "4":
                        out.add(" ".join(name.replace("_", " ").casefold().split()))
        except (OSError, csv.Error, IndexError):
            continue
    return frozenset(out)


def download_danbooru(root: Path) -> Path:
    """The Danbooru tag list with aliases (from the a1111 tagcomplete extension)."""
    import urllib.request

    d = root / "taglists"
    d.mkdir(parents=True, exist_ok=True)
    target = d / "danbooru.csv"
    tmp = target.with_suffix(".partial")
    with urllib.request.urlopen(DANBOORU_URL, timeout=60) as res, open(tmp, "wb") as out:
        out.write(res.read())
    TagList.load(tmp)  # refuse something that isn't a tag list
    tmp.replace(target)
    return target


# ---- output clean-up ------------------------------------------------------------------

_tokenizer = None


def token_count(text: str) -> int:
    """CLIP tokens in ``text`` (without start/end), as Stable Diffusion training
    counts a caption: its text encoder takes 75."""
    global _tokenizer
    if _tokenizer is None:
        from open_clip.tokenizer import SimpleTokenizer

        _tokenizer = SimpleTokenizer()
    return len(_tokenizer.encode(text))


# Emoticon tags keep their underscore (it is part of the face); the list the common
# taggers and training scripts use.
KAOMOJI = {"0_0", "(o)_(o)", "+_+", "+_-", "._.", "<o>_<o>", "<|>_<|>", "=_=", ">_<", "3_3", "6_9", ">_o", "@_@", "^_^", "o_o", "u_u", "x_x", "|_|", "||_||"}


def booru_style(tags: list[str], underscores: bool = False) -> list[str]:
    """Booru tags as stored: with spaces ("blue sky"), as training tools mostly
    expect today, or with underscores (the Danbooru spelling). Emoticons stay."""
    if underscores:
        return [t if t in KAOMOJI else t.replace(" ", "_") for t in tags]
    return [t if t in KAOMOJI else t.replace("_", " ") for t in tags]



def split_tags(text: str, *, booru: bool, keep_prefixed: bool = False) -> list[str]:
    """A model's comma-separated answer as tags. Keywords are lowercased with
    spaces, without "photograph" and the like, at most KEYWORD_MAX (the model lists
    the specific ones first); booru tags keep underscores and drop
    artist:/copyright:/meta: boilerplate."""
    out: list[str] = []
    for t in re.split(r"[,\n]", text):
        t = t.strip().strip(".").strip()
        if not t:
            continue
        if booru:
            if BOILERPLATE.match(t) and not keep_prefixed:
                continue
            t = PREFIXES.sub("", t).replace(" ", "_")
        else:
            t = PREFIXES.sub("", t).replace("_", " ").casefold()
            if t in NOT_KEYWORDS or t in out:
                continue
            if len(out) == KEYWORD_MAX:
                break
        out.append(t)
    return out


# ---- models -------------------------------------------------------------------------


def load_wd(options: dict):
    """The WD tagger as ``run(images, ids) -> [{tags}]``."""
    import onnxruntime as ort
    from huggingface_hub import hf_hub_download
    from PIL import Image

    model_path = hf_hub_download(WD_REPO, "model.onnx")
    tags_path = hf_hub_download(WD_REPO, "selected_tags.csv")
    providers = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in ort.get_available_providers()]
    sess = ort.InferenceSession(model_path, providers=providers)
    with open(tags_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    names = [r["name"] for r in rows]
    category = np.array([int(r["category"]) for r in rows])
    inp = sess.get_inputs()[0]
    size = inp.shape[1]
    general = float(options.get("threshold", 0.35))
    characters = float(options.get("character_threshold", 0.85)) if options.get("characters") else None
    rating = bool(options.get("rating"))

    def run(images, ids):
        batch = []
        for img in images:
            side = max(img.size)
            canvas = Image.new("RGB", (side, side), (255, 255, 255))
            canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
            batch.append(np.asarray(canvas.resize((size, size), Image.BICUBIC), dtype=np.float32)[:, :, ::-1])  # BGR
        probs = sess.run(None, {inp.name: np.ascontiguousarray(np.stack(batch))})[0]
        out = []
        for p in probs:
            picked = [(names[j], p[j]) for j in np.flatnonzero((category == 0) & (p >= general))]
            if characters is not None:
                picked += [(names[j], p[j]) for j in np.flatnonzero((category == 4) & (p >= characters))]
            tags = [t for t, _ in sorted(picked, key=lambda x: -x[1])]
            if rating:
                r = np.flatnonzero(category == 9)
                tags.append(f"rating:{names[r[p[r].argmax()]]}")
            out.append({"tags": tags})
        return out

    return run, lambda: None


def load_joycaption(options: dict):
    """JoyCaption as ``run(images, ids) -> [{caption?, tags?}]``. Options: caption
    ("short" | "medium" | "detailed" | None), tags ("keywords" | "booru" | None),
    prompt (replaces the caption prompt), keep_prefixed."""
    import torch
    from huggingface_hub import snapshot_download
    from transformers import AutoProcessor, LlavaForConditionalGeneration

    path = snapshot_download(JOY_REPO)
    proc = AutoProcessor.from_pretrained(path)
    model = LlavaForConditionalGeneration.from_pretrained(path, dtype=torch.bfloat16, device_map="cuda").eval()
    caption = options.get("caption")
    tags = options.get("tags")
    prompt = (options.get("prompt") or "").strip() or CAPTION_PROMPTS.get(caption or "medium")

    def ask(img, text):
        convo = [{"role": "system", "content": JOY_SYSTEM}, {"role": "user", "content": text}]
        chat = proc.apply_chat_template(convo, tokenize=False, add_generation_prompt=True)
        x = proc(text=[chat], images=[img], return_tensors="pt").to("cuda")
        x["pixel_values"] = x["pixel_values"].to(torch.bfloat16)
        with torch.inference_mode():
            ids = model.generate(**x, max_new_tokens=300, do_sample=False)
        return proc.batch_decode(ids[:, x["input_ids"].shape[1]:], skip_special_tokens=True)[0].strip()

    def run(images, ids):
        out = []
        for img in images:
            res = {}
            if caption:
                res["caption"] = ask(img, prompt)
            if tags:
                booru = tags == "booru"
                res["tags"] = split_tags(ask(img, BOORU_PROMPT if booru else KEYWORD_PROMPT), booru=booru, keep_prefixed=bool(options.get("keep_prefixed")))
            out.append(res)
        return out

    def free():
        nonlocal model
        del model
        gc.collect()
        torch.cuda.empty_cache()

    return run, free


OCR_PROMPT = (
    "Read all text visible in this image (signs, labels, screens, documents, handwriting) exactly as written, "
    "in its original language and script, keeping line breaks. Answer only with JSON: "
    '{"text": "...", "language": "...", "english": "..."}. "language" is the language of the text in English '
    '(e.g. "Chinese"). "english" is an English translation, or "" if the text is already English. '
    'If there is no readable text, answer {"text": ""}.'
)
OCR_PROMPT_NO_TRANSLATION = (
    "Read all text visible in this image (signs, labels, screens, documents, handwriting) exactly as written, "
    "in its original language and script, keeping line breaks. Answer only with JSON: "
    '{"text": "...", "language": "..."}. If there is no readable text, answer {"text": ""}.'
)
TRANSLATE_PROMPT = "Translate this text into English. Keep the line breaks. Answer with the translation only.\n\n"
NO_TEXT = re.compile(r"^\W*(no (readable |visible )?text\b.*|none|n/?a)\W*$", re.I)


def _json_object(raw: str) -> dict | None:
    """The JSON object in a model's answer (code fences and prose around it ignored).
    An answer cut off by the token limit (long documents) still yields the string
    fields it got to, the last one up to where it stopped."""
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(raw[start : end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    if start < 0:
        return None
    fields = {}
    for m in re.finditer(r'"(\w+)"\s*:\s*"((?:[^"\\]|\\.)*)("?)', raw[start:], re.S):
        try:
            fields[m.group(1)] = json.loads('"' + m.group(2).rstrip("\\") + '"')
        except json.JSONDecodeError:
            fields[m.group(1)] = m.group(2)
    return fields or None


def parse_ocr(answer: str) -> dict:
    """The model's answer as {text, translation, language}: the JSON object in it
    (code fences and prose around it ignored), else the whole answer as the text.
    "No text"-style answers give an empty text (read, none found)."""
    raw = answer.strip()
    data = _json_object(raw)
    if not isinstance(data, dict):
        text = re.sub(r"^```\w*\n?|\n?```$", "", raw).strip()
        data = {"text": "" if NO_TEXT.match(text) else text}
    text = str(data.get("text") or "").strip()
    if NO_TEXT.match(text):
        text = ""
    english = str(data.get("english") or "").strip() if text else ""
    language = str(data.get("language") or "").strip() or None
    if english.casefold() == text.casefold():
        english = ""  # "translated" English text
    return {"text": text, "translation": english, "language": language if text else None}


COMBINED_PROMPT = (
    "Describe this image and read any text in it. Answer only with JSON: "
    '{"caption": "...", "keywords": [...], "text": "...", "language": "...", "english": "..."}. '
    '"caption": a descriptive caption in a formal tone within 50 words: the subject, the setting, and the light. '
    '"keywords": 10 to 15 lowercase keywords: the subject, the setting, the light, and the mood. '
    '"text": all text visible in the image exactly as written, in its original language and script, keeping line breaks, or "" if none. '
    '"language": the language of the text in English. "english": an English translation of the text, or "" if it is already English or there is none.'
)


def parse_combined(answer: str) -> dict:
    """The combined answer as {caption, tags, text: {text, translation, language}}.
    Without usable JSON the whole answer is taken as the caption."""
    raw = answer.strip()
    data = _json_object(raw)
    if not isinstance(data, dict):
        return {"caption": re.sub(r"^```\w*\n?|\n?```$", "", raw).strip()}
    keywords = data.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    out = {
        "caption": str(data.get("caption") or "").strip(),
        "tags": split_tags(", ".join(str(k) for k in keywords), booru=False),
        "text": parse_ocr(json.dumps({k: data.get(k) for k in ("text", "language", "english")}, ensure_ascii=False)),
    }
    return out


def _qwen():
    """Qwen3-VL-8B on the GPU: ``(ask(prompt, image=None) -> answer, free)``."""
    import torch
    from huggingface_hub import snapshot_download
    from transformers import AutoModelForImageTextToText, AutoProcessor

    path = snapshot_download(QWEN_REPO)
    proc = AutoProcessor.from_pretrained(path)
    model = AutoModelForImageTextToText.from_pretrained(path, dtype=torch.bfloat16, device_map="cuda").eval()

    def ask(prompt: str, img=None) -> str:
        content = ([{"type": "image"}] if img is not None else []) + [{"type": "text", "text": prompt}]
        chat = proc.apply_chat_template([{"role": "user", "content": content}], add_generation_prompt=True, tokenize=False)
        x = proc(text=[chat], images=[img] if img is not None else None, return_tensors="pt").to("cuda")
        with torch.inference_mode():
            ids = model.generate(**x, max_new_tokens=2048, do_sample=False)  # old maps: 300+ words
        return proc.batch_decode(ids[:, x["input_ids"].shape[1]:], skip_special_tokens=True)[0].strip()

    def free():
        nonlocal model
        del model
        gc.collect()
        torch.cuda.empty_cache()

    return ask, free


# ---- quick check for text (CLIP), before reading ----------------------------------------
# Reading a photo without text still takes Qwen 1.3 s. The photos' CLIP embeddings
# (already computed) against prompts for text and for plain photos leave out the
# ones that most likely have none. On the first library (§14.5) text was common (21%
# of photos: boat names, shop signs; most illustrations: signatures), and small text
# is what CLIP misses: at TEXT_MIN it keeps 95% of photos with text and skips 22% of
# photos (but misses 8 of 28 signed illustrations). A text detector (PP-OCRv4) did
# barely better (27% at 95%), so it was not added.

TEXT_PROMPTS = [
    "a photo of a sign with text", "a photo with writing on it", "a photo of printed text", "a street sign",
    "a shop sign with lettering", "a poster with text", "a menu", "a label with words", "a document", "a screenshot",
    "a book page", "a caption or watermark with text", "a plaque with an inscription", "a board with writing", "a signpost",
]
PLAIN_PROMPTS = [
    "a photo", "a landscape photo", "a photo of nature", "a photo of people", "a photo of an animal",
    "a photo of a building", "a photo of food", "an illustration", "a close-up photo", "a photo of the sky",
]
TEXT_MIN = -0.097  # max(text prompts) - max(plain prompts); 95% recall on the first library's photos


def text_check_vectors(encode_text) -> tuple[np.ndarray, np.ndarray]:
    return encode_text(TEXT_PROMPTS), encode_text(PLAIN_PROMPTS)


def text_scores(E: np.ndarray, text_vecs: np.ndarray, plain_vecs: np.ndarray) -> np.ndarray:
    """How much more a photo looks like text than like a plain photo."""
    return (E @ text_vecs.T).max(1) - (E @ plain_vecs.T).max(1)


def text_likely(E: np.ndarray, text_vecs: np.ndarray, plain_vecs: np.ndarray) -> np.ndarray:
    return text_scores(E, text_vecs, plain_vecs) >= TEXT_MIN


def load_qwen_ocr(options: dict):
    """Qwen3-VL as ``run(images, ids) -> [{text: {text, translation, language}}]``.
    With ``retranslate``, it translates ``options["texts"][id]`` instead (the current,
    possibly edited, text; no image) -> [{translation_only}]."""
    ask, free = _qwen()
    translate = options.get("translate", True)
    texts = options.get("texts") or {}

    def run(images, ids):
        if options.get("retranslate"):
            return [{"translation_only": ask(TRANSLATE_PROMPT + texts[str(i)]) if texts.get(str(i)) else ""} for i in ids]
        return [{"text": parse_ocr(ask(OCR_PROMPT if translate else OCR_PROMPT_NO_TRANSLATION, img))} for img in images]

    return run, free


def load_qwen_all(options: dict):
    """Qwen3-VL as ``run(images, ids) -> [{caption, tags, text}]`` in one pass per photo."""
    ask, free = _qwen()
    return (lambda images, ids: [parse_combined(ask(COMBINED_PROMPT, img)) for img in images]), free


LOADERS: dict[str, Callable] = {"wd": load_wd, "joycaption": load_joycaption, "ocr": load_qwen_ocr, "qwen": load_qwen_all}


def run_captioning(
    cfg,
    progress=None,
    report=print,
    *,
    photos: list[tuple[int, Path]],
    method: str,
    options: dict,
    store: Callable[[dict[int, dict]], int],  # stores a batch of results; how many changed
    taglist: Path | None = None,
    cancel: threading.Event | None = None,
    load: Callable | None = None,
) -> dict:
    """A BackgroundJob: caption ``photos`` [(id, image path)] with a model method and
    ``store`` the results batch by batch. ``load`` replaces the model loader (tests)."""
    from PIL import Image

    report(f"loading {METHODS[method].label}")
    run, free = (load or LOADERS[method])(options)
    tl = TagList.load(taglist) if taglist else None
    bar = progress(total=len(photos), desc="captioning") if progress else None
    done = changed = failed = 0
    try:
        for i in range(0, len(photos), BATCH):
            if cancel is not None and cancel.is_set():
                report("cancelled")
                break
            batch = photos[i : i + BATCH]
            images, ids = [], []
            for pid, path in batch:
                if options.get("retranslate"):  # text only: no image needed
                    ids.append(pid)
                    continue
                try:
                    with Image.open(path) as im:
                        images.append(im.convert("RGB"))
                    ids.append(pid)
                except OSError:
                    failed += 1
            results = dict(zip(ids, run(images, ids))) if ids else {}
            booru = method == "wd" or options.get("tags") == "booru"
            for r in results.values():
                if r.get("tags"):
                    if tl is not None:
                        r["tags"] = tl.filter(r["tags"])
                    if booru:
                        r["tags"] = booru_style(r["tags"], bool(options.get("underscores")))
            changed += store(results)
            done += len(batch)
            if bar:
                bar.update(len(batch))
    finally:
        free()
    summary = {"photos": done, "changed": changed, "failed": failed, "cancelled": bool(cancel and cancel.is_set())}
    report(f"captioned {done} photos, {changed} changed" + (f", {failed} unreadable" if failed else ""))
    return summary
