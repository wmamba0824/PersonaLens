"""Prepare bounded, identifiable evidence excerpts for the chat prompt."""

from app.config.settings import MAX_CONTEXT_CHARS
from app.rag.loader import source_label
from app.rag.search import _lexical_terms


def _excerpt(entry, query, limit):
    if len(entry) <= limit:
        return entry
    matches = [term for term in _lexical_terms(query) if term in entry.lower()]
    term = max(matches, key=lambda value: (len(value), value)) if matches else None
    anchor = entry.lower().find(term) if term else 0
    start = max(0, min(anchor - limit // 3, len(entry) - limit))
    return ("…" if start else "") + entry[start:start + limit] + "…（节选）"


def prepare_context(entries, query="", max_chars=MAX_CONTEXT_CHARS):
    """Share a budget across distinct entries; sources describe only supplied excerpts."""
    blocks, sources, seen = [], [], set()
    remaining = max_chars
    for entry in entries:
        if entry in seen:
            continue
        seen.add(entry)
        label = source_label(entry)
        number = len(blocks) + 1
        header = f"【资料{number}】{label}\n"
        separator = "\n\n---\n\n" if blocks else ""
        budget = remaining - len(header) - len(separator)
        if budget < 100:
            break
        text = _excerpt(entry, query, min(1000, max(1, budget - 12)))
        block = header + text
        blocks.append(block)
        sources.append(f"[资料{number}] {label}")
        remaining -= len(separator) + len(block)
    return "\n\n---\n\n".join(blocks), sources
