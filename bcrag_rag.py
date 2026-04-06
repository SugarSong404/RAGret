"""bcrag: SQLite store for BCE embeddings + text chunks; dense retrieval + BCE rerank."""
from __future__ import annotations

from bcrag_bce_rerank import BcragBCERerank

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

EMBEDDING_MODEL = "maidalun1020/bce-embedding-base_v1"
RERANKER_MODEL = "maidalun1020/bce-reranker-base_v1"


def load_one_file(path: Path) -> list[Document]:
    suf = path.suffix.lower()
    if suf == ".pdf":
        return PyPDFLoader(str(path)).load()
    if suf in (".txt", ".md", ".markdown"):
        return TextLoader(str(path), encoding="utf-8").load()
    raise ValueError(f"Unsupported file type: {path.suffix}. Use .pdf, .txt, or .md.")


def load_documents_from_dir(work_dir: Path) -> list[Document]:
    if not work_dir.exists():
        raise FileNotFoundError(work_dir)
    if work_dir.is_file():
        return load_one_file(work_dir)
    documents: list[Document] = []
    for glob_pat, loader in [
        ("**/*.pdf", "pdf"),
        ("**/*.txt", "txt"),
        ("**/*.md", "txt"),
    ]:
        for f in sorted(work_dir.glob(glob_pat)):
            if not f.is_file():
                continue
            if loader == "pdf":
                documents.extend(PyPDFLoader(str(f)).load())
            else:
                documents.extend(TextLoader(str(f), encoding="utf-8").load())
    if not documents:
        raise ValueError(f"No .pdf / .txt / .md files under: {work_dir}")
    return documents


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            chunk_index INTEGER NOT NULL DEFAULT 0,
            content TEXT NOT NULL,
            metadata_json TEXT,
            embedding BLOB NOT NULL,
            UNIQUE(source, chunk_index)
        );
        """
    )
    conn.commit()


def _clear_chunks(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM chunks;")
    conn.commit()


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def _get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def make_embed_model(device: str) -> HuggingFaceEmbeddings:
    return HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"device": device},
        encode_kwargs={"batch_size": 8, "normalize_embeddings": True},
    )


def make_reranker(device: str, top_n: int) -> BcragBCERerank:
    return BcragBCERerank(
        model=RERANKER_MODEL,
        top_n=top_n,
        device=device,
        use_fp16=torch.cuda.is_available(),
    )


def resolve_device() -> str:
    return "cpu" if not torch.cuda.is_available() else "cuda:0"


def index_workdir(
    work_dir: Path,
    db_path: Path,
    *,
    chunk_size: int = 1500,
    chunk_overlap: int = 200,
    device: str | None = None,
) -> None:
    work_dir = work_dir.resolve()
    db_path = db_path.resolve()
    device = device or resolve_device()

    documents = load_documents_from_dir(work_dir)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    texts = splitter.split_documents(documents)
    if not texts:
        raise ValueError("No chunks after split.")

    embed_model = make_embed_model(device)
    contents = [d.page_content for d in texts]
    vectors = embed_model.embed_documents(contents)
    if not vectors:
        raise RuntimeError("Embedding returned empty.")
    dim = len(vectors[0])
    arr = np.asarray(vectors, dtype=np.float32)

    conn = _connect(db_path)
    try:
        _init_schema(conn)
        _clear_chunks(conn)
        _set_meta(conn, "schema_version", "1")
        _set_meta(conn, "embedding_model", EMBEDDING_MODEL)
        _set_meta(conn, "embed_dim", str(dim))
        _set_meta(conn, "indexed_work_dir", str(work_dir))
        _set_meta(conn, "indexed_at", str(int(time.time())))

        for i, doc in enumerate(texts):
            meta = json.dumps(doc.metadata, ensure_ascii=False)
            blob = arr[i].tobytes()
            src = str(doc.metadata.get("source", "") or work_dir)
            conn.execute(
                """
                INSERT INTO chunks(source, chunk_index, content, metadata_json, embedding)
                VALUES(?, ?, ?, ?, ?)
                """,
                (src, i, doc.page_content, meta, blob),
            )
        conn.commit()
    finally:
        conn.close()

    print(f"Indexed {len(texts)} chunk(s) into {db_path} (dim={dim}, device={device}).")


def _load_index(conn: sqlite3.Connection) -> tuple[np.ndarray, list[dict[str, Any]]]:
    rows = conn.execute(
        "SELECT id, source, chunk_index, content, metadata_json, embedding FROM chunks ORDER BY id"
    ).fetchall()
    if not rows:
        raise ValueError(
            "Index is empty. Build it first: python bcrag.py index --dir <path>.",
        )
    dim = _get_meta(conn, "embed_dim")
    if not dim:
        raise ValueError("Missing embed_dim in meta table.")
    dim = int(dim)
    embs = []
    records = []
    for rid, source, cidx, content, meta_json, emb_blob in rows:
        vec = np.frombuffer(emb_blob, dtype=np.float32)
        if vec.size != dim:
            raise ValueError(f"Embedding size mismatch for id={rid}")
        embs.append(vec)
        try:
            meta = json.loads(meta_json) if meta_json else {}
        except json.JSONDecodeError:
            meta = {}
        records.append(
            {
                "id": rid,
                "source": source,
                "chunk_index": cidx,
                "content": content,
                "metadata": meta,
            }
        )
    matrix = np.stack(embs, axis=0)
    return matrix, records


def search_db(
    db_path: Path,
    query: str,
    *,
    device: str | None = None,
    k: int = 10,
    score_threshold: float = 0.3,
    rerank_top_n: int = 5,
) -> str:
    db_path = db_path.resolve()
    if not db_path.is_file():
        raise FileNotFoundError(db_path)

    device = device or resolve_device()
    embed_model = make_embed_model(device)
    q = np.asarray(embed_model.embed_query(query), dtype=np.float32)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        stored_model = _get_meta(conn, "embedding_model")
        if stored_model and stored_model != EMBEDDING_MODEL:
            print(
                f"Warning: index was built with {stored_model}, this build expects {EMBEDDING_MODEL}.",
            )
        matrix, records = _load_index(conn)
    finally:
        conn.close()

    scores = matrix @ q
    order = np.argsort(-scores)
    candidates: list[Document] = []
    for idx in order:
        s = float(scores[idx])
        if s < score_threshold:
            continue
        r = records[int(idx)]
        meta = dict(r["metadata"])
        meta["source"] = meta.get("source") or r["source"]
        meta["chunk_index"] = r["chunk_index"]
        meta["vector_score"] = s
        candidates.append(Document(page_content=r["content"], metadata=meta))
        if len(candidates) >= k:
            break

    if not candidates:
        return (
            f"No passages above similarity threshold ({score_threshold}); "
            f"try rephrasing or lower --threshold.\n"
            f"(Total chunks in index: {len(records)})"
        )

    reranker = make_reranker(device, top_n=rerank_top_n)
    ranked = list(reranker.compress_documents(candidates, query))

    lines = [
        f"Query: {query}",
        f"From {len(records)} chunks: recalled {len(candidates)}, kept {len(ranked)} after rerank.",
        "",
        "--- Retrieved passages ---",
        "",
    ]
    for i, d in enumerate(ranked, 1):
        rs = d.metadata.get("relevance_score", "")
        vs = d.metadata.get("vector_score", "")
        src = d.metadata.get("source", "")
        lines.append(f"[{i}] rerank={rs}  vector={vs}")
        if src:
            lines.append(f"    source: {src}")
        lines.append(d.page_content.strip())
        lines.append("")
    lines.append("--- Short summary ---")
    lines.append(
        ranked[0].page_content.strip()[:800]
        + ("…" if len(ranked[0].page_content) > 800 else "")
    )
    return "\n".join(lines)
