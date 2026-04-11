"""SQLite-backed BCE embeddings, chunk store, dense retrieval + rerank."""
from __future__ import annotations

import ragret.compat  # noqa: F401 — multiprocess patch before torch / langchain

import hashlib
import json
import os
import sqlite3
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

import numpy as np
import torch

try:
    import intel_extension_for_pytorch as ipex  # noqa: F401
except ImportError:
    pass

from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

try:
    from langchain_huggingface import HuggingFaceEmbeddings
except ImportError:
    from langchain_community.embeddings import HuggingFaceEmbeddings

from ragret.paths import default_hf_models_dir, resolve_hf_snapshot_dir
from ragret.rerank import RagretBCERerank

EMBEDDING_MODEL = "maidalun1020/bce-embedding-base_v1"
RERANKER_MODEL = "maidalun1020/bce-reranker-base_v1"
EMBED_BATCH_SIZE = 8

# HTTP search: reuse models + SQLite snapshot per path (see search_db).
_SEARCH_INDEX_CACHE_MAX = int(os.environ.get("RAGRET_SEARCH_INDEX_CACHE_MAX", "64"))
_SEARCH_RERANK_CACHE_TOP = max(8, int(os.environ.get("RAGRET_SEARCH_RERANK_CACHE_TOP", "256")))

_search_runtime_lock = threading.RLock()
_search_embed_models: dict[str, Any] = {}
_search_rerank_models: dict[str, Any] = {}
_search_embed_infer_lock = threading.Lock()
_search_rerank_infer_lock = threading.Lock()
_search_index_cache: OrderedDict[
    str, tuple[int, int, np.ndarray, list[dict[str, Any]], str | None]
] = OrderedDict()


def _ensure_hf_cache_env() -> None:
    """Single cache root; force offline Hub for index/search."""
    default_root = default_hf_models_dir()
    raw_hf = os.environ.get("HF_HOME")
    raw_st = os.environ.get("SENTENCE_TRANSFORMERS_HOME")
    if raw_hf:
        root = Path(raw_hf).expanduser().resolve()
    elif raw_st:
        root = Path(raw_st).expanduser().resolve()
    else:
        root = default_root
    s = str(root)
    os.environ["HF_HOME"] = s
    os.environ["SENTENCE_TRANSFORMERS_HOME"] = s
    root.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"


_ensure_hf_cache_env()


