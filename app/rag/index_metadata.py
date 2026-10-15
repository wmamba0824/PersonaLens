"""Bind a built embedding matrix to its ordered knowledge manifest."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np

from app.rag.manifest import manifest_digest


SCHEMA_VERSION = 1


def metadata_path(index_path: Path) -> Path:
    return index_path.with_suffix(index_path.suffix + ".meta.json")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_index_metadata(index_path: Path, matrix, manifest, model: str) -> Path:
    """Write the build identity only after the completed matrix is on disk."""
    if matrix.ndim != 2 or matrix.dtype != np.float32:
        raise ValueError("Embedding matrix must be two-dimensional float32")
    if matrix.shape[0] != len(manifest):
        raise ValueError("Embedding rows do not match the manifest")

    payload = {
        "schema_version": SCHEMA_VERSION,
        "embedding_model": model,
        "manifest_digest": manifest_digest(manifest),
        "entry_count": int(matrix.shape[0]),
        "vector_dimensions": int(matrix.shape[1]),
        "index_sha256": _file_sha256(index_path),
    }
    path = metadata_path(index_path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def index_metadata_issues(index_path: Path, matrix, manifest, model: str):
    """Fail closed when the matrix, metadata, or ordered knowledge differ."""
    path = metadata_path(index_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f"index metadata unavailable or invalid: {exc}"]

    if not isinstance(payload, dict):
        return ["index metadata must be a JSON object"]
    if matrix.ndim != 2 or matrix.dtype != np.float32:
        return ["embedding matrix must be two-dimensional float32"]

    expected = {
        "schema_version": SCHEMA_VERSION,
        "embedding_model": model,
        "manifest_digest": manifest_digest(manifest),
        "entry_count": int(matrix.shape[0]),
        "vector_dimensions": int(matrix.shape[1]),
    }
    for field, value in expected.items():
        if payload.get(field) != value:
            return [f"index metadata {field} mismatch"]
    if matrix.shape[0] != len(manifest):
        return ["embedding rows do not match the manifest"]
    try:
        actual_hash = _file_sha256(index_path)
    except OSError as exc:
        return [f"embedding file cannot be verified: {exc}"]
    if payload.get("index_sha256") != actual_hash:
        return ["embedding file checksum mismatch"]
    return []
