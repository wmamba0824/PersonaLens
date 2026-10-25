"""Local, atomic API settings. Never send stored credentials back to the UI."""

import json
import os
from pathlib import Path
import tempfile
import threading
from urllib.parse import urlsplit

from app.config import settings

CONFIG_FILE = settings.PROJECT_ROOT / ".runtime" / "api-settings.json"
_LOCK = threading.RLock()


class ConfigError(ValueError):
    pass


def environment_config():
    return {"api_key": os.getenv("DEEPSEEK_API_KEY", ""),
            "url": os.getenv("DEEPSEEK_API_URL", settings.DEEPSEEK_API_URL),
            "model": os.getenv("DEEPSEEK_CHAT_MODEL", settings.MODEL),
            "use_system_proxy": os.getenv("DEEPSEEK_USE_SYSTEM_PROXY", "false").lower() in {"1", "true", "yes", "on"}}


def validate_config(config):
    if not isinstance(config, dict):
        raise ConfigError("API配置格式不正确，请在本机重新保存。")
    for name, limit in (("api_key", 512), ("url", 500), ("model", 150)):
        value = config.get(name)
        if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(char) < 32 for char in value):
            raise ConfigError("请填写有效的 API Key、接口地址和模型名，不要包含换行。")
    try:
        url = urlsplit(config["url"])
        valid = (url.scheme == "https" and url.hostname and not url.username and not url.password
                 and not url.query and not url.fragment and not any(char.isspace() for char in config["url"]))
        url.port  # Validate malformed ports without echoing the submitted URL.
    except ValueError:
        valid = False
    if not valid:
        raise ConfigError("接口须为完整 HTTPS 聊天地址，不含账号、密码、查询参数或片段。")
    if not isinstance(config.get("use_system_proxy"), bool):
        raise ConfigError("代理配置格式不正确。")
    return config


def get_config():
    config = environment_config()
    if CONFIG_FILE.is_symlink():
        raise ConfigError("API配置不能是符号链接。")
    if CONFIG_FILE.exists():
        try:
            if CONFIG_FILE.stat().st_size > 4096:
                raise ValueError()
            saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if not isinstance(saved, dict) or saved.get("schema") != 1:
                raise ValueError()
            config.update({name: saved[name] for name in config})
            validate_config(config)
        except (OSError, ValueError, KeyError, TypeError):
            raise ConfigError("本机API配置无法读取，请在本机网页重新填写并保存。") from None
    return config


def save_config(api_key, url, model, use_system_proxy=False):
    """An empty key preserves the current key; no API request or .env mutation."""
    with _LOCK:
        try:
            previous = get_config()
        except ConfigError:
            previous = environment_config()
        config = {"api_key": api_key.strip() or previous["api_key"], "url": url.strip(),
                  "model": model.strip(), "use_system_proxy": bool(use_system_proxy)}
        validate_config(config)
        if CONFIG_FILE.is_symlink() or CONFIG_FILE.parent.is_symlink():
            raise ConfigError("API配置路径不能是符号链接。")
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=CONFIG_FILE.parent,
                                             suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump({"schema": 1, **config}, handle, ensure_ascii=False)
            temporary.chmod(0o600)
            os.replace(str(temporary), str(CONFIG_FILE))
        finally:
            if temporary and temporary.exists():
                temporary.unlink()


def redact(text):
    try:
        key = get_config()["api_key"]
    except ConfigError:
        key = ""
    return text.replace(key, "[凭据已隐藏]") if len(key) >= 8 else text
