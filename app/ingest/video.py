"""Bounded creator-owned Bilibili video text, without paid/cloud ASR services.

Only normal platform responses are used. Cookies stay on api.bilibili.com;
subtitle/audio requests use a separate credential-free session. Nothing runs
on import. The caller holds CreatorStore.operation while saving the result.
"""

from datetime import datetime, timedelta, timezone
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

import requests

from app.ingest.bilibili import CollectionError
from app.ingest.process import run_bounded, ProcessCleanupError
from app.config.bilibili import apply_api_cookies, refusal_message


BV = re.compile(r"BV[0-9A-Za-z]{10}\Z")
API = "https://api.bilibili.com"
MAX_AUDIO_BYTES = 40 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_AUDIO_SECONDS = 1800
MAX_TEXT_CHARS = 60000
MODEL_FILES = ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")
CACHE_SCHEMA = 2  # v1 did not exclude explicitly marked song lyrics.


def is_music_text(text):
    return bool(re.search(r"[♪♫♬]|^\s*[\[【（(]?(?:纯音乐|音乐|配乐|伴奏|music|bgm)[\]】）)]?\s*$", text, re.I))


def _session(auth=False):
    session = requests.Session()
    session.trust_env = False
    if auth:
        try:
            apply_api_cookies(session)
        except ValueError:
            session.close()
            raise
    return session


def _bytes(response, limit, deadline):
    if response.status_code != 200:
        raise CollectionError(refusal_message(response.status_code))
    length = response.headers.get("Content-Length", "")
    if length.isdigit() and int(length) > limit:
        raise CollectionError("平台文件超过本次大小上限，已跳过。")
    result = bytearray()
    for block in response.iter_content(65536):
        if time.monotonic() > deadline:
            raise CollectionError("达到本次请求时间上限，已停止。")
        result.extend(block)
        if len(result) > limit:
            raise CollectionError("平台文件超过本次大小上限，已跳过。")
    return bytes(result)


def _api(session, path, params, identity):
    response = session.get(API + path, params=params,
        headers={"User-Agent": "Mozilla/5.0", "Referer": identity.url},
        timeout=(5, 15), allow_redirects=False, stream=True)
    try:
        payload = json.loads(_bytes(response, MAX_JSON_BYTES, time.monotonic() + 30))
    finally:
        response.close()
    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise CollectionError("B站接口拒绝请求；请核对本地登录态或手动稍后重试，不绕过限制。")
    if not isinstance(payload.get("data"), dict):
        raise CollectionError("B站视频接口结构不兼容，未当作空内容。")
    return payload["data"]


def safe_media_url(url, audio=False):
    if not isinstance(url, str):
        raise CollectionError("字幕或音频地址无效。")
    if url.startswith("//"):
        url = "https:" + url
    parsed = urlsplit(url)
    suffixes = (".bilivideo.com", ".bilivideo.cn") if audio else (".hdslb.com",)
    try:
        valid = (parsed.scheme == "https" and parsed.hostname
                 and any(parsed.hostname.endswith(suffix) for suffix in suffixes)
                 and not parsed.username and not parsed.password and parsed.port in (None, 443)
                 and not parsed.fragment and not any(char.isspace() for char in url))
    except ValueError:
        valid = False
    if not valid:
        raise CollectionError("平台返回了非允许的字幕/音频CDN地址，未访问。")
    return url


def _media(session, url, audio=False):
    # Injected sessions are useful in tests, but must never carry API cookies.
    if getattr(session, "cookies", None) or any(key.lower() in {"authorization", "cookie"}
            for key in getattr(session, "headers", {})) or getattr(session, "auth", None):
        raise CollectionError("媒体下载会话不能携带登录凭据。")
    session.trust_env = False
    response = session.get(safe_media_url(url, audio),
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"},
        timeout=(5, 15), allow_redirects=False, stream=True)
    try:
        return _bytes(response, MAX_AUDIO_BYTES if audio else MAX_JSON_BYTES,
                      time.monotonic() + (120 if audio else 30))
    finally:
        response.close()


def _duration(text):
    if not isinstance(text, str) or not re.fullmatch(r"\d{1,3}:\d{2}(?::\d{2})?", text):
        return 0
    value = 0
    for piece in text.split(":"):
        value = value * 60 + int(piece)
    return value


