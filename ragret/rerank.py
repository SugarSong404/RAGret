"""
LangChain-compatible BCE reranker (BCEmbedding RerankerModel + langchain-core 1.x / Pydantic v2).

Upstream ``BCEmbedding.tools.langchain.BCERerank`` targets older LangChain; this wrapper stays here.
"""
from __future__ import annotations

import os
from typing import Any, Optional, Sequence

import ragret.compat  # noqa: F401 — multiprocess patch before other imports

from ragret.bce_embedding_rerank_patch import patch_bce_embedding_reranker_tokenize

patch_bce_embedding_reranker_tokenize()

_RERANK_PASSAGE_CHARS = max(256, int(os.environ.get("RAGRET_RERANK_PASSAGE_CHARS", "1000")))


def _clip_passage_for_rerank(passage: str) -> str:
    """BCE reranker is 512 tokens total (query+passage); long index chunks must be clipped."""
    t = passage.replace("\n", " ").strip()
    if len(t) <= _RERANK_PASSAGE_CHARS:
        return t
    return t[: _RERANK_PASSAGE_CHARS - 1] + "…"

from langchain_core.callbacks.manager import Callbacks
from langchain_core.documents import BaseDocumentCompressor, Document
from pydantic import ConfigDict, PrivateAttr


class RagretBCERerank(BaseDocumentCompressor):
    """Rerank passages with Netease Youdao BCE RerankerModel (installed via pip)."""

    top_n: int = 5
    model: str = "maidalun1020/bce-reranker-base_v1"
    device: Optional[str] = None
    use_fp16: bool = False

    _model: Any = PrivateAttr(default=None)

    model_config = ConfigDict(
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    def model_post_init(self, __context: Any) -> None:
        try:
            from BCEmbedding.models import RerankerModel
        except ImportError as e:
            raise ImportError(
                "Install BCEmbedding: pip install BCEmbedding>=0.1.5",
            ) from e
        self._model = RerankerModel(
            model_name_or_path=self.model,
            device=self.device,
            use_fp16=self.use_fp16,
            local_files_only=True,
        )

    def compress_documents(
        self,
        documents: Sequence[Document],
        query: str,
        callbacks: Optional[Callbacks] = None,
    ) -> Sequence[Document]:
        if len(documents) == 0:
            return []
        doc_list = list(documents)

        passages = []
        valid_doc_list = []
        invalid_doc_list = []
        for d in doc_list:
            passage = d.page_content
            if isinstance(passage, str) and len(passage) > 0:
                passages.append(_clip_passage_for_rerank(passage))
                valid_doc_list.append(d)
            else:
                invalid_doc_list.append(d)

        rerank_result = self._model.rerank(query, passages)
        final_results = []
        for score, doc_id in zip(
            rerank_result["rerank_scores"],
            rerank_result["rerank_ids"],
        ):
            doc = valid_doc_list[doc_id]
            doc.metadata["relevance_score"] = score
            final_results.append(doc)
        for doc in invalid_doc_list:
            doc.metadata["relevance_score"] = 0
            final_results.append(doc)

        return final_results[: self.top_n]
