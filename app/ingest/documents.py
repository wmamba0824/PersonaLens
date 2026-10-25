"""Explicit, bounded local TXT/Markdown imports into one creator's corpus."""

import hashlib
from pathlib import PurePosixPath, PureWindowsPath

from app.rag.loader import KNOWLEDGE_SUFFIXES

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024


def decode_document(raw):
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_FILE_BYTES:
        raise ValueError("单份资料须为非空文本，且不超过2MB。")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gb18030")
        except UnicodeDecodeError:
            raise ValueError("资料编码无法识别，请另存为UTF-8后重试。") from None
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text or len(text) > 200000 or any(ord(char) < 32 and char not in "\n\t" for char in text):
        raise ValueError("资料为空、超过20万字或包含二进制控制字符，请提供纯文本。")
    return text


def prepare_documents(files, identity, author_name, confirmed_same_author=False):
    if not confirmed_same_author:
        raise ValueError("请先确认这些资料属于当前人物且有权使用，不自动合并多个作者。")
    if not 1 <= len(files) <= 20 or sum(len(raw) for _, raw in files) > MAX_TOTAL_BYTES:
        raise ValueError("每次导入1–20份资料，合计不超过8MB。")
    records, names, contents, skipped = [], set(), set(), 0
    for name, raw in files:
        if not isinstance(name, str):
            raise ValueError("资料文件名格式不正确。")
        path = PurePosixPath(name)
        if (len(name) > 150 or name.startswith(".")
                or path.name != name or PureWindowsPath(name).name != name or ":" in name
                or any(ord(char) < 32 for char in name) or path.suffix.lower() not in KNOWLEDGE_SUFFIXES):
            raise ValueError("仅接受普通文件名的TXT/MD/Markdown，不接受路径、隐藏文件或其他格式。")
        stem = path.stem.lower()
        if path.suffix.lower() != ".txt" and (stem in {"readme", "agents", "skill"} or stem.startswith("readme.")):
            raise ValueError("README/AGENTS/SKILL等说明文件不作为人物资料；若是正式原文请改名后导入。")
        text = decode_document(raw)
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if name.casefold() in names:
            raise ValueError("同次导入存在同名文件，请先分别命名，避免覆盖。")
        names.add(name.casefold())
        if content_hash in contents:
            skipped += 1
            continue
        contents.add(content_hash)
        identifier = "file:" + hashlib.sha256(name.casefold().encode("utf-8")).hexdigest()
        records.append({"id": identifier, "author_uid": identity.uid, "author_name": author_name,
                        "published_at": "日期未知", "url": "", "type": "document", "text": text,
                        "source_file": name, "title": path.stem, "content_hash": content_hash,
                        "source_type": "local_txt" if path.suffix.lower() == ".txt" else "local_markdown",
                        "source_platform": "local", "identity_link": "user_confirmed_same_creator",
                        "uncertainty": "作者归属由导入者确认；文件名和上传日期不是原文发布日期。"})
    return records, skipped


def import_documents(store, files, confirmed_same_author=False):
    """Same filename updates its prior record; identical content is idempotent."""
    data = store.read()
    records, skipped = prepare_documents(files, store.identity, data["name"], confirmed_same_author)
    with store.operation("import_documents"):
        data = store.read()
        existing = {row["id"]: row for row in data["records"]}
        accepted, changed = 0, 0
        for row in records:
            if any(old.get("content_hash") == row["content_hash"] and old["id"] != row["id"] for old in existing.values()):
                skipped += 1
                continue
            if existing.get(row["id"]) == row:
                skipped += 1
                continue
            changed += row["id"] in existing
            existing[row["id"]] = row
            accepted += 1
        result = {"records": list(existing.values()), "name": data["name"], "complete": False,
                  "warning": "包含用户确认归属的本地资料；日期未知不会推定为上传日期。", "skipped": skipped}
        store.save_collection(result)
    return {"accepted": accepted, "updated": changed, "skipped": skipped}