def discover_videos(identity, session, max_pages=1, min_seconds=0, progress=None):
    """Metadata only; titles are never substituted for speech or portrait text."""
    candidates, seen, offsets = [], set(), set()
    offset, complete, skipped = "", False, 0
    for page in range(max_pages):
        if progress:
            progress(f"读取公开视频列表：第{page + 1}/{max_pages}页…")
        data = _api(session, "/x/polymer/web-dynamic/v1/feed/space",
                    {"host_mid": identity.uid, "offset": offset, "type": "all"}, identity)
        if not isinstance(data.get("items"), list):
            raise CollectionError("B站视频列表格式异常，已停止。")
        for item in data["items"][:100]:
            if not isinstance(item, dict):
                skipped += 1
                continue
            modules = item.get("modules") or {}
            author = modules.get("module_author") or {}
            dynamic = modules.get("module_dynamic") or {}
            archive = (dynamic.get("major") or {}).get("archive") or {}
            bvid = archive.get("bvid", "")
            if (item.get("type") != "DYNAMIC_TYPE_AV" or item.get("orig")
                    or str(author.get("mid")) != identity.uid or dynamic.get("is_charge")
                    or (item.get("basic") or {}).get("is_only_fans")
                    or "UPPOWER" in str((dynamic.get("additional") or {}).get("type", ""))
                    or not isinstance(bvid, str) or not BV.fullmatch(bvid)
                    or (min_seconds and 0 < _duration(archive.get("duration_text")) < min_seconds)):
                skipped += 1
                continue
            if bvid not in seen:
                seen.add(bvid)
                candidates.append({"bvid": bvid, "name": str(author.get("name") or "")[:100]})
        if not data.get("has_more"):
            complete = True
            break
        next_offset = str(data.get("offset") or "")
        if not next_offset or next_offset in offsets:
            break
        offsets.add(next_offset)
        offset = next_offset
    return candidates, complete, skipped


def _view(session, identity, bvid):
    data = _api(session, "/x/web-interface/view", {"bvid": bvid}, identity)
    rights = data.get("rights") or {}
    if (str((data.get("owner") or {}).get("mid")) != identity.uid or data.get("bvid") != bvid
            or data.get("is_chargeable_season") or data.get("is_upower_exclusive")
            or any(rights.get(key) for key in ("pay", "ugc_pay", "ugc_pay_preview", "arc_pay", "is_stein_gate", "is_cooperation"))
            or data.get("state", 0) < 0):
        raise CollectionError("跳过非该作者、付费/试看、合作或交互视频；不把这些内容归给本人。")
    if not all(isinstance(data.get(key), int) and data[key] > 0 for key in ("aid", "cid", "duration", "pubdate")):
        raise CollectionError("视频标识、时长或发布日期不完整，已跳过。")
    return data


def _segments(body, duration, max_seconds=None):
    if not isinstance(body, list):
        raise CollectionError("字幕/转录结构无效。")
    segments, chars = [], 0
    for item in body[:15000]:
        if not isinstance(item, dict):
            continue
        try:
            start, end = float(item["from"]), float(item["to"])
        except (KeyError, ValueError, TypeError):
            continue
        text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", str(item.get("content") or "")).strip()
        if (not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= duration + 3
                or (max_seconds is not None and end > max_seconds + 1) or not text or is_music_text(text)):
            continue
        if chars + len(text) > MAX_TEXT_CHARS:
            break
        chars += len(text)
        segments.append({"from": round(start, 2), "to": round(end, 2), "content": text})
    return sorted(segments, key=lambda item: item["from"])


def local_model_path(store=None):
    explicit = os.getenv("MD_WHISPER_MODEL_PATH", "")
    default = Path(__file__).resolve().parents[2] / ".runtime" / "models" / "faster-whisper-small"
    return Path(explicit).expanduser().resolve() if explicit else default


def transcription_status(model_path=None):
    path = Path(model_path) if model_path is not None else local_model_path()
    if importlib.util.find_spec("faster_whisper") is None:
        return False, "未安装本地转录依赖；运行 setup_video.ps1，本地资料和字幕模式不受影响。"
    if not all((path / name).is_file() for name in MODEL_FILES):
        return False, "本地转录模型未准备；运行 setup_video.ps1 -DownloadModel，或配置 MD_WHISPER_MODEL_PATH。"
    return True, "本地转录已就绪（CPU/int8）；不调用云端语音API，不自动下载模型。"


