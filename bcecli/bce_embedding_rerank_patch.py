"""BCEmbedding rerank tokenization compat for tokenizers without ``encode_plus`` (newer transformers)."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List


def _encode_plus_compat(tokenizer: Any, text: str, **kwargs: Any) -> Dict[str, List[int]]:
    enc = getattr(tokenizer, "encode_plus", None)
    if callable(enc):
        return enc(text, **kwargs)
    batch = tokenizer(text, **kwargs)
    out: Dict[str, List[int]] = {}
    for k in batch.keys():
        v = batch[k]
        if hasattr(v, "tolist"):
            v = v.tolist()
        if isinstance(v, list) and v and isinstance(v[0], list):
            v = v[0]
        out[k] = list(v) if not isinstance(v, list) else v
    return out


def reranker_tokenize_preproc(
    query: str,
    passages: List[str],
    tokenizer=None,
    max_length: int = 512,
    overlap_tokens: int = 80,
):
    assert tokenizer is not None, "Please provide a valid tokenizer for tokenization!"
    sep_id = tokenizer.sep_token_id

    def _merge_inputs(chunk1_raw, chunk2):
        chunk1 = deepcopy(chunk1_raw)

        chunk1["input_ids"].append(sep_id)
        chunk1["input_ids"].extend(chunk2["input_ids"])
        chunk1["input_ids"].append(sep_id)

        chunk1["attention_mask"].append(chunk2["attention_mask"][0])
        chunk1["attention_mask"].extend(chunk2["attention_mask"])
        chunk1["attention_mask"].append(chunk2["attention_mask"][0])

        if "token_type_ids" in chunk1:
            token_type_ids = [1 for _ in range(len(chunk2["token_type_ids"]) + 2)]
            chunk1["token_type_ids"].extend(token_type_ids)
        return chunk1

    query_inputs = _encode_plus_compat(tokenizer, query, truncation=False, padding=False)
    max_passage_inputs_length = max_length - len(query_inputs["input_ids"]) - 2
    assert max_passage_inputs_length > 100, (
        "Your query is too long! Please make sure your query less than 400 tokens!"
    )
    overlap_tokens_implt = min(overlap_tokens, max_passage_inputs_length // 4)

    res_merge_inputs = []
    res_merge_inputs_pids = []
    for pid, passage in enumerate(passages):
        passage_inputs = _encode_plus_compat(
            tokenizer,
            passage,
            truncation=False,
            padding=False,
            add_special_tokens=False,
        )
        passage_inputs_length = len(passage_inputs["input_ids"])

        if passage_inputs_length <= max_passage_inputs_length:
            qp_merge_inputs = _merge_inputs(query_inputs, passage_inputs)
            res_merge_inputs.append(qp_merge_inputs)
            res_merge_inputs_pids.append(pid)
        else:
            start_id = 0
            while start_id < passage_inputs_length:
                end_id = start_id + max_passage_inputs_length
                sub_passage_inputs = {k: v[start_id:end_id] for k, v in passage_inputs.items()}
                start_id = end_id - overlap_tokens_implt if end_id < passage_inputs_length else end_id

                qp_merge_inputs = _merge_inputs(query_inputs, sub_passage_inputs)
                res_merge_inputs.append(qp_merge_inputs)
                res_merge_inputs_pids.append(pid)

    return res_merge_inputs, res_merge_inputs_pids


def patch_bce_embedding_reranker_tokenize() -> None:
    """Re-bind BCE rerank tokenization for tokenizers without ``encode_plus``.

    ``BCEmbedding.models.reranker`` does ``from .utils import reranker_tokenize_preproc``, so the
    copy on the reranker module must be updated too (runtime lookup uses that global).
    """
    import BCEmbedding.models.reranker as rerank_mod
    import BCEmbedding.models.utils as utils_mod

    if getattr(utils_mod, "_bcecli_rerank_tokenize_patched", False):
        return

    utils_mod.reranker_tokenize_preproc = reranker_tokenize_preproc
    rerank_mod.reranker_tokenize_preproc = reranker_tokenize_preproc
    utils_mod._bcecli_rerank_tokenize_patched = True
