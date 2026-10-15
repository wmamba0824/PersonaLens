"""Single source of truth for application configuration."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from app.config.instance import load_instance_config, nested_value


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", encoding="utf-8-sig")


def _absolute_project_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


INSTANCE_CONFIG_FILE = _absolute_project_path(
    os.getenv("INSTANCE_CONFIG_FILE", "instances/general/config.json")
)
INSTANCE_CONFIG = load_instance_config(INSTANCE_CONFIG_FILE)
UI_MODE = nested_value(INSTANCE_CONFIG, "ui", "mode", "creator")
RETRIEVAL_BACKEND = os.getenv(
    "RETRIEVAL_BACKEND", nested_value(INSTANCE_CONFIG, "retrieval", "backend", "cloud")
)
if RETRIEVAL_BACKEND not in ("local", "cloud", "lexical"):
    raise ValueError("RETRIEVAL_BACKEND must be local, cloud or lexical")
LOCAL_EMBEDDING_MODEL = os.getenv("LOCAL_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
LOCAL_CACHE_DIR = PROJECT_ROOT / ".runtime" / "local-models"


def _project_path(name: str, instance_key: str, default: Path) -> Path:
    """Return an absolute project path while preserving absolute overrides."""
    raw_value = os.getenv(name) or nested_value(
        INSTANCE_CONFIG, "paths", instance_key, str(default)
    )
    path = Path(raw_value) if raw_value else default
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


DATA_FILE = _project_path(
    "DATA_FILE", "data_file", PROJECT_ROOT / "dynamic_txt" / "all_dynamics.txt"
)
TRANSCRIPT_DIR = _project_path(
    "TRANSCRIPT_DIR", "transcript_dir", PROJECT_ROOT / "audio_trans_txt"
)
XUEQIU_DIR = _project_path(
    "XUEQIU_DIR", "xueqiu_dir", PROJECT_ROOT / "xueqiu_luohuitou"
)
PERSONA_FILE = _project_path(
    "PERSONA_FILE", "persona_file", PROJECT_ROOT / "persona.md"
)
EMBEDDINGS_FILE = _project_path(
    "EMBEDDINGS_FILE", "embeddings_file", PROJECT_ROOT / "embeddings.npy"
)
CHUNK_MANIFEST_FILE = _project_path(
    "CHUNK_MANIFEST_FILE",
    "chunk_manifest_file",
    PROJECT_ROOT / "chunk_manifest.jsonl",
)

APP_NAME = nested_value(INSTANCE_CONFIG, "identity", "name")
APP_SUBTITLE = nested_value(INSTANCE_CONFIG, "identity", "subtitle")
PAGE_TITLE = nested_value(INSTANCE_CONFIG, "identity", "page_title")
PAGE_ICON = nested_value(INSTANCE_CONFIG, "identity", "page_icon")
CHAT_PLACEHOLDER = nested_value(INSTANCE_CONFIG, "ui", "chat_placeholder")
QUOTES_LABEL = nested_value(INSTANCE_CONFIG, "ui", "quotes_label")
TIMELINE_LABEL = nested_value(INSTANCE_CONFIG, "ui", "timeline_label")
HOLDING_KEYWORDS = tuple(
    nested_value(INSTANCE_CONFIG, "ui", "holding_keywords", ())
)
FALLBACK_QUOTES = tuple(nested_value(INSTANCE_CONFIG, "ui", "fallback_quotes", ()))
PROMPT_SYSTEM_INTRO = nested_value(INSTANCE_CONFIG, "prompt", "system_intro")
PROMPT_RULES = tuple(nested_value(INSTANCE_CONFIG, "prompt", "rules", ()))
PROMPT_DISCLAIMER = nested_value(INSTANCE_CONFIG, "prompt", "disclaimer", "")


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            "Set it before starting the application."
        )
    return value


def optional_env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def optional_bool_env(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable with conservative defaults."""
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


DEEPSEEK_API_KEY = optional_env("DEEPSEEK_API_KEY")
ADMIN_PASSWORD = optional_env("ADMIN_PASSWORD")
EMBEDDING_KEY = optional_env("EMBEDDING_KEY")

DEEPSEEK_API_URL = os.getenv(
    "DEEPSEEK_API_URL", "https://api.deepseek.com/v1/chat/completions"
)
DEEPSEEK_CHAT_MODEL = os.getenv("DEEPSEEK_CHAT_MODEL", "deepseek-v4-flash")
DEEPSEEK_EXTRACTION_MODEL = os.getenv("DEEPSEEK_EXTRACTION_MODEL", "deepseek-v4-pro")
DEEPSEEK_USE_SYSTEM_PROXY = optional_bool_env("DEEPSEEK_USE_SYSTEM_PROXY", False)

EMBEDDING_API_URL = os.getenv(
    "EMBEDDING_API_URL", "https://api.siliconflow.cn/v1/embeddings"
)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-large-zh-v1.5")
EMBEDDING_USE_SYSTEM_PROXY = optional_bool_env("EMBEDDING_USE_SYSTEM_PROXY", False)

UP_UID = int(os.getenv("BILI_UP_UID", "0"))
XUEQIU_USER_ID = int(os.getenv("XUEQIU_USER_ID", "0"))

MODEL = DEEPSEEK_CHAT_MODEL
TOP_K = int(
    os.getenv("TOP_K", nested_value(INSTANCE_CONFIG, "retrieval", "top_k", 10))
)
MAX_CONTEXT_CHARS = int(
    os.getenv(
        "MAX_CONTEXT_CHARS",
        nested_value(INSTANCE_CONFIG, "retrieval", "max_context_chars", 6000),
    )
)
MAX_HISTORY = int(
    os.getenv(
        "MAX_HISTORY", nested_value(INSTANCE_CONFIG, "retrieval", "max_history", 20)
    )
)
