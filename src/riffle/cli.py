"""Command line entry point: `riffle index | tag | serve`."""

from __future__ import annotations

import argparse
import logging
import multiprocessing
import os
import sys

from .config import load_config


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
