"""Entrypoint for the detached index worker."""

import argparse
from pathlib import Path

from .worker import run_worker


def main() -> None:
    parser = argparse.ArgumentParser(description="Davinci Semantic Search")
    subcommands = parser.add_subparsers(dest="command", required=True)
    worker = subcommands.add_parser("worker", help="Process a project index queue")
    worker.add_argument("--config", type=Path, required=True)
    worker.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    run_worker(args.config, args.db)


if __name__ == "__main__":
    main()
