"""Argparse entry for ``serve``."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    os.environ.setdefault("HF_ENDPOINT", "https://huggingface.co")

    p = argparse.ArgumentParser(
        prog="ragret",
        description=(
            "RAGret — local RAG index (BCE embedding + rerank) stored in SQLite. "
            "Subcommands: serve (HTTP API)."
        ),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pv = sub.add_parser(
        "serve",
        help="HTTP API service",
    )
    pv.add_argument("--host", type=str, default="127.0.0.1", help="Bind address (default: loopback)")
    pv.add_argument("--port", type=int, default=8765)
    pv.add_argument(
        "--registry",
        type=Path,
        default=None,
        help="Registry JSON path (default: ./runtime/ragret_registry.json or env RAGRET_REGISTRY)",
    )

    args = p.parse_args()
    if args.registry is not None:
        os.environ["RAGRET_REGISTRY"] = str(args.registry.expanduser().resolve())
    from server import run_server

    return run_server(host=args.host, port=args.port, repo_root=REPO_ROOT)