def transcribe_local(audio, model_path=None, sample_seconds=180, timeout=900):
    path = Path(model_path) if model_path is not None else local_model_path()
    ready, message = transcription_status(path)
    if not ready:
        raise CollectionError(message)
    if not 1 <= sample_seconds <= 600 or not 30 <= timeout <= 1800:
        raise ValueError("转录采样或执行时间超出上限。")
    # The inference worker has no reason to inherit provider/platform secrets,
    # and must not try any network fallback while loading a local checkpoint.
    worker_env = {key: value for key, value in os.environ.items()
                  if not key.upper().startswith(("DEEPSEEK_", "BILI_", "XQ_", "XUEQIU_"))
                  and key.upper() not in {"EMBEDDING_KEY", "ADMIN_PASSWORD", "BUVID3", "DEDEUSERID",
                      "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"}}
    worker_env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    try:
        result = run_bounded([sys.executable, "-m", "app.ingest.whisper_worker", str(audio), str(path), str(sample_seconds)],
            capture_output=True, timeout=timeout, check=False, text=True, encoding="utf-8",
            env=worker_env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), cwd=str(Path(__file__).resolve().parents[2]))
        if result.returncode != 0:
            raise CollectionError("本地语音转录失败；未发送音频到云端，原始错误不展示。")
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        raise CollectionError("本地转录达到时间上限，子进程已停止；可减小采样范围。") from None
    except ProcessCleanupError as exc:
        raise CollectionError(str(exc)) from None
    except (OSError, ValueError):
        raise CollectionError("本地转录输出不可用；请检查依赖和模型。") from None


def _atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False)
        os.replace(str(temporary), str(path))
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _record(store, view, segments, kind, language, sample_seconds):
    bvid, cid = view["bvid"], view["cid"]
    date = datetime.fromtimestamp(view["pubdate"], timezone(timedelta(hours=8))).isoformat()
    text = "\n".join(f"[{item['from']:.1f}–{item['to']:.1f}s] {item['content']}" for item in segments)
    if sum(len(item["content"]) for item in segments) < 20:
        raise CollectionError("没有足够可辨识语音文字；未拿标题、音乐或空字幕生成画像。")
    end = segments[-1]["to"]
    return {"id": f"video_{bvid}_{cid}", "author_uid": store.identity.uid,
        "author_name": str((view.get("owner") or {}).get("name") or "")[:100],
        "url": f"https://www.bilibili.com/video/{bvid}/?p=1", "published_at": date,
        "type": "video", "source_type": kind, "bvid": bvid, "cid": cid,
        "title": str(view.get("title") or "")[:300], "language": language,
        "duration_seconds": view["duration"], "sample_range": f"{segments[0]['from']:.1f}–{end:.1f}秒",
        "sample_seconds": sample_seconds, "sample_complete": kind == "subtitle" and end >= view["duration"] - 5,
        "uncertainty": "平台字幕可能自动识别有误。" if kind == "subtitle" else "本地语音识别未经逐字校对。",
        "speaker_note": "视频归该账号，不代表每句话均由本人说；可能含音乐、嘉宾或转述。",
        "segments": segments, "text": text, "truncated": kind == "transcript" and sample_seconds < view["duration"]}


