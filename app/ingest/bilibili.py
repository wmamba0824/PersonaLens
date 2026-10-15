"""Bounded public dynamic collection using the existing MD endpoint/parser.

No paid posts, comments, downloads, redirects, CAPTCHA workarounds or retries.
"""

from datetime import datetime, timezone, timedelta
import os
import time

import requests

from app.ingest.identity import parse_creator
from app.ingest.text import extract_dynamic_text


class CollectionError(RuntimeError):
    """Safe message without cookies or raw provider bodies."""


ALLOWED_TYPES = {"DYNAMIC_TYPE_WORD", "DYNAMIC_TYPE_DRAW", "DYNAMIC_TYPE_AV", "DYNAMIC_TYPE_ARTICLE"}


def normalize_dynamic(item, uid):
    if not isinstance(item, dict) or item.get("type") not in ALLOWED_TYPES:
        return None
    modules = item.get("modules") or {}
    author = modules.get("module_author") or {}
    dynamic = modules.get("module_dynamic") or {}
    additional = dynamic.get("additional") or {}
    basic = item.get("basic") or {}
    if (str(author.get("mid", "")) != uid or item.get("orig") or dynamic.get("is_charge")
            or basic.get("is_only_fans") or "UPPOWER" in str(additional.get("type", ""))):
        return None
    identifier = str(item.get("id_str", ""))
    if not identifier.isascii() or not identifier.isdigit() or len(identifier) > 30:
        return None
    # Metadata titles are not treated as sentences spoken by the author.
    major = dynamic.get("major") or {}
    fallback = major if major.get("type") in {"MAJOR_TYPE_DRAW", "MAJOR_TYPE_OPUS"} else {}
    text = extract_dynamic_text(dynamic.get("desc"), fallback)
    if len(text) < 12:
        return None
    try:
        timestamp = int(author.get("pub_ts", 0))
        date = datetime.fromtimestamp(timestamp, timezone(timedelta(hours=8))).isoformat() if timestamp > 0 else "日期未知"
    except (ValueError, TypeError, OverflowError, OSError):
        date = "日期未知"
    return {"id": identifier, "author_uid": uid, "author_name": str(author.get("name") or "")[:100],
            "url": "https://t.bilibili.com/" + identifier, "published_at": date,
            "type": "dynamic", "text": text[:5000], "truncated": len(text) > 5000}


def collect_dynamics(identity, max_items=30, max_pages=3, session=None, progress=None):
    identity = parse_creator(identity.uid)
    if not 1 <= max_items <= 100 or not 1 <= max_pages <= 5:
        raise ValueError("采集范围需为1–100条、1–5页。")
    owned = session is None
    session = session or requests.Session()
    if owned:
        session.trust_env = False
        for env_name, cookie_name in (("BILI_SESSDATA", "SESSDATA"), ("BILI_JCT", "bili_jct"),
                                      ("BUVID3", "buvid3"), ("DEDEUSERID", "DedeUserID")):
            value = os.getenv(env_name)
            if value:
                session.cookies.set(cookie_name, value, domain="api.bilibili.com", secure=True)
    records, seen, offset, offsets, skipped = [], set(), "", set(), 0
    complete, warning = False, ""
    started = time.monotonic()
    try:
        for page in range(max_pages):
            if time.monotonic() - started > 75:
                warning = "达到采集时间上限，保留已获取内容。"
                break
            if progress:
                progress(f"正在获取第{page + 1}页（最多{max_pages}页）…")
            try:
                response = session.get("https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space",
                    params={"host_mid": identity.uid, "offset": offset, "type": "all"},
                    headers={"User-Agent": "Mozilla/5.0", "Referer": identity.url},
                    timeout=(5, 15), allow_redirects=False)
                try:
                    if response.status_code != 200:
                        raise CollectionError(f"B站返回HTTP {response.status_code}。可能需要登录或遇到平台限制；没有重试或绕过。")
                    payload = response.json()
                finally:
                    response.close()
                if not isinstance(payload, dict) or payload.get("code") != 0:
                    raise CollectionError("B站接口拒绝请求。请核对本地登录态或稍后手动重试；不自动绕过限制。")
                data = payload.get("data")
                if not isinstance(data, dict) or not isinstance(data.get("items", []), list):
                    raise CollectionError("B站返回结构不兼容，未将其视作空知识库。")
                items = data.get("items", [])
                consumed = 0
                for item in items:
                    consumed += 1
                    record = normalize_dynamic(item, identity.uid)
                    if record is None:
                        skipped += 1
                    elif record["id"] not in seen:
                        seen.add(record["id"])
                        records.append(record)
                    if len(records) >= max_items:
                        break
                if len(records) >= max_items and (consumed < len(items) or data.get("has_more")):
                    warning = "达到条数上限；未获取全部历史内容。"
                    break
                if not data.get("has_more"):
                    complete = True
                    break
                if len(records) >= max_items:
                    warning = "达到条数上限；未获取全部历史内容。"
                    break
                next_offset = str(data.get("offset") or "")
                if not next_offset or next_offset in offsets:
                    warning = "分页游标缺失或重复，已停止；覆盖范围可能不完整。"
                    break
                offsets.add(next_offset)
                offset = next_offset
                if page + 1 < max_pages:
                    time.sleep(0.4)
            except (requests.RequestException, ValueError, TypeError, AttributeError, CollectionError) as exc:
                safe = str(exc) if isinstance(exc, CollectionError) else "B站连接失败或返回格式异常；未自动重试。"
                if not records:
                    raise CollectionError(safe) from None
                warning = "部分采集完成；" + safe
                break
        else:
            warning = "达到页数上限；未获取全部历史内容。"
    finally:
        if owned:
            session.close()
    name = next((record["author_name"] for record in records if record["author_name"]), "UP主 " + identity.uid)
    return {"records": records, "name": name, "complete": complete, "warning": warning, "skipped": skipped}
