"""Knowledge-base and embedding loading."""

import logging
import os
import re

import streamlit as st

from app.config.settings import DATA_FILE, EMBEDDINGS_FILE, TRANSCRIPT_DIR, XUEQIU_DIR


LOGGER = logging.getLogger(__name__)


def read_txt(path):
    """Accept common Windows TXT encodings; never replace invalid bytes silently."""
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return raw.decode("gb18030")
        except UnicodeDecodeError as exc:
            raise ValueError(f"Cannot read TXT {path.name}; save it as UTF-8") from exc


def knowledge_files():
    """Directory mode is isolated from legacy creator sources."""
    if DATA_FILE.is_dir():
        return sorted(
            (path for path in DATA_FILE.rglob("*") if path.is_file()
             and path.suffix.lower() == ".txt" and not path.is_symlink()),
            key=lambda path: path.relative_to(DATA_FILE).as_posix(),
        )
    return [DATA_FILE] if DATA_FILE.is_file() else []


def split_txt(text, max_chars=400):
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        paragraph = paragraph.strip()
        for start in range(0, len(paragraph), max_chars):
            part = paragraph[start:start + max_chars].strip()
            if part:
                yield part


def read_entries(include_sources=False):
    """Read all configured knowledge sources without Streamlit caching."""
    entries = []
    origins = {}

    def add_entry(text, label):
        entries.append(text)
        origins.setdefault(text, set()).add(label)
    if DATA_FILE.is_dir():
        for path in knowledge_files():
            label = f"TXT · {path.relative_to(DATA_FILE).as_posix()}"
            for part in split_txt(read_txt(path)):
                add_entry(part, label)
        return (entries, origins) if include_sources else entries
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        text = f.read()
    for raw in re.split(r"\n\s*\n", text.strip()):
        raw = raw.strip()
        if raw:
            add_entry(raw, f"资料 · {DATA_FILE.name}")
    for fname in ("posts.txt", "replies.txt"):
        fpath = XUEQIU_DIR / fname
        if not fpath.exists():
            continue
        try:
            with fpath.open("r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        add_entry(line.strip(), f"雪球 · {fname}")
        except OSError as exc:
            LOGGER.warning("Failed to read Xueqiu data %s: %s", fpath, exc)
    if os.path.isdir(TRANSCRIPT_DIR):
        for fname in sorted(os.listdir(TRANSCRIPT_DIR)):
            if not fname.endswith(".txt"):
                continue
            fpath = os.path.join(TRANSCRIPT_DIR, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
                for chunk in chunk_text(content, os.path.splitext(fname)[0]):
                    add_entry(chunk, f"视频 · {fname}")
            except Exception as exc:
                LOGGER.warning("Failed to read transcript %s: %s", fname, exc)
    return (entries, origins) if include_sources else entries


@st.cache_resource
def load_source_labels():
    """Retain file provenance without changing text order or index hashes."""
    return read_entries(include_sources=True)[1]


def source_label(entry):
    labels = load_source_labels().get(entry, {"来源未标记"})
    match = re.match(r"\[(\d{4}-\d{2}-\d{2})", entry)
    if match:
        date = match.group(1)
    else:
        match = re.match(r"\[视频转录\]\s*(\d{8})", entry)
        date = f"{match[1][:4]}-{match[1][4:6]}-{match[1][6:8]}" if match else "日期未标记"
    return date + " · " + " / ".join(sorted(labels))


@st.cache_resource
def load_entries():
    """Load knowledge entries once per Streamlit process."""
    return read_entries()


def chunk_text(text, source_name, chunk_lines=20):
    lines = [line.strip() for line in text.strip().split("\n") if line.strip()]
    if not lines:
        return []
    chunks = []
    for i in range(0, len(lines), chunk_lines):
        text_block = " ".join(lines[i:i + chunk_lines])
        if len(text_block) > 20:
            chunks.append(f"[视频转录] {source_name}: {text_block}")
    return chunks


def load_embeddings():
    """Load the precomputed embedding matrix using memory mapping."""
    import numpy as np

    if not EMBEDDINGS_FILE.exists():
        return None
    try:
        matrix = np.load(EMBEDDINGS_FILE, mmap_mode="r")
        LOGGER.info(
            "Embedding index loaded: %s rows, %.0f MB",
            len(matrix),
            matrix.nbytes / 1024 / 1024,
        )
        return matrix
    except Exception as exc:
        LOGGER.warning("Failed to load embedding index: %s", exc)
        return None
