"""Local ONNX encoder and atomic, content-addressed incremental index cache."""

from functools import lru_cache
import json
import os
from pathlib import Path
import tempfile
import threading
from zipfile import BadZipFile

import numpy as np
import streamlit as st

from app.config.settings import (
    DATA_FILE, INSTANCE_CONFIG_FILE, LOCAL_CACHE_DIR, LOCAL_EMBEDDING_MODEL, PROJECT_ROOT,
)
from app.rag.manifest import content_hash

SCHEMA = 1
_LOCK = threading.RLock()


def corpus_digest(entries):
    return content_hash("\n".join(content_hash(entry) for entry in entries))


def instance_cache_dir():
    identity = str(INSTANCE_CONFIG_FILE.resolve()) + "\n" + str(DATA_FILE.resolve())
    return PROJECT_ROOT / ".runtime" / "instances" / content_hash(identity)[:20]


@lru_cache(maxsize=2)
def get_local_model(model_name=LOCAL_EMBEDDING_MODEL):
    from fastembed import TextEmbedding
    from importlib.metadata import version

    # Python 3.8 compatibility release predates Qdrant's official HF mirror.
    # Its sole GCS source currently returns 403. Add the identical ONNX model
    # source in memory; never patch site-packages or substitute another model.
    if version("fastembed") == "0.3.6" and model_name == "BAAI/bge-small-zh-v1.5":
        from fastembed.text.onnx_embedding import supported_onnx_models

        for description in supported_onnx_models:
            if description["model"] == model_name:
                description["sources"].setdefault("hf", "Qdrant/bge-small-zh-v1.5")

    cached_snapshots = LOCAL_CACHE_DIR / "models--Qdrant--bge-small-zh-v1.5" / "snapshots"
    required = ("model_optimized.onnx", "config.json", "tokenizer.json",
                "tokenizer_config.json", "special_tokens_map.json")
    cached = model_name == "BAAI/bge-small-zh-v1.5" and any(
        all((snapshot / name).is_file() for name in required)
        for snapshot in cached_snapshots.glob("*")
    )
    return TextEmbedding(model_name=model_name, cache_dir=str(LOCAL_CACHE_DIR),
                         threads=2, local_files_only=cached)


def normalized(vectors, rows):
    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[0] != rows or not matrix.shape[1] or not np.isfinite(matrix).all():
        raise ValueError("Local encoder returned invalid vectors")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if (norms <= 0).any():
        raise ValueError("Local encoder returned zero vectors")
    return matrix / norms


class LocalIndex:
    def __init__(self, entries, path, model_name=LOCAL_EMBEDDING_MODEL, model_factory=get_local_model):
        self.hashes = [content_hash(entry) for entry in entries]
        self.model_name, self.path, self.model_factory = model_name, Path(path), model_factory
        self.matrix = None
        self.stats = {"generated": 0, "reused": 0, "total": len(entries)}
        if not entries:
            return
        with _LOCK:
            self._build(entries)

    def _old_cache(self):
        try:
            with np.load(self.path, allow_pickle=False) as data:
                metadata = json.loads(str(data["metadata"].item()))
                hashes, matrix = data["hashes"].tolist(), data["vectors"].copy()
            if metadata != {"schema": SCHEMA, "model": self.model_name}:
                return {}, None
            if len(set(hashes)) != len(hashes):
                return {}, None
            matrix = normalized(matrix, len(hashes))
            return dict(zip(hashes, matrix)), matrix
        except (OSError, ValueError, KeyError, TypeError, BadZipFile, EOFError):
            return {}, None

    def _build(self, entries):
        reusable, _ = self._old_cache()
        unique = dict(zip(self.hashes, entries))
        missing = [digest for digest in unique if digest not in reusable]
        if missing:
            model = self.model_factory(self.model_name)
            generated = normalized(list(model.embed([unique[digest] for digest in missing], batch_size=32)), len(missing))
            if reusable and len(next(iter(reusable.values()))) != generated.shape[1]:
                raise ValueError("Local model dimensions changed; clear its cache before retrying")
            reusable.update(zip(missing, generated))
        self.matrix = np.asarray([reusable[digest] for digest in self.hashes], dtype=np.float32)
        self.stats.update(generated=len(missing), reused=len(unique) - len(missing))
        if missing or set(reusable) != set(unique) or not self.path.exists():
            self._save(unique, reusable)

    def _save(self, unique, vectors):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, suffix=".npz", delete=False) as handle:
                temporary = Path(handle.name)
                np.savez_compressed(handle,
                    metadata=json.dumps({"schema": SCHEMA, "model": self.model_name}),
                    hashes=np.asarray(list(unique)),
                    vectors=np.asarray([vectors[digest] for digest in unique], dtype=np.float32))
            os.replace(str(temporary), str(self.path))
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def matches(self, entries):
        return self.hashes == [content_hash(entry) for entry in entries]

    def search(self, query, entries, k):
        if not entries or not query.strip() or k <= 0:
            return []
        if not self.matches(entries):
            raise ValueError("Knowledge changed after index initialization")
        model = self.model_factory(self.model_name)
        # A one-item list also avoids FastEmbed 0.3.6's duplicate-yield bug
        # when query_embed receives a string directly.
        query_vector = normalized(list(model.query_embed([query])), 1)[0]
        if self.matrix.shape[1] != len(query_vector):
            raise ValueError("Query and index dimensions do not match")
        scores = self.matrix @ query_vector
        indexes = np.argsort(scores)[-k:][::-1]
        return [(float(scores[i]), entries[i]) for i in indexes if scores[i] > 0.3]


@st.cache_resource(show_spinner=False)
def load_local_index(entries):
    return LocalIndex(entries, instance_cache_dir() / "index.npz", model_factory=get_local_model)
