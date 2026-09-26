"""Command line entry point: `riffle index | tag | serve`."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .config import load_config


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="riffle", description="Riffle: find the best photos in your library.")
    parser.add_argument(
        "-c", "--config", default=None,
        help="config file (default: ./config.yaml if present, else the user config; see `riffle paths`)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("index", help="scan, thumbnail, embed, tag and group duplicates (incremental)")
    sub.add_parser("tag", help="re-tag from stored embeddings after editing vocabulary or thresholds")
    serve = sub.add_parser("serve", help="run the web UI and API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true", help="auto-reload on code changes")
    sub.add_parser("paths", help="show where the config, your flags and the derived data live")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        cfg = load_config(args.config)
    except FileNotFoundError as e:
        sys.exit(str(e))

    if args.command == "paths":
        rows = [
            ("config", cfg.path),
            ("vocabulary", cfg.vocabulary_path),
            ("your flags", cfg.selections_path),
            ("derived data", cfg.data_dir),
        ]
        for label, path in rows:
            print(f"{label:13} {path}")
        return

    if args.command == "index":
        from .index import run_index

        run_index(cfg)
    elif args.command == "tag":
        from .index import run_tag

        run_tag(cfg)
    elif args.command == "serve":
        import uvicorn

        os.environ["RIFFLE_CONFIG"] = str(cfg.path)
        print(f"Serving on http://{args.host}:{args.port}")
        uvicorn.run(
            "riffle.server:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            reload=args.reload,
            reload_dirs=[os.path.dirname(__file__)] if args.reload else None,
        )


if __name__ == "__main__":
    main()
