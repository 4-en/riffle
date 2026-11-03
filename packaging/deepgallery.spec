# PyInstaller spec for the standalone app: `pyinstaller packaging/deepgallery.spec`.
# A folder build (dist/DeepGallery/, or dist/DeepGallery.app on macOS) without a console
# window; starts in the one-click mode (see packaging/deepgallery_app.py).
# Needs the UI built first (cd web && npm run build) and deep_gallery installed (pip install .).

import importlib.util
import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]

if not (ROOT / "src" / "deep_gallery" / "web" / "index.html").exists():
    sys.exit("The UI is not built: run `npm run build` in web/ first.")

datas = [
    *collect_data_files("deep_gallery"),  # defaults/*.yaml and the built UI (web/)
    *collect_data_files("open_clip"),  # model configs, tokenizer vocabulary
    *collect_data_files("reverse_geocode"),  # place names for coordinates
    *copy_metadata("deep-gallery"),  # version for /api/health
]
for package in ("torch", "tqdm", "regex", "safetensors", "huggingface_hub", "filelock", "numpy", "packaging"):
    try:
        datas += copy_metadata(package)  # some check their own version at import
    except Exception:
        pass

# torchvision >= 0.29 loads its operators from _C_stable.so / .pyd, which the
# PyInstaller hook does not know yet (open_clip needs them: "torchvision::nms").
_tv = Path(importlib.util.find_spec("torchvision").origin).parent
binaries = [(str(f), "torchvision") for pattern in ("*.so", "*.pyd", "*.dll", "*.dylib") for f in _tv.glob(pattern)]

a = Analysis(
    [str(ROOT / "packaging" / "deepgallery_app.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=[*collect_submodules("deep_gallery"), *collect_submodules("uvicorn")],
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "jupyter", "notebook"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DeepGallery",
    console=False,  # no terminal window; output goes to the log (deep_gallery.frozen)
    disable_windowed_traceback=True,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="DeepGallery", upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="DeepGallery.app",
        bundle_identifier="io.github.4-en.deepgallery",
        version=VERSION,
        info_plist={
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            # No Dock icon: the app has no windows of its own, only the browser tab.
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
        },
    )
