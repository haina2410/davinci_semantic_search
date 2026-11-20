"""Entrypoint for the detached index worker."""

import argparse
from io import TextIOWrapper
from pathlib import Path
import sys

from .index import Index
from .worker import recover_scratch, run_queue_process, run_worker


def main() -> None:
    parser = argparse.ArgumentParser(description="Davinci Semantic Search")
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name in ("worker", "queue"):
        worker = subcommands.add_parser(name)
        worker.add_argument("--config", type=Path, required=True)
        worker.add_argument("--db", type=Path, required=True)
        worker.add_argument("--run", required=True)
    cleanup = subcommands.add_parser("cleanup")
    cleanup.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "cleanup":
        raise SystemExit(1 if recover_scratch(Index(args.db)) else 0)
    # Worker logs can contain Vietnamese even under a legacy Windows code page.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    action = run_worker if args.command == "worker" else run_queue_process
    action(args.config, args.db, args.run)


if __name__ == "__main__":
    main()
