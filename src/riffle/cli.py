"""Command line entry point: `riffle index | tag | serve | paths | upscale-raw`."""

from __future__ import annotations

import argparse
import logging
import multiprocessing
import os
import sys

from .config import load_config


def _active_selections(cfg) -> str:
    """The active profile's selections file, with the profile's name if not the default."""
    from . import profiles

    slug = profiles.active_slug(cfg)
    path = profiles.path_for(cfg, slug)
    return str(path) if slug == profiles.DEFAULT else f"{path}  (profile: {slug})"


def _raw_models(cfg) -> str:
    """Where `upscale-raw` and the export look for their checkpoints."""
    from pathlib import Path

    from . import rawsr

    specs = {rawsr.checkpoint_spec(cfg, k) for k in rawsr.KINDS}
    folders = {str(Path(s).parent) for s in specs if ":" not in s}
    return ", ".join(sorted(folders)) if len(specs) == 2 and len(folders) == 1 else ", ".join(sorted(specs))


def _upscale_raw(cfg, args) -> int:
    """`riffle upscale-raw`: each RAW to a DNG of its name; returns the exit code."""
    import time
    from pathlib import Path

    from . import rawsr

    src, dst = Path(args.input).expanduser(), Path(args.output).expanduser()
    kind = "mono" if args.bw else "rgb"
    if not args.bw and (args.luminance or args.filter != "none" or args.mix):
        print("--luminance, --filter and --mix are for --bw", file=sys.stderr)
        return 2
    mix = None
    if args.mix:
        try:
            mix = [float(v) for v in args.mix.split(",")]
            assert len(mix) == 3
        except (ValueError, AssertionError):
            print("--mix takes three weights: r,g,b", file=sys.stderr)
            return 2
    ok = rawsr.availability(cfg, kind)
    if not ok["available"]:
        print(f"cannot upscale RAWs: {ok['reason']}", file=sys.stderr)
        return 1

    if src.is_dir():
        if dst.suffix.lower() == ".dng":
            print("the output for a folder is a folder", file=sys.stderr)
            return 2
        found = src.rglob("*") if args.recursive else src.iterdir()
        pairs = [(f, dst / f.relative_to(src).with_suffix(".dng")) for f in sorted(found)
                 if f.is_file() and f.suffix.lower() in cfg.raw_extensions]
    elif src.is_file():
        pairs = [(src, dst if dst.suffix.lower() == ".dng" else dst / f"{src.stem}.dng")]
    else:
        print(f"not found: {src}", file=sys.stderr)
        return 1
    if not pairs:
        print(f"no RAW files in {src}", file=sys.stderr)
        return 1

    failed = done = skipped = 0
    for n, (raw, out) in enumerate(pairs, 1):
        head = f"[{n}/{len(pairs)}] {raw.name}"
        if out.resolve() == raw.resolve():
            print(f"{head}: the output would replace the RAW; choose another folder", file=sys.stderr)
            failed += 1
            continue
        if out.exists() and not args.overwrite:
            print(f"{head}: {out} is already there (--overwrite to replace it)")
            skipped += 1
            continue
        reason = rawsr.check(raw)
        if reason:
            print(f"{head}: skipped, {reason}")
            skipped += 1
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(f".{out.name}.partial")
        t0 = time.perf_counter()
        try:
            r = rawsr.upscale_raw(cfg, raw, tmp, kind=kind, denoise=args.denoise, base="luminance" if args.luminance else "film",
                                  filter=args.filter, mix=mix)
        except Exception as e:  # one bad file does not stop the rest
            tmp.unlink(missing_ok=True)
            print(f"{head}: failed, {e}", file=sys.stderr)
            failed += 1
            continue
        tmp.replace(out)
        done += 1
        w, h = r["size"]
        print(f"{head} → {out} ({w}×{h}, {time.perf_counter() - t0:.0f} s)")
    print(f"{done} upscaled" + (f", {skipped} skipped" if skipped else "") + (f", {failed} failed" if failed else ""))
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> None:
    multiprocessing.freeze_support()  # thumbnail workers in a frozen (standalone) build
    parser = argparse.ArgumentParser(
        prog="riffle",
        description="Riffle: find the best photos in your library. Without a command, "
        "it starts (or reuses) the app and opens it in your browser.",
    )
    parser.add_argument(
        "-c", "--config", default=None,
        help="config file (default: ./config.yaml if present, else the user config; see `riffle paths`)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("index", help="scan, thumbnail, embed, tag and group duplicates (incremental)")
    sub.add_parser("tag", help="re-tag from stored embeddings after editing vocabulary or thresholds")
    serve = sub.add_parser("serve", help="run the web UI and API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true", help="auto-reload on code changes")
    sub.add_parser("paths", help="show where the config, your flags and the derived data live")
    up = sub.add_parser(
        "upscale-raw", help="RAWs to ×2 linear DNGs with Riffle's Bayer model (replaces demosaicing)",
        description="Upscale RAWs ×2 into linear DNGs that open in darktable like the RAW. Needs the raw "
        "extra and a checkpoint (see `riffle paths` and the README). Experimental: slow (about 45 s per "
        "20 MP RAW on a fast GPU) and large (about 500 MB per DNG); results may be soft or show artefacts; "
        "trained on one camera (OM-5 Mark II), RGGB Bayer RAWs only.",
    )
    up.add_argument("input", help="a RAW file, or a folder of them")
    up.add_argument("output", help="a .dng file (for one RAW), or a folder (created if needed)")
    up.add_argument("-r", "--recursive", action="store_true", help="also the input folder's subfolders (mirrored in the output)")
    up.add_argument("--bw", action="store_true", help="black and white (the black-and-white model)")
    up.add_argument("--denoise", type=float, default=0.3,
                    help="share of the noise to remove, 0.05-1 (default 0.3; lower keeps more grain and fine texture)")
    up.add_argument("--luminance", action="store_true", help="--bw as the eye sees brightness, instead of like panchromatic film")
    up.add_argument("--filter", default="none", choices=["none", "yellow", "orange", "red", "green", "blue"],
                    help="--bw through a colour filter (yellow to red darken skies)")
    up.add_argument("--mix", help="--bw with own weights r,g,b on white-balanced camera RGB")
    up.add_argument("--overwrite", action="store_true", help="replace DNGs that are already there (default: skip them)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        cfg = load_config(args.config)
    except FileNotFoundError as e:
        sys.exit(str(e))

    if args.command is None:
        from .launch import launch

        launch(cfg)
        return

    if args.command == "paths":
        rows = [
            ("config", cfg.path),
            ("vocabulary", cfg.vocabulary_path),
            ("your flags", _active_selections(cfg)),
            ("derived data", cfg.data_dir),
            ("RAW models", _raw_models(cfg)),
        ]
        for label, path in rows:
            print(f"{label:13} {path}")
        return

    if args.command == "upscale-raw":
        sys.exit(_upscale_raw(cfg, args))

    if args.command == "index":
        from .index import run_index

        run_index(cfg)
    elif args.command == "tag":
        from .index import run_tag

        run_tag(cfg)
    elif args.command == "serve":
        os.environ["RIFFLE_CONFIG"] = str(cfg.path)
        print(f"Serving on http://{args.host}:{args.port} (Ctrl+C to stop)")
        if args.reload:  # development: uvicorn re-imports the app on code changes
            import uvicorn

            try:
                uvicorn.run(
                    "riffle.server:create_app",
                    factory=True,
                    host=args.host,
                    port=args.port,
                    reload=True,
                    reload_dirs=[os.path.dirname(__file__)],
                    timeout_graceful_shutdown=3,
                )
            except KeyboardInterrupt:
                pass
        else:
            from .server import create_app, run_server

            run_server(create_app(cfg), args.host, args.port)


if __name__ == "__main__":
    main()
