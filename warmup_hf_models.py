"""Pre-download BCE embedding + reranker weights (local: ./models; Docker: HF_HOME=/opt/hf from image)."""
from __future__ import annotations

import os
from pathlib import Path

from ragret.paths import default_hf_models_dir, resolve_hf_snapshot_dir

# Docker sets HF_HOME=/opt/hf in the Dockerfile before this runs; locally unset → repo ./models.
os.environ.setdefault("HF_HOME", str(default_hf_models_dir()))
_root = Path(os.environ["HF_HOME"]).expanduser().resolve()
os.environ["HF_HOME"] = str(_root)
os.environ["SENTENCE_TRANSFORMERS_HOME"] = str(_root)
_root.mkdir(parents=True, exist_ok=True)

from BCEmbedding.models import RerankerModel

try:
    from langchain_huggingface import HuggingFaceEmbeddings
except ImportError:
    from langchain_community.embeddings import HuggingFaceEmbeddings

EMBEDDING_MODEL = "maidalun1020/bce-embedding-base_v1"
RERANKER_MODEL = "maidalun1020/bce-reranker-base_v1"


def main() -> None:
    hf = _root
    if resolve_hf_snapshot_dir(
        EMBEDDING_MODEL,
        hf_home=hf,
        require_weights=True,
        require_tokenizer=True,
    ) is None:
        print(f"Downloading BCE embedding {EMBEDDING_MODEL} …", flush=True)
        emb = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"batch_size": 1, "normalize_embeddings": True},
            cache_folder=os.environ["SENTENCE_TRANSFORMERS_HOME"],
        )
        _ = emb.embed_query("warmup")
    else:
        print(f"Skip download: {EMBEDDING_MODEL} already cached under {hf}", flush=True)

    if resolve_hf_snapshot_dir(
        RERANKER_MODEL,
        hf_home=hf,
        require_weights=True,
        require_tokenizer=True,
    ) is None:
        print(f"Downloading BCE reranker {RERANKER_MODEL} …", flush=True)
        RerankerModel(
            model_name_or_path=RERANKER_MODEL,
            device="cpu",
            use_fp16=False,
        )
    else:
        print(f"Skip download: {RERANKER_MODEL} already cached under {hf}", flush=True)

    print("HF warmup OK, cache root (HF_HOME=SENTENCE_TRANSFORMERS_HOME)=", os.environ["HF_HOME"], flush=True)


if __name__ == "__main__":
    main()
