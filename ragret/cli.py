"""Argparse entry for ``serve``."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from ragret.quick_qa_agent import set_quick_qa_llm_config

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
        "--llm-base-url",
        dest="llm_base_url",
        type=str,
        default="",
        help="OpenAI-compatible base URL for Quick QA LangGraph agent.",
    )
    pv.add_argument(
        "--llm-model",
        dest="llm_model",
        type=str,
        default="",
        help="LLM model name used by Quick QA agent.",
    )
    pv.add_argument(
        "--llm-api-key",
        dest="llm_api_key",
        type=str,
        default="",
        help="API key used by Quick QA agent.",
    )
    pv.add_argument(
        "--registry",
        type=Path,
        default=None,
        help="Registry JSON path (default: ./runtime/ragret_registry.json or env RAGRET_REGISTRY)",
    )

    args = p.parse_args()
    if args.registry is not None:
        os.environ["RAGRET_REGISTRY"] = str(args.registry.expanduser().resolve())
    set_quick_qa_llm_config(
        base_url=str(args.llm_base_url or "").strip(),
        model=str(args.llm_model or "").strip(),
        api_key=str(args.llm_api_key or "").strip(),
    )
    from server import run_server

    return run_server(host=args.host, port=args.port, repo_root=REPO_ROOT)
