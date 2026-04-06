"""
LangChain-compatible BCE reranker using the pip-installed BCEmbedding RerankerModel.

Upstream BCEmbedding.tools.langchain.BCERerank targets older LangChain/Pydantic; this
wrapper stays in bcrag and works with langchain-core 1.x + Pydantic v2.
"""
from __future__ import annotations

import os


def _patch_multiprocess_resource_tracker_py312() -> None:
    """Py3.12: RLock has no _recursion_count; skip check when absent (multiprocess/datasets)."""
    try:
        from multiprocess.resource_tracker import ResourceTracker
    except ImportError:
        return
    if getattr(ResourceTracker, "_bcrag_py312_fixed", False):
        return

    def _stop_locked(
        self,
        close=os.close,
        waitpid=os.waitpid,
        waitstatus_to_exitcode=os.waitstatus_to_exitcode,
    ):
        lock = getattr(self, "_lock", None)
        if lock is not None:
            fn = getattr(lock, "_recursion_count", None)
            if callable(fn):
                try:
                    if fn() > 1:
                        return self._reentrant_call_error()
                except Exception:
                    pass
        if self._fd is None:
            return
        if self._pid is None:
            return
        close(self._fd)
        self._fd = None
        waitpid(self._pid, 0)
        self._pid = None

    ResourceTracker._stop_locked = _stop_locked  # type: ignore[assignment]
    ResourceTracker._bcrag_py312_fixed = True


_patch_multiprocess_resource_tracker_py312()

from typing import Any, Optional, Sequence

from langchain_core.callbacks.manager import Callbacks
from langchain_core.documents import BaseDocumentCompressor, Document
from pydantic import ConfigDict, PrivateAttr


class BcragBCERerank(BaseDocumentCompressor):
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
                passages.append(passage.replace("\n", " "))
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
