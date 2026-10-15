"""Dense retrieval with a Chinese-friendly lexical fallback."""

import logging
import re

import numpy as np

from app.config.settings import (
    CHUNK_MANIFEST_FILE,
    EMBEDDING_API_URL,
    EMBEDDING_KEY,
    EMBEDDING_MODEL,
    EMBEDDING_USE_SYSTEM_PROXY,
    TOP_K,
    RETRIEVAL_BACKEND,
)
from app.rag.embedding_client import EmbeddingClient, EmbeddingServiceError

_EMB_MAT = None
_LOCAL_INDEX = None
_SEARCH_STATUS = {
    "mode": "not_initialized",
    "entry_count": 0,
    "embedding_count": 0,
    "message": "检索尚未初始化",
}
LOGGER = logging.getLogger(__name__)
_EMBEDDING_CLIENT = (
    EmbeddingClient(
        EMBEDDING_API_URL,
        EMBEDDING_KEY,
        EMBEDDING_MODEL,
        use_system_proxy=EMBEDDING_USE_SYSTEM_PROXY,
        timeout=10,
    )
    if EMBEDDING_KEY
    else None
)

QUERY_FILLERS = (
    "你怎么看",
    "怎么看",
    "如何",
    "请问",
    "你觉得",
    "能不能",
    "是什么",
    "为什么",
    "说说",
    "聊聊",
)


def init_search():
    """Load the index and expose an honest readiness status for the UI."""
    global _EMB_MAT, _SEARCH_STATUS, _LOCAL_INDEX
    if RETRIEVAL_BACKEND in ("local", "lexical"):
        from app.rag.loader import load_entries
        from app.rag.local_index import load_local_index

        entries = load_entries()
        _EMB_MAT, _LOCAL_INDEX = None, None
        mode, message = "lexical_selected", "关键词检索（配置选择）"
        if RETRIEVAL_BACKEND == "local" and entries:
            try:
                _LOCAL_INDEX = load_local_index(entries)
                mode = "local_ready"
                stats = _LOCAL_INDEX.stats
                message = f"本地向量索引就绪（{len(entries)} 段；新建 {stats['generated']}、复用 {stats['reused']}）"
            except Exception:
                LOGGER.warning("Local embeddings unavailable; using keyword retrieval", exc_info=False)
                mode, message = "lexical_local_error", "本地向量不可用：请检查模型下载/依赖；当前使用关键词检索"
        elif RETRIEVAL_BACKEND == "local":
            mode, message = "lexical_empty", "等待添加 TXT（尚未下载或初始化模型）"
        _SEARCH_STATUS = {"mode": mode, "entry_count": len(entries),
                          "embedding_count": len(entries) if _LOCAL_INDEX is not None else 0, "message": message}
        return dict(_SEARCH_STATUS)
    _LOCAL_INDEX = None
    from app.config.settings import EMBEDDINGS_FILE
    from app.rag.index_metadata import index_metadata_issues
    from app.rag.loader import load_embeddings, load_entries
    from app.rag.manifest import ManifestError, load_manifest, validate_manifest

    _EMB_MAT = load_embeddings()
    entry_count = len(load_entries())
    embedding_count = len(_EMB_MAT) if _EMB_MAT is not None else 0

    if _EMB_MAT is None:
        mode = "lexical_no_index"
        message = "关键词检索（未找到向量索引）"
    elif embedding_count != entry_count:
        mode = "lexical_mismatch"
        message = f"关键词检索（索引待重建：{embedding_count}/{entry_count}）"
    elif not CHUNK_MANIFEST_FILE.exists():
        mode = "lexical_no_manifest"
        message = "关键词检索（向量索引缺少清单）"
        _EMB_MAT = None
    else:
        try:
            manifest = load_manifest(CHUNK_MANIFEST_FILE)
            manifest_issues = validate_manifest(manifest, load_entries(), EMBEDDING_MODEL)
        except ManifestError as exc:
            manifest_issues = [str(exc)]

        if manifest_issues:
            mode = "lexical_manifest_mismatch"
            message = "关键词检索（索引清单不匹配）"
            LOGGER.warning("Chunk manifest mismatch: %s", "; ".join(manifest_issues))
            _EMB_MAT = None
        elif index_issues := index_metadata_issues(
            EMBEDDINGS_FILE, _EMB_MAT, manifest, EMBEDDING_MODEL
        ):
            mode = "lexical_index_mismatch"
            message = "关键词检索（向量索引与知识清单不匹配）"
            LOGGER.warning("Embedding index mismatch: %s", "; ".join(index_issues))
            _EMB_MAT = None
        elif not EMBEDDING_KEY:
            mode = "lexical_missing_key"
            message = "关键词检索（未配置查询向量服务）"
            _EMB_MAT = None
        else:
            mode = "dense_ready"
            message = f"向量检索就绪（{entry_count} 条）"

    _SEARCH_STATUS = {
        "mode": mode,
        "entry_count": entry_count,
        "embedding_count": embedding_count,
        "message": message,
    }
    return dict(_SEARCH_STATUS)


def get_search_status():
    """Return a copy so UI code cannot mutate global search state."""
    return dict(_SEARCH_STATUS)


def encode_query(query):
    """Encode a query with the configured embedding service."""
    if _EMBEDDING_CLIENT is None:
        return None
    try:
        return _EMBEDDING_CLIENT.embed([query])[0]
    except EmbeddingServiceError as exc:
        LOGGER.warning("Embedding API request failed: %s", exc)
    return None


def _lexical_terms(query):
    """Extract useful Latin words and Chinese phrases/bigrams from a query."""
    normalized = query.lower()
    for filler in QUERY_FILLERS:
        normalized = normalized.replace(filler, " ")

    terms = set(re.findall(r"[a-z0-9][a-z0-9_.+-]{1,}", normalized))
    for sequence in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
        terms.add(sequence)
        if len(sequence) > 2:
            terms.update(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return {term for term in terms if len(term) >= 2}


def _lexical_search(query, entries, k):
    terms = _lexical_terms(query)
    if not terms:
        return []

    maximum = sum(len(term) ** 2 for term in terms)
    scored = []
    for entry in entries:
        normalized_entry = entry.lower()
        matched = [term for term in terms if term in normalized_entry]
        if not matched:
            continue
        raw_score = sum(len(term) ** 2 for term in matched)
        scored.append((raw_score / maximum, entry))
    return sorted(scored, reverse=True, key=lambda item: item[0])[:k]


def search_entries(query, entries, k=TOP_K):
    """Use dense retrieval when aligned, with lexical fallback."""
    if _LOCAL_INDEX is not None:
        try:
            return _LOCAL_INDEX.search(query, entries, k)
        except Exception:
            _SEARCH_STATUS.update(mode="lexical_local_error", message="本地向量查询失败，本次已使用关键词检索")
            return _lexical_search(query, entries, k)
    if _EMB_MAT is not None and len(_EMB_MAT) == len(entries):
        query_vector = encode_query(query)
        if query_vector is not None:
            scores = _EMB_MAT @ query_vector
            indexes = np.argsort(scores)[-k:][::-1]
            return [(float(scores[i]), entries[i]) for i in indexes if scores[i] > 0.3]
    return _lexical_search(query, entries, k)
