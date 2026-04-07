"""Argparse entry for ``index`` | ``search`` | ``serve``."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from bcrag.registry import IndexRegistry, resolve_db_path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "index.sqlite"


def _registry_file() -> Path:
    env = os.environ.get("BCRAG_REGISTRY")
    if env:
        return Path(env).expanduser().resolve()
    return (REPO_ROOT / "bcrag_registry.json").resolve()


def _run_index(args: argparse.Namespace) -> int:
    from bcrag.rag import index_workdir

    try:
        work_path, db_path = resolve_db_path(args.dir, args.name)
    except FileNotFoundError as e:
        print(f"Error: path not found: {e}", file=sys.stderr)
        return 1

    print(f"Input:    {work_path}")
    print(f"SQLite:   {db_path}")

    try:
        index_workdir(
            work_path,
            db_path,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.register_as:
        try:
            reg = IndexRegistry(_registry_file())
            key = reg.add(args.register_as, db_path)
            print(f"Registered in {reg.path}: {key} -> {db_path.resolve()}", flush=True)
        except ValueError as e:
            print(f"Error: --register-as: {e}", file=sys.stderr)
            return 1
    return 0


def _run_search(args: argparse.Namespace) -> int:
    from bcrag.rag import search_db

    db = args.db
    if db is None:
        env_db = os.environ.get("BCRAG_DB")
        db = Path(env_db) if env_db else DEFAULT_DB
    db = db.resolve()
    if not db.is_file():
        print(f"Index not found: {db}", file=sys.stderr)
        print("Build one with:  python bcrag.py index --dir <corpus_path>", file=sys.stderr)
        print("Or set BCRAG_DB to your .sqlite file.", file=sys.stderr)
        return 1

    try:
        text = search_db(
            db,
            args.query,
            k=args.k,
            score_threshold=args.threshold,
            rerank_top_n=args.top_n,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(text)
    return 0


def main() -> int:
    os.environ.setdefault("HF_ENDPOINT", "https://huggingface.co")

    p = argparse.ArgumentParser(
        prog="bcrag",
        description=(
            "bcrag — local RAG index (BCE embedding + rerank) stored in SQLite. "
            "Subcommands: index (build), search (query), serve (HTTP API)."
        ),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser(
        "index",
        help="Chunk documents, embed with BCE, write vectors + text to SQLite",
    )
    pi.add_argument(
        "--dir",
        type=Path,
        required=True,
        help="Corpus folder (recursive) or one .pdf / .txt / .md file",
    )
    pi.add_argument(
        "--name",
        type=str,
        default=None,
        help="SQLite filename without extension; default from folder or file stem",
    )
    pi.add_argument("--chunk-size", type=int, default=1500)
    pi.add_argument("--chunk-overlap", type=int, default=200)
    pi.add_argument(
        "--register-as",
        type=str,
        default=None,
        metavar="NAME",
        help="After indexing, register NAME for HTTP server (GET /NAME?query=...); see `serve`",
    )

    pv = sub.add_parser(
        "serve",
        help="HTTP API (read-only): GET /indexes, GET /{name}?query=...; index via CLI only",
    )
    pv.add_argument("--host", type=str, default="127.0.0.1", help="Bind address (default: loopback)")
    pv.add_argument("--port", type=int, default=8765)
    pv.add_argument(
        "--registry",
        type=Path,
        default=None,
        help="Registry JSON path (default: ./bcrag_registry.json or env BCRAG_REGISTRY)",
    )

    ps = sub.add_parser(
        "search",
        help="Load index, retrieve + rerank, print passages (no LLM answer synthesis)",
    )
    ps.add_argument(
        "-d",
        "--db",
        type=Path,
        default=None,
        help=f"SQLite index path; default: {DEFAULT_DB} or env BCRAG_DB",
    )
    ps.add_argument("-q", "--query", type=str, required=True, help="Search query")
    ps.add_argument("-k", type=int, default=10, help="Vector recall size")
    ps.add_argument("--threshold", type=float, default=0.3)
    ps.add_argument("--top-n", type=int, default=5, help="Rerank output size")

    args = p.parse_args()

    if args.cmd == "index":
        return _run_index(args)
    if args.cmd == "serve":
        if args.registry is not None:
            os.environ["BCRAG_REGISTRY"] = str(args.registry.expanduser().resolve())
        from bcrag.server import run_server

        return run_server(host=args.host, port=args.port, repo_root=REPO_ROOT)
    return _run_search(args)
