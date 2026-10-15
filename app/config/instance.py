"""Load and validate one creator-AI instance configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


class InstanceConfigError(RuntimeError):
    """Raised when an instance configuration cannot be used safely."""


REQUIRED_STRING_FIELDS = (
    ("identity", "name"),
    ("identity", "subtitle"),
    ("identity", "page_title"),
    ("identity", "page_icon"),
    ("ui", "chat_placeholder"),
    ("ui", "quotes_label"),
    ("ui", "timeline_label"),
    ("paths", "data_file"),
    ("paths", "transcript_dir"),
    ("paths", "xueqiu_dir"),
    ("paths", "persona_file"),
    ("paths", "embeddings_file"),
    ("paths", "chunk_manifest_file"),
    ("prompt", "system_intro"),
)


def nested_value(config: Dict[str, Any], section: str, key: str, default=None):
    """Return a nested configuration value without exposing traversal details."""
    section_value = config.get(section, {})
    if not isinstance(section_value, dict):
        return default
    return section_value.get(key, default)


def validate_instance_config(config: Dict[str, Any], source: Path) -> None:
    if not isinstance(config, dict):
        raise InstanceConfigError(f"Instance config must be a JSON object: {source}")
    if config.get("schema_version") != 1:
        raise InstanceConfigError(
            f"Unsupported or missing schema_version in instance config: {source}"
        )

    missing = []
    for section, key in REQUIRED_STRING_FIELDS:
        value = nested_value(config, section, key)
        if not isinstance(value, str) or not value.strip():
            missing.append(f"{section}.{key}")
    if missing:
        raise InstanceConfigError(
            f"Missing instance config field(s) in {source}: {', '.join(missing)}"
        )

    rules = nested_value(config, "prompt", "rules")
    if not isinstance(rules, list) or not rules or not all(
        isinstance(rule, str) and rule.strip() for rule in rules
    ):
        raise InstanceConfigError(
            f"prompt.rules must be a non-empty list of strings: {source}"
        )

    for key in ("top_k", "max_context_chars", "max_history"):
        value = nested_value(config, "retrieval", key)
        if not isinstance(value, int) or value <= 0:
            raise InstanceConfigError(
                f"retrieval.{key} must be a positive integer: {source}"
            )


def load_instance_config(path: Path) -> Dict[str, Any]:
    """Read one instance file and fail with a focused configuration error."""
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            config = json.load(handle)
    except FileNotFoundError as exc:
        raise InstanceConfigError(f"Instance config not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise InstanceConfigError(f"Cannot read instance config {path}: {exc}") from exc
    validate_instance_config(config, path)
    return config