def _prog(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _hf_weights_hint() -> str:
    return (
        "BCE weights are missing on disk. "
        f"HF_HOME (where ragret looks): {os.environ.get('HF_HOME', '')}. "
        "From the RAGret repo root with network run: python warmup_hf_models.py "
        "(or set HF_HOME to the directory that already contains the Hub cache)."
    )


def _looks_like_hf_cache_miss(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(
        s in msg
        for s in (
            "couldn't connect",
            "could not connect",
            "cached files",
            "local_files_only",
            "find them in the cached",
        )
    )


def _reraise_if_missing_hf_weights(exc: BaseException) -> None:
    if _looks_like_hf_cache_miss(exc):
        raise RuntimeError(
            f"{_hf_weights_hint()}\n\nOriginal: {type(exc).__name__}: {exc}",
        ) from exc
    raise exc


def load_one_file(path: Path) -> list[Document]:
    suf = path.suffix.lower()
    if suf == ".pdf":
        return PyPDFLoader(str(path)).load()
    if suf in (".txt", ".md", ".markdown"):
        return TextLoader(str(path), encoding="utf-8").load()
    raise ValueError(f"Unsupported file type: {path.suffix}. Use .pdf, .txt, or .md.")


def _iter_indexable_files(work_dir: Path) -> list[Path]:
    """Sorted list of .pdf / .txt / .md files under work_dir (recursive)."""
    if not work_dir.exists():
        raise FileNotFoundError(work_dir)
    if work_dir.is_file():
        return [work_dir.resolve()]
    out: list[Path] = []
    for glob_pat in ("**/*.pdf", "**/*.txt", "**/*.md"):
        for f in sorted(work_dir.glob(glob_pat)):
            if f.is_file():
                out.append(f.resolve())
    return out


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as bf:
        for chunk in iter(lambda: bf.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _relative_source_key(work_dir: Path, source_raw: str) -> str:
    """Stable logical path for chunks (posix, relative to corpus root)."""
    work_dir = work_dir.resolve()
    if not source_raw or not str(source_raw).strip():
        return work_dir.name
    p = Path(source_raw).expanduser()
    try:
        p = p.resolve()
    except OSError:
        p = Path(source_raw)
    try:
        rel = p.relative_to(work_dir)
    except ValueError:
        return str(p).replace("\\", "/")
    return rel.as_posix()


def _fingerprint_map(work_dir: Path) -> dict[str, str]:
    m: dict[str, str] = {}
    for f in _iter_indexable_files(work_dir):
        key = f.relative_to(work_dir.resolve()).as_posix()
        m[key] = _file_sha256(f)
    return m


def load_documents_from_dir(work_dir: Path) -> list[Document]:
    if not work_dir.exists():
        raise FileNotFoundError(work_dir)
    if work_dir.is_file():
        return load_one_file(work_dir)
    documents: list[Document] = []
    for f in _iter_indexable_files(work_dir):
        suf = f.suffix.lower()
        if suf == ".pdf":
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


def _local_snapshot_path_or_fail(repo_id: str, label: str) -> str:
    roots: list[Path] = []
    for raw in (
        os.environ.get("HF_HOME"),
        os.environ.get("SENTENCE_TRANSFORMERS_HOME"),
        str(default_hf_models_dir()),
    ):
        if not raw:
            continue
        p = Path(raw).expanduser().resolve()
        if p not in roots:
            roots.append(p)

    for root in roots:
        snap = resolve_hf_snapshot_dir(
            repo_id,
            hf_home=root,
            require_weights=True,
            require_tokenizer=True,
        )
        if snap is None:
            continue
        s = str(root)
        os.environ["HF_HOME"] = s
        os.environ["SENTENCE_TRANSFORMERS_HOME"] = s
        return str(snap.resolve())

    root = os.environ.get("HF_HOME", "")
    checked = ", ".join(str(p) for p in roots) or "(none)"
    raise RuntimeError(
        f"No on-disk snapshot for {label} ({repo_id!r}) under {root}. "
        "Expected …/hub/models--<org>--<name>/snapshots/<hash>/ or "
        "…/models--<org>--<name>/snapshots/<hash>/ (flat cache). "
        f"Checked roots: {checked}. "
        "Run from repo root with network: python warmup_hf_models.py",
    )


def make_embed_model(device: str) -> HuggingFaceEmbeddings:
    local = _local_snapshot_path_or_fail(EMBEDDING_MODEL, "BCE embedding")
    return HuggingFaceEmbeddings(
        model_name=local,
        model_kwargs={"device": device, "local_files_only": True},
        encode_kwargs={"batch_size": EMBED_BATCH_SIZE, "normalize_embeddings": True},
        cache_folder=os.environ["SENTENCE_TRANSFORMERS_HOME"],
    )


def _embed_documents_with_progress(
    embed_model: HuggingFaceEmbeddings,
    contents: list[str],
    *,
    on_batch: Callable[[int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[list[float]]:
    n = len(contents)
    if n == 0:
        return []
    out: list[list[float]] = []
    report_step = max(EMBED_BATCH_SIZE, max(1, n // 40))
    last_reported = 0
    for i in range(0, n, EMBED_BATCH_SIZE):
        if cancel_check is not None and cancel_check():
            raise BuildCancelledError("embedding cancelled")
        batch = contents[i : i + EMBED_BATCH_SIZE]
        out.extend(embed_model.embed_documents(batch))
        done = min(i + len(batch), n)
        if done - last_reported >= report_step or done == n:
            _prog(f"Embedding: {done}/{n} chunks")
            if on_batch is not None:
                on_batch(done, n)
            last_reported = done
    return out


def make_reranker(device: str, top_n: int) -> RagretBCERerank:
    dev = str(device)
    rerank_dev = "cpu" if dev.lower().startswith("xpu") else dev
    use_fp16 = rerank_dev.startswith("cuda") and torch.cuda.is_available()
    local = _local_snapshot_path_or_fail(RERANKER_MODEL, "BCE reranker")
    return RagretBCERerank(
        model=local,
        top_n=top_n,
        device=rerank_dev,
        use_fp16=use_fp16,
    )


def _xpu_available() -> bool:
    if not hasattr(torch, "xpu"):
        return False
    try:
        return bool(torch.xpu.is_available())
    except Exception:
        return False


def resolve_device() -> str:
    """Pick compute device: env RAGRET_DEVICE, else CUDA, else Intel XPU. CPU is not supported."""
    override = (os.environ.get("RAGRET_DEVICE") or "").strip()
    if override:
        if override.lower() == "cpu":
            raise RuntimeError(
                "ragret does not support a CPU backend; use an NVIDIA GPU (CUDA) or "
                "Intel GPU (torch.xpu). See README (Dockerfile / Dockerfile.xpu).",
            )
        return override
    if torch.cuda.is_available():
        return "cuda:0"
    if _xpu_available():
        return "xpu:0"
    raise RuntimeError(
        "No GPU available: neither CUDA nor Intel XPU is usable. Use the NVIDIA "
        "Dockerfile with --gpus all, or Dockerfile.xpu with Intel device passthrough, "
        "or install CUDA or PyTorch-with-XPU locally (see README).",
    )


def _require_non_cpu_device(device: str) -> None:
    if str(device).strip().lower() == "cpu":
        raise RuntimeError(
            "ragret does not support device='cpu'; use CUDA or Intel XPU (see README).",
        )


IndexProgressFn = Callable[[str, int, str | None], None]


class BuildCancelledError(Exception):
    """Raised when a long-running index operation is cancelled (e.g. user abort)."""


def index_workdir(
    work_dir: Path,
    db_path: Path,
    *,
    chunk_size: int = 1500,
    chunk_overlap: int = 200,
    device: str | None = None,
    progress: IndexProgressFn | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> None:
    work_dir = work_dir.resolve()
    db_path = db_path.resolve()
    device = device or resolve_device()
    _require_non_cpu_device(device)

    def report(phase: str, pct: int, detail: str | None = None) -> None:
        if progress is not None:
            progress(phase, max(0, min(100, pct)), detail)

    report("load", 3, None)
    _prog(f"Loading documents from {work_dir} …")
    if cancel_check is not None and cancel_check():
        raise BuildCancelledError("cancelled before load")
    documents = load_documents_from_dir(work_dir)
    report("load", 10, f"{len(documents)} doc(s)")
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    texts = splitter.split_documents(documents)
    if not texts:
        raise ValueError("No chunks after split.")
    report("chunk", 14, f"{len(texts)} chunks")
    _prog(
        f"Split into {len(texts)} chunk(s); embedding (device={device}, batch={EMBED_BATCH_SIZE}) …",
    )

    try:
        embed_model = make_embed_model(device)
        contents = [d.page_content for d in texts]
        vectors = _embed_documents_with_progress(
            embed_model,
            contents,
            on_batch=(lambda done, total: report("embed", 15 + int(69 * done / max(total, 1)), f"{done}/{total}"))
            if progress
            else None,
            cancel_check=cancel_check,
        )
    except Exception as e:
        _reraise_if_missing_hf_weights(e)
    if not vectors:
        raise RuntimeError("Embedding returned empty.")
    dim = len(vectors[0])
    arr = np.asarray(vectors, dtype=np.float32)

    if cancel_check is not None and cancel_check():
        raise BuildCancelledError("cancelled before sqlite write")

    conn = _connect(db_path)
    try:
        _init_schema(conn)
        _clear_chunks(conn)
        _set_meta(conn, "schema_version", "1")
        _set_meta(conn, "embedding_model", EMBEDDING_MODEL)
        _set_meta(conn, "embed_dim", str(dim))
        _set_meta(conn, "indexed_work_dir", str(work_dir))
        _set_meta(conn, "indexed_at", str(int(time.time())))

        n_write = len(texts)
        last_src: str | None = None
        local_i = 0
        for i, doc in enumerate(texts):
            meta = json.dumps(doc.metadata, ensure_ascii=False)
            blob = arr[i].tobytes()
            src = _relative_source_key(work_dir, str(doc.metadata.get("source", "") or ""))
            if src != last_src:
                local_i = 0
                last_src = src
            conn.execute(
                """
                INSERT INTO chunks(source, chunk_index, content, metadata_json, embedding)
                VALUES(?, ?, ?, ?, ?)
                """,
                (src, local_i, doc.page_content, meta, blob),
            )
            local_i += 1
            step = max(1, n_write // 20)
            if (i + 1) % step == 0 or i + 1 == n_write:
                _prog(f"Writing SQLite: {i + 1}/{n_write} rows")
                if progress is not None:
                    pct = 85 + int(13 * (i + 1) / max(n_write, 1))
                    report("sqlite", min(99, pct), f"{i + 1}/{n_write}")
        fp_map = _fingerprint_map(work_dir)
        _set_meta(conn, "source_fingerprints", json.dumps(fp_map, sort_keys=True, ensure_ascii=False))
        _set_meta(conn, "chunk_size", str(chunk_size))
        _set_meta(conn, "chunk_overlap", str(chunk_overlap))
        conn.commit()
    finally:
        conn.close()

    report("done", 100, None)
    print(f"Indexed {len(texts)} chunk(s) into {db_path} (dim={dim}, device={device}).")


def try_incremental_update_workdir(
    work_dir: Path,
    db_path: Path,
    *,
    chunk_size: int = 1500,
    chunk_overlap: int = 200,
    device: str | None = None,
    progress: IndexProgressFn | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> bool:
    """Apply minimal SQLite changes from a new corpus tarball (same logical KB).

    Uses ``meta.source_fingerprints`` (sha256 per relative path) and deletes/re-embeds
    only added/removed/changed files. Returns False if a full rebuild is required.
    """
    work_dir = work_dir.resolve()
    db_path = db_path.resolve()
    if not db_path.is_file():
        return False

    device = device or resolve_device()
    _require_non_cpu_device(device)

    def report(phase: str, pct: int, detail: str | None = None) -> None:
        if progress is not None:
            progress(phase, max(0, min(100, pct)), detail)

    conn = _connect(db_path)
    try:
        _init_schema(conn)
        n_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        if int(n_chunks or 0) == 0:
            return False
        raw_fp = _get_meta(conn, "source_fingerprints")
        if not raw_fp:
            return False
        try:
            old_fp: dict[str, str] = json.loads(raw_fp)
        except json.JSONDecodeError:
            return False
        if not isinstance(old_fp, dict):
            return False
        stored_cs = _get_meta(conn, "chunk_size")
        stored_co = _get_meta(conn, "chunk_overlap")
        if stored_cs and int(stored_cs) != chunk_size:
            return False
        if stored_co and int(stored_co) != chunk_overlap:
            return False
        dim_s = _get_meta(conn, "embed_dim")
        if not dim_s:
            return False
        dim = int(dim_s)
        emb_model = _get_meta(conn, "embedding_model")
        if emb_model and emb_model != EMBEDDING_MODEL:
            return False
    finally:
        conn.close()

    try:
        new_fp = _fingerprint_map(work_dir)
    except ValueError:
        return False
    if not new_fp:
        return False

    old_keys = set(old_fp.keys())
    new_keys = set(new_fp.keys())
    removed = old_keys - new_keys
    added_or_changed = {k for k in new_keys if k not in old_fp or old_fp[k] != new_fp[k]}

    if not removed and not added_or_changed:
        if cancel_check is not None and cancel_check():
            raise BuildCancelledError("cancelled")
        conn = _connect(db_path)
        try:
            _set_meta(conn, "indexed_work_dir", str(work_dir))
            _set_meta(conn, "indexed_at", str(int(time.time())))
            _set_meta(conn, "source_fingerprints", json.dumps(new_fp, sort_keys=True, ensure_ascii=False))
        finally:
            conn.close()
        report("done", 100, "no file changes")
        _prog("Incremental index: no corpus file changes; metadata updated only.")
        return True

    report("load", 8, f"+{len(added_or_changed)} ~{len(removed)} removed")
    if cancel_check is not None and cancel_check():
        raise BuildCancelledError("cancelled")

    try:
        embed_model = make_embed_model(device)
    except Exception as e:
        _reraise_if_missing_hf_weights(e)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    conn = _connect(db_path)
    try:
        for rel in sorted(removed):
            conn.execute("DELETE FROM chunks WHERE source = ?", (rel,))
        conn.commit()

        to_embed: list[tuple[str, Document]] = []
        for rel in sorted(added_or_changed):
            conn.execute("DELETE FROM chunks WHERE source = ?", (rel,))
            fp = (work_dir / rel).resolve()
            try:
                fp.relative_to(work_dir)
            except ValueError:
                conn.rollback()
                return False
            if not fp.is_file():
                conn.rollback()
                return False
            docs = load_one_file(fp)
            for d in docs:
                d.metadata["source"] = str(fp)
            parts = splitter.split_documents(docs)
            for d in parts:
                to_embed.append((rel, d))
        conn.commit()

        if not to_embed:
            _set_meta(conn, "source_fingerprints", json.dumps(new_fp, sort_keys=True, ensure_ascii=False))
            _set_meta(conn, "indexed_work_dir", str(work_dir))
            _set_meta(conn, "indexed_at", str(int(time.time())))
            conn.commit()
            report("done", 100, "deleted only")
            _prog(f"Incremental index: removed {len(removed)} file(s) from index.")
            return True

        contents = [d.page_content for _, d in to_embed]
        n_emb = len(contents)
        report("chunk", 12, f"{n_emb} new chunks")

        try:
            vectors = _embed_documents_with_progress(
                embed_model,
                contents,
                on_batch=(
                    lambda done, total: report(
                        "embed",
                        15 + int(69 * done / max(total, 1)),
                        f"{done}/{total}",
                    )
                )
                if progress
                else None,
                cancel_check=cancel_check,
            )
        except Exception as e:
            _reraise_if_missing_hf_weights(e)
        if not vectors or len(vectors) != n_emb:
            raise RuntimeError("Embedding returned empty or wrong count.")
        arr = np.asarray(vectors, dtype=np.float32)
        if arr.shape[1] != dim:
            raise RuntimeError("Embedding dimension mismatch; full rebuild required.")

        last_src: str | None = None
        local_i = 0
        for row_i, (rel, doc) in enumerate(to_embed):
            if rel != last_src:
                local_i = 0
                last_src = rel
            meta = json.dumps(doc.metadata, ensure_ascii=False)
            blob = arr[row_i].tobytes()
            conn.execute(
                """
                INSERT INTO chunks(source, chunk_index, content, metadata_json, embedding)
                VALUES(?, ?, ?, ?, ?)
                """,
                (rel, local_i, doc.page_content, meta, blob),
            )
            local_i += 1
            step = max(1, n_emb // 20)
            if (row_i + 1) % step == 0 or row_i + 1 == n_emb:
                if progress is not None:
                    pct = 85 + int(13 * (row_i + 1) / max(n_emb, 1))
                    report("sqlite", min(99, pct), f"{row_i + 1}/{n_emb}")

        _set_meta(conn, "source_fingerprints", json.dumps(new_fp, sort_keys=True, ensure_ascii=False))
        _set_meta(conn, "indexed_work_dir", str(work_dir))
        _set_meta(conn, "indexed_at", str(int(time.time())))
        _set_meta(conn, "chunk_size", str(chunk_size))
        _set_meta(conn, "chunk_overlap", str(chunk_overlap))
        conn.commit()
    finally:
        conn.close()

    report("done", 100, None)
    _prog(
        f"Incremental index: removed {len(removed)} file(s), "
        f"re-indexed {len(added_or_changed)} file(s), {len(to_embed)} chunk(s).",
    )
    return True


def _load_index(conn: sqlite3.Connection) -> tuple[np.ndarray, list[dict[str, Any]]]:
    rows = conn.execute(
        "SELECT id, source, chunk_index, content, metadata_json, embedding FROM chunks ORDER BY id"
    ).fetchall()
    if not rows:
        raise ValueError(
            "Index is empty. Build it first: python ragret.py index --dir <path>.",
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


def _file_stat_sig(path: Path) -> tuple[int, int]:
    st = path.stat()
    ns = getattr(st, "st_mtime_ns", None)
    if ns is None:
        ns = int(st.st_mtime * 1_000_000_000)
    return int(ns), int(st.st_size)


def _load_index_snapshot_ro(db_path: Path) -> tuple[np.ndarray, list[dict[str, Any]], str | None]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        stored_model = _get_meta(conn, "embedding_model")
        matrix, records = _load_index(conn)
        return matrix, records, stored_model
    finally:
        conn.close()


def _resolve_search_index(db_path: Path) -> tuple[np.ndarray, list[dict[str, Any]], str | None]:
    """Shared in-memory vectors + chunk metadata; invalidated on file mtime/size change."""
    db_path = db_path.resolve()
    key = str(db_path)
    sig = _file_stat_sig(db_path)
    with _search_runtime_lock:
        ent = _search_index_cache.get(key)
        if ent is not None:
            if ent[0] == sig[0] and ent[1] == sig[1]:
                _search_index_cache.move_to_end(key)
                return ent[2], ent[3], ent[4]
            del _search_index_cache[key]

    matrix, records, stored_model = _load_index_snapshot_ro(db_path)
    sig2 = _file_stat_sig(db_path)
    with _search_runtime_lock:
        ent = _search_index_cache.get(key)
        if ent is not None and ent[0] == sig2[0] and ent[1] == sig2[1]:
            _search_index_cache.move_to_end(key)
            return ent[2], ent[3], ent[4]
        while len(_search_index_cache) >= _SEARCH_INDEX_CACHE_MAX:
            _search_index_cache.popitem(last=False)
        _search_index_cache[key] = (sig2[0], sig2[1], matrix, records, stored_model)
        _search_index_cache.move_to_end(key)
    return matrix, records, stored_model


def _get_search_embed_model(device: str) -> Any:
    with _search_runtime_lock:
        m = _search_embed_models.get(device)
        if m is None:
            m = make_embed_model(device)
            _search_embed_models[device] = m
        return m


def _get_search_rerank_model(device: str) -> Any:
    with _search_runtime_lock:
        r = _search_rerank_models.get(device)
        if r is None:
            r = make_reranker(device, top_n=_SEARCH_RERANK_CACHE_TOP)
            _search_rerank_models[device] = r
        return r


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
    _require_non_cpu_device(device)
    dev_key = str(device)
    embed_model = _get_search_embed_model(dev_key)
    try:
        with _search_embed_infer_lock:
            q = np.asarray(embed_model.embed_query(query), dtype=np.float32)
    except Exception as e:
        _reraise_if_missing_hf_weights(e)

    matrix, records, stored_model = _resolve_search_index(db_path)
    if stored_model and stored_model != EMBEDDING_MODEL:
        print(
            f"Warning: index was built with {stored_model}, this build expects {EMBEDDING_MODEL}.",
        )

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

    try:
        want = max(1, int(rerank_top_n))
        if want > _SEARCH_RERANK_CACHE_TOP:
            reranker = make_reranker(device, top_n=want)
            with _search_rerank_infer_lock:
                ranked = list(reranker.compress_documents(candidates, query))
        else:
            reranker = _get_search_rerank_model(dev_key)
            with _search_rerank_infer_lock:
                ranked = list(reranker.compress_documents(candidates, query))
            ranked = ranked[:want]
    except Exception as e:
        _reraise_if_missing_hf_weights(e)

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
