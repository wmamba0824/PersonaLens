"""Deterministic chunk manifest generation and validation."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Dict, Iterable, List


class ManifestError(RuntimeError):
    """Raised when a chunk manifest cannot be parsed or trusted."""


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_label(text: str) -> str:
    video = re.match(r"\[视频转录\]\s*([^:：]+)", text)
    if video:
        return f"video:{video.group(1).strip()}"
    dated = re.match(r"\[(\d{4}-\d{2}-\d{2})[^\]]*\]", text)
    if dated:
        return f"dated:{dated.group(1)}"
    return "primary"


def build_manifest_records(entries: Iterable[str], model: str) -> List[Dict]:
    """Map ordered knowledge chunks to stable IDs without calling an API."""
    occurrences = {}
    records = []
    for row, text in enumerate(entries):
        digest = content_hash(text)
        occurrence = occurrences.get(digest, 0)
        occurrences[digest] = occurrence + 1
        records.append(
            {
                "row": row,
                "chunk_id": f"{digest[:20]}-{occurrence}",
                "content_sha256": digest,
                "characters": len(text),
                "source": _source_label(text),
                "preview": " ".join(text.split())[:120],
                "embedding_model": model,
            }
        )
    return records


def manifest_digest(records: Iterable[Dict]) -> str:
    ordered_hash = hashlib.sha256()
    for record in records:
        ordered_hash.update(record["chunk_id"].encode("ascii"))
        ordered_hash.update(b"\n")
    return ordered_hash.hexdigest()


def load_manifest(path: Path) -> List[Dict]:
    records = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ManifestError(
                        f"Invalid JSON at {path}:{line_number}: {exc}"
                    ) from exc
    except OSError as exc:
        raise ManifestError(f"Cannot read chunk manifest {path}: {exc}") from exc
    return records


def validate_manifest(records: List[Dict], entries: List[str], model: str) -> List[str]:
    expected = build_manifest_records(entries, model)
    if len(records) != len(expected):
        return [f"manifest rows {len(records)} != knowledge entries {len(expected)}"]

    issues = []
    for row, (actual, wanted) in enumerate(zip(records, expected)):
        for field in ("row", "chunk_id", "content_sha256", "embedding_model"):
            if actual.get(field) != wanted[field]:
                issues.append(f"row {row} has mismatched {field}")
                break
        if len(issues) >= 5:
            break
    return issues


def write_manifest(path: Path, records: Iterable[Dict]) -> None:
    """Atomically replace one exact manifest path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                handle.write("\n")
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists():
            temporary.unlink()
