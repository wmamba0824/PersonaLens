"""Offline WeChat JSON exports linked to a creator by explicit user assertion.

No crawling or model calls. Only catalogued, successful full article bodies are
imported; analysis reports, cookies and temporary reader links are not sources.
"""

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlencode, urlsplit


MAX_FILES = 1000
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 40 * 1024 * 1024


def public_article_url(raw):
    """Keep only a public short URL or the four stable article identity fields."""
    if not isinstance(raw, str):
        return ""
    try:
        value = urlsplit(raw)
        if (value.scheme not in {"http", "https"} or value.hostname != "mp.weixin.qq.com"
                or value.username or value.password or value.port not in (None, 80, 443)
                or any(char.isspace() for char in raw)):
            return ""
        if re.fullmatch(r"/s/[A-Za-z0-9_-]{4,120}", value.path):
            return "https://mp.weixin.qq.com" + value.path
        if value.path not in {"/s", "/s/"}:
            return ""
        query = parse_qs(value.query, max_num_fields=30)
        keys = ("__biz", "mid", "idx", "sn")
        if any(len(query.get(key, [])) != 1 for key in keys):
            return ""
        clean = {key: query[key][0] for key in keys}
        if (not re.fullmatch(r"[A-Za-z0-9+/=_-]{4,128}", clean["__biz"])
                or not re.fullmatch(r"[0-9]{1,30}", clean["mid"])
                or not re.fullmatch(r"[0-9]{1,3}", clean["idx"])
                or not re.fullmatch(r"[0-9a-fA-F]{32}", clean["sn"])):
            return ""
        return "https://mp.weixin.qq.com/s?" + urlencode(clean)
    except (ValueError, TypeError):
        return ""


def _date(catalog):
    try:
        raw = str(catalog.get("send_time") or "")
        if not re.fullmatch(r"[0-9]{1,12}", raw):
            raise ValueError()
        value = datetime.fromtimestamp(int(raw), timezone(timedelta(hours=8)))
        if not 2000 <= value.year <= 2100:
            raise ValueError()
        return value.isoformat(), "export_send_time"
    except (ValueError, OverflowError, OSError):
        return "日期未知", "unknown"


def normalize_article(row, identity, account_name):
    if not isinstance(row, dict) or not isinstance(row.get("catalog"), dict):
        return None, "not_article_export"
    catalog = row["catalog"]
    if row.get("fetch_status") != "ok" or row.get("body_source") != "js_content" or row.get("http_status") != 200:
        return None, "not_successful_full_body"
    if str(catalog.get("deleted", "0")) != "0":
        return None, "deleted"
    if str(row.get("account_name") or "").strip() != account_name:
        return None, "different_account"
    if row.get("title_matches_catalog") is not True:
        return None, "title_not_verified"
    identifier = str(catalog.get("appmsgid") or "")
    if not re.fullmatch(r"[0-9]{1,30}", identifier):
        return None, "invalid_article_id"
    title = str(catalog.get("title") or "").strip()
    text = row.get("body_text")
    text = text.strip() if isinstance(text, str) else ""
    if not title or not 20 <= len(text) <= 60000:
        return None, "empty_short_or_overlong_body"
    url = public_article_url(row.get("final_url")) or public_article_url(catalog.get("url"))
    if not url:
        return None, "no_stable_public_url"
    # Do not accidentally promote temporary reader/session links embedded in text.
    if re.search(r"(?:tempkey|pass_ticket|wxuin|wxsid|key)=", text, re.I):
        return None, "temporary_link_in_body"
    date, date_source = _date(catalog)
    stable = hashlib.sha256((account_name + "\0" + identifier).encode("utf-8")).hexdigest()[:24]
    return {"id": "wechat_" + stable, "author_uid": identity.uid, "author_name": account_name,
        "source_platform": "wechat", "source_type": "wechat_article", "type": "article",
        "account_name": account_name, "article_id": identifier,
        "byline": str(row.get("author_name") or "")[:100],
        "identity_link": "user_confirmed_same_creator", "title": title[:500],
        "published_at": date, "date_source": date_source, "url": url,
        "source_file": "公众号导出/" + identifier + ".json", "text": text, "truncated": False,
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "originality_marker": str(catalog.get("copyright_type") or "unknown"),
        "uncertainty": "公众号与本人物的关联由用户确认，未做跨平台官方身份认证。",
        "speaker_note": "文章可能含引文、转述或历史观点；不等于本人当前立场。"}, ""


def read_wechat_export(source, identity, account_name, confirmed_same_author=False):
    if not confirmed_same_author:
        raise ValueError("须先明确确认该公众号与当前人物是同一作者；不能仅凭同名自动合并。")
    account_name = str(account_name).strip()
    if not 1 <= len(account_name) <= 100:
        raise ValueError("公众号名称需为1–100字。")
    source = Path(source)
    if not source.is_dir() or source.is_symlink():
        raise ValueError("请选择原始公众号JSON所在的普通目录，不接受符号链接。")
    files = sorted(source.glob("*.json"))
    if not files or len(files) > MAX_FILES:
        raise ValueError("目录需包含1–1000个原始文章JSON；不导入报告或全目录任意文件。")
    records, skipped, seen_ids, seen_content = [], Counter(), {}, set()
    total = 0
    for path in files:
        path.resolve().relative_to(source.resolve())
        if path.is_symlink() or not path.is_file():
            skipped["unsafe_file"] += 1
            continue
        size = path.stat().st_size
        total += size
        if total > MAX_TOTAL_BYTES:
            raise ValueError("本次公众号导出超过40MB上限，未导入。")
        if size > MAX_FILE_BYTES:
            skipped["oversize_file"] += 1
            continue
        try:
            row = json.loads(path.read_text(encoding="utf-8-sig"))
        except (ValueError, UnicodeError):
            skipped["invalid_json"] += 1
            continue
        record, reason = normalize_article(row, identity, account_name)
        if record is None:
            skipped[reason] += 1
            continue
        key, body_hash = record["id"], record["content_sha256"]
        if key in seen_ids and seen_ids[key] != body_hash:
            raise ValueError("导出中同一文章ID存在不同正文，请先核对源文件；未合并。")
        if key in seen_ids or body_hash in seen_content:
            skipped["duplicate_article_or_body"] += 1
            continue
        seen_ids[key] = body_hash
        seen_content.add(body_hash)
        records.append(record)
    return {"records": records, "name": account_name, "complete": False,
        "warning": "本地公众号导出有限覆盖；只纳入抓取成功的完整正文，不代表完整历史。身份关联由用户确认。",
        "skipped": sum(skipped.values()),
        "source_summary": {"platform": "wechat", "account_name": account_name, "files": len(files),
            "accepted": len(records), "chars": sum(len(record["text"]) for record in records),
            "skipped_reasons": dict(skipped), "identity_link": "user_confirmed_same_creator"}}


def import_wechat_articles(store, source, account_name, confirmed_same_author=False):
    result = read_wechat_export(source, store.identity, account_name, confirmed_same_author)
    if not result["records"]:
        raise ValueError("没有通过筛选的公众号完整正文；未修改人物资料。")
    with store.operation("import_wechat"):
        old = store.read()
        result["name"] = old["name"] if old["name"] != "UP主 " + store.identity.uid else account_name
        data = store.save_collection(result)
        data.setdefault("linked_sources", {}).setdefault("wechat", {})[account_name] = result["source_summary"]
        store.write(data)
    return result
