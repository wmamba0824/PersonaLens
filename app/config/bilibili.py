"""Local Bilibili login settings; never probe, refresh or bypass platform login."""

import json
import os
from pathlib import Path
import tempfile
import threading

from app.config.settings import PROJECT_ROOT

COOKIE_FILE = PROJECT_ROOT / ".runtime" / "bilibili-settings.json"
COOKIE_ENV = {"SESSDATA": "BILI_SESSDATA", "bili_jct": "BILI_JCT", "buvid3": "BUVID3", "DedeUserID": "DEDEUSERID"}
_LOCK = threading.RLock()


class CookieConfigError(ValueError):
    pass


def _validate(cookies):
    if not isinstance(cookies, dict) or set(cookies) != set(COOKIE_ENV):
        raise CookieConfigError("B站登录配置格式不正确，请在本机重新保存。")
    for name, value in cookies.items():
        limit = 4096 if name == "SESSDATA" else 512
        if (not isinstance(value, str) or len(value) > limit
                or any(ord(char) < 33 or ord(char) > 126 or char in ';"\\' for char in value)):
            raise CookieConfigError("Cookie须为单个原始值，不含空格、换行、分号或整条Cookie字符串；请检查输入。")
    return cookies


def get_cookies():
    """Saved fields are authoritative, including blanks used to disable login."""
    try:
        if COOKIE_FILE.is_symlink():
            raise ValueError()
        if not COOKIE_FILE.exists():
            return _validate({name: os.getenv(env, "") for name, env in COOKIE_ENV.items()})
        if COOKIE_FILE.stat().st_size > 12288:
            raise ValueError()
        data = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema") != 1:
            raise ValueError()
        return _validate(data["cookies"])
    except (OSError, ValueError, TypeError, KeyError):
        raise CookieConfigError("本机B站登录配置无法读取；请打开“B站采集设置”重新填写或停用。未自动回退或请求平台。") from None


def cookie_status():
    cookies = get_cookies()
    return {"configured": {name: bool(value) for name, value in cookies.items()},
            "source": "web" if COOKIE_FILE.exists() else "environment", "verified": False}


def _write(cookies):
    _validate(cookies)
    if COOKIE_FILE.is_symlink() or COOKIE_FILE.parent.is_symlink():
        raise CookieConfigError("B站配置路径不能是符号链接。")
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=COOKIE_FILE.parent,
                                         suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump({"schema": 1, "cookies": cookies}, handle, ensure_ascii=False)
        temporary.chmod(0o600)
        os.replace(str(temporary), str(COOKIE_FILE))
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def save_cookies(values):
    """Blank form fields retain prior values; no environment or API changes."""
    _validate(values)
    with _LOCK:
        try:
            previous = get_cookies()
        except CookieConfigError:
            previous = dict.fromkeys(COOKIE_ENV, "")
        cookies = {name: values[name] or previous[name] for name in COOKIE_ENV}
        if not any(cookies.values()):
            raise CookieConfigError("尚未填写Cookie；原配置未改动。只用TXT/MD无需配置B站。")
        _write(cookies)


def disable_cookies():
    """Store an explicit empty override; never delete or re-enable old .env values."""
    with _LOCK:
        _write(dict.fromkeys(COOKIE_ENV, ""))


def apply_api_cookies(session):
    for name, value in get_cookies().items():
        if value:
            session.cookies.set(name, value, domain="api.bilibili.com", secure=True, path="/")


def redact_cookies(text):
    try:
        cookies = get_cookies()
    except CookieConfigError:
        return text
    for value in cookies.values():
        if len(value) >= 8:
            text = text.replace(value, "[凭据已隐藏]")
    return text


def refusal_message(status):
    if status == 412:
        return ("B站返回HTTP 412：本次采集请求被拒绝，可能涉及平台限制。请检查“B站采集设置”；"
                "填写Cookie不保证解决，不能仅凭412认定登录已过期。已停止，不自动重试或绕过；可导入TXT/MD继续。")
    return f"B站返回HTTP {status}；请检查登录态或平台限制，已停止，不自动重试或绕过。"
