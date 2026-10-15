"""Shared dynamic text extraction for legacy and bounded acquisition."""

from html import unescape
import re


def extract_dynamic_text(desc_text, major):
    if isinstance(desc_text, dict):
        text = str(desc_text.get("text") or "").strip()
    else:
        text = unescape(re.sub(r"<[^>]+>", " ", str(desc_text or ""))).strip()
    major = major if isinstance(major, dict) else {}
    kind = major.get("type", "")
    if kind in ("MAJOR_TYPE_ARCHIVE", "MAJOR_TYPE_ARTICLE"):
        key = "archive" if kind == "MAJOR_TYPE_ARCHIVE" else "article"
        title = (major.get(key) or {}).get("title", "")
        if title and title not in text:
            prefix = f"发布了{'视频' if key == 'archive' else '专栏'}《{title}》"
            text = f"{prefix} {text}".strip()
    elif not text and kind == "MAJOR_TYPE_DRAW":
        text = str((major.get("draw") or {}).get("text") or "").strip()
    elif not text and kind == "MAJOR_TYPE_OPUS":
        text = str(((major.get("opus") or {}).get("summary") or {}).get("text") or "").strip()
    elif not text and kind == "MAJOR_TYPE_LIVE_RCMD":
        content = (major.get("live_rcmd") or {}).get("content", "")
        text = str(content.get("text", "") if isinstance(content, dict) else content).strip()
    elif not text and kind == "MAJOR_TYPE_LIVE":
        title = (major.get("live") or {}).get("title", "")
        text = f"直播: {title}" if title else ""
    return text.strip()
