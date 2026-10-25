"""Plain-text, heading-aware excerpts with bounded overlap; no Markdown execution."""

import re

CHUNKER_VERSION = "sections-overlap-v1"


def _parts(text, width, overlap):
    start = 0
    while start < len(text):
        end = min(start + width, len(text))
        if end < len(text):
            candidates = [match.end() for match in re.finditer(r"[。！？；\n]|[.!?;]\s", text[start:end])]
            boundary = max((cut for cut in candidates if cut >= width * 0.6), default=width)
            end = start + boundary
        value = text[start:end].strip()
        if value:
            yield value
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)


def document_chunks(text, max_chars=400, overlap=60):
    if max_chars < 80 or overlap < 0 or overlap >= max_chars // 2:
        raise ValueError("Invalid document chunk bounds")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    headings, buffer, segments, fence = [], [], [], None
    def flush():
        body = "\n".join(buffer).strip()
        if body:
            segments.append(("\n".join(value for _, value in headings), body))
        buffer.clear()
    for line in text.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        heading = re.match(r"^(#{1,6})\s+\S.*", line) if fence is None else None
        if heading:
            flush()
            level = len(heading[1])
            headings[:] = [(depth, value) for depth, value in headings if depth < level]
            headings.append((level, line.strip()))
        elif not line.strip() and fence is None:
            flush()
        else:
            buffer.append(line)
        if marker:
            symbol = marker[1][0]
            if fence is None:
                fence = (symbol, len(marker[1]))
            elif symbol == fence[0] and len(marker[1]) >= fence[1]:
                fence = None
    flush()
    if not segments and headings:
        segments.append(("", "\n".join(value for _, value in headings)))
    for section, (heading, body) in enumerate(segments):
        prefix = heading[:min(120, max_chars // 3)] + "\n" if heading else ""
        width = max_chars - len(prefix)
        for part in _parts(body, width, min(overlap, width // 3)):
            yield {"text": prefix + part, "heading": heading, "section": section}