def collect_video_text(store, max_videos=3, max_pages=1, allow_transcribe=False,
                       min_seconds=0, sample_seconds=180, session=None, media_session=None,
                       progress=None, transcriber=None, model_path=None):
    """At most five distinct videos; first part only. No paid model requests."""
    if not 1 <= max_videos <= 5 or not 1 <= max_pages <= 5 or not 0 <= min_seconds <= 600 or not 15 <= sample_seconds <= 600:
        raise ValueError("视频范围超出上限：最多5条、5页、每条转录15–600秒。")
    api_owned, media_owned = session is None, media_session is None
    session = session if session is not None else _session(auth=True)
    media_session = media_session if media_session is not None else _session()
    records, skipped, name = [], [], "UP主 " + store.identity.uid
    coverage = {"subtitle": 0, "transcript": 0, "cache": 0, "attempted": 0,
                "max_videos": max_videos, "max_pages": max_pages, "first_part_only": True}
    warning, complete = "", False
    try:
        candidates, exhausted, excluded = discover_videos(store.identity, session, max_pages, min_seconds, progress)
        coverage.update(candidates=len(candidates), excluded=excluded)
        for candidate in candidates[:max_videos]:
            bvid = candidate["bvid"]
            coverage["attempted"] += 1
            name = candidate["name"] or name
            if progress:
                progress(f"视频 {coverage['attempted']}/{min(max_videos, len(candidates))}：{bvid}（先字幕，再按授权转录）…")
            try:
                view = _view(session, store.identity, bvid)
                if view["duration"] < min_seconds:
                    raise CollectionError("视频短于所选最小时长，已跳过。")
                cache = store.path / "video" / f"{bvid}_{view['cid']}.json"
                cache.resolve().relative_to(store.path.resolve())
                if cache.is_file() and cache.stat().st_size <= MAX_JSON_BYTES:
                    cached = json.loads(cache.read_text(encoding="utf-8"))
                    record = cached.get("record", {})
                    if (cached.get("schema") == CACHE_SCHEMA and record.get("author_uid") == store.identity.uid
                            and record.get("bvid") == bvid and record.get("cid") == view["cid"]
                            and record.get("id") == f"video_{bvid}_{view['cid']}"
                            and record.get("sample_seconds") == sample_seconds and record.get("text")
                            and record.get("source_type") in {"subtitle", "transcript"}):
                        records.append(record)
                        coverage["cache"] += 1
                        continue
                player = _api(session, "/x/player/v2", {"aid": view["aid"], "cid": view["cid"]}, store.identity)
                subtitles = (player.get("subtitle") or {}).get("subtitles") or []
                subtitles = sorted((item for item in subtitles if item.get("subtitle_url")),
                    key=lambda item: ("zh" not in str(item.get("lan", "")), str(item.get("lan", "")).startswith("ai-")))
                segments, kind, language = [], "subtitle", ""
                if subtitles:
                    chosen = subtitles[0]
                    body = json.loads(_media(media_session, chosen["subtitle_url"]))
                    segments = _segments(body.get("body"), view["duration"])
                    language = str(chosen.get("lan") or "unknown")[:30]
                    if (not segments and any(is_music_text(str(item.get("content") or ""))
                            for item in body.get("body", []) if isinstance(item, dict))):
                        raise CollectionError("字幕仅含音乐/歌词，没有可用口述；不再次转录歌词来冒充本人表达。")
                if not segments or sum(len(item["content"]) for item in segments) < 20:
                    if not allow_transcribe:
                        raise CollectionError("没有可用字幕；本次未授权本地音频转录，已跳过。")
                    if view["duration"] > MAX_AUDIO_SECONDS:
                        raise CollectionError("无字幕且视频超过30分钟；第一版不下载长音频。")
                    if transcriber is None:
                        ready, message = transcription_status(model_path)
                        if not ready:
                            raise CollectionError(message)
                    play = _api(session, "/x/player/playurl",
                        {"bvid": bvid, "cid": view["cid"], "fnval": 16, "qn": 16, "fourk": 0}, store.identity)
                    if play.get("is_preview") or play.get("preview"):
                        raise CollectionError("平台只提供试看音频，已跳过。")
                    audio = (play.get("dash") or {}).get("audio") or []
                    if not audio:
                        raise CollectionError("平台未提供允许的独立音轨；不解析整站或绕过限制。")
                    track = min(audio, key=lambda item: int(item.get("bandwidth") or 10 ** 9))
                    media = _media(media_session, track.get("baseUrl") or track.get("base_url"), audio=True)
                    store.path.mkdir(parents=True, exist_ok=True)
                    audio_path = None
                    try:
                        with tempfile.NamedTemporaryFile(dir=store.path, suffix=".m4a", delete=False) as handle:
                            audio_path = Path(handle.name)
                            handle.write(media)
                        body = (transcriber or transcribe_local)(audio_path, model_path=model_path,
                            sample_seconds=min(sample_seconds, view["duration"]))
                    finally:
                        if audio_path is not None and audio_path.exists():
                            audio_path.unlink()
                    segments = _segments(body.get("segments"), view["duration"], min(sample_seconds, view["duration"]))
                    kind, language = "transcript", str(body.get("language") or "unknown")[:30]
                record = _record(store, view, segments, kind, language, sample_seconds)
                _atomic_json(cache, {"schema": CACHE_SCHEMA, "record": record})
                records.append(record)
                coverage[kind] += 1
            except (CollectionError, requests.RequestException, ValueError, KeyError, TypeError, AttributeError) as exc:
                safe = str(exc) if isinstance(exc, CollectionError) else "视频数据格式或连接异常，未自动重试。"
                skipped.append({"bvid": bvid, "reason": safe})
                # Refusal/rate limiting stops this run; do not probe other routes/videos.
                if "HTTP" in safe or "接口拒绝" in safe:
                    warning = "平台限制导致停止；已获取内容保留。"
                    break
        complete = exhausted and len(candidates) <= max_videos and not skipped and not warning
        warning = warning or ("仅有限采样，不代表完整历史；多分P视频只读取第1P。" if not complete else "多分P视频只读取第1P；不代表作者全部历史。")
    except (requests.RequestException, ValueError, TypeError, AttributeError) as exc:
        if isinstance(exc, CollectionError):
            raise
        raise CollectionError("视频列表连接失败或格式异常，未自动重试。") from None
    finally:
        if api_owned:
            session.close()
        if media_owned:
            media_session.close()
    coverage["skipped"] = skipped
    return {"records": records, "name": name, "complete": complete, "warning": warning,
            "skipped": coverage.get("excluded", 0) + len(skipped), "coverage": coverage}
