"""Local readiness checks for the PersonaLens Streamlit application."""

from __future__ import annotations

import os
import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv


def report(level: str, message: str) -> None:
    print(f"[{level}] {message}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-unconfigured", action="store_true", help="Allow first-use web configuration")
    args = parser.parse_args()
    load_dotenv(PROJECT_ROOT / ".env", encoding="utf-8-sig")
    errors = 0

    report("INFO", f"Python {sys.version.split()[0]}")
    if sys.version_info < (3, 8):
        report("ERROR", "Python 3.8 or newer is required")
        errors += 1

    try:
        from app.config.settings import (
            APP_NAME,
            CHUNK_MANIFEST_FILE,
            DATA_FILE,
            EMBEDDING_MODEL,
            EMBEDDINGS_FILE,
            INSTANCE_CONFIG_FILE,
            PERSONA_FILE,
            RETRIEVAL_BACKEND,
            LOCAL_EMBEDDING_MODEL,
        )

        report("OK", f"Instance config loaded: {APP_NAME} ({INSTANCE_CONFIG_FILE.name})")
    except Exception as exc:
        report("ERROR", f"Instance config failed: {exc}")
        return 1

    required_files = (
        PROJECT_ROOT / "chat_app.py",
        INSTANCE_CONFIG_FILE,
        PERSONA_FILE,
        DATA_FILE,
    )
    for path in required_files:
        if path.exists():
            try:
                display_path = path.relative_to(PROJECT_ROOT)
            except ValueError:
                display_path = path
            report("OK", f"Found {display_path}")
        else:
            report("ERROR", f"Missing {path}")
            errors += 1

    from app.config.runtime import get_config, ConfigError
    try:
        configured = bool(get_config()["api_key"])
    except ConfigError:
        configured = False
    if configured:
        report("OK", "Chat API is configured (key not displayed)")
    elif args.allow_unconfigured:
        report("WARN", "Configure API in the local web setup page; startup is allowed")
    else:
        report("ERROR", "Chat API is not configured. Use start_local.bat and complete web setup.")
        errors += 1

    if RETRIEVAL_BACKEND == "local":
        report("INFO", f"Local retrieval: {LOCAL_EMBEDDING_MODEL}; no embedding API key needed")
    elif RETRIEVAL_BACKEND == "cloud" and os.getenv("EMBEDDING_KEY"):
        report("OK", "Environment variable EMBEDDING_KEY is configured")
    elif RETRIEVAL_BACKEND == "cloud":
        report("WARN", "EMBEDDING_KEY is missing; lexical retrieval will be used")

    try:
        import numpy as np

        from app.rag.loader import read_entries

        entries = read_entries()
        report("OK", f"Knowledge base contains {len(entries)} entries")
        if not entries:
            report("WARN", "No knowledge yet. Put TXT or Markdown files in knowledge/ and reload the page.")
        if RETRIEVAL_BACKEND != "cloud":
            if RETRIEVAL_BACKEND == "local":
                report("INFO", "Index is built automatically on first use; first model download requires network")
            else:
                report("INFO", "Keyword retrieval explicitly selected")
            report("OK" if not errors else "ERROR", "Readiness checks complete (no paid API request made)")
            return 1 if errors else 0
        manifest_valid = False
        if CHUNK_MANIFEST_FILE.exists():
            from app.rag.manifest import load_manifest, validate_manifest

            manifest = load_manifest(CHUNK_MANIFEST_FILE)
            manifest_issues = validate_manifest(manifest, entries, EMBEDDING_MODEL)
            if manifest_issues:
                report("WARN", f"Chunk manifest is stale: {manifest_issues[0]}")
            else:
                manifest_valid = True
                report("OK", f"Chunk manifest is aligned ({len(manifest)} rows)")
        else:
            report("WARN", "Chunk manifest is missing; dense retrieval stays disabled")

        if EMBEDDINGS_FILE.exists():
            from app.rag.index_metadata import index_metadata_issues

            matrix = np.load(EMBEDDINGS_FILE, mmap_mode="r")
            if len(matrix) == len(entries) and manifest_valid:
                index_issues = index_metadata_issues(
                    EMBEDDINGS_FILE, matrix, manifest, EMBEDDING_MODEL
                )
                if index_issues:
                    report(
                        "WARN",
                        f"Embedding index is not verified: {index_issues[0]}; "
                        "lexical retrieval will be used",
                    )
                else:
                    report("OK", f"Embedding index is aligned ({len(matrix)} rows)")
            elif len(matrix) == len(entries):
                report(
                    "WARN",
                    "Embedding rows match knowledge entries, but the chunk manifest is not valid",
                )
            else:
                report(
                    "WARN",
                    "Embedding index is stale: "
                    f"{len(matrix)} rows for {len(entries)} knowledge entries; "
                    "lexical retrieval will be used",
                )
        else:
            report("WARN", "Embedding index is missing; lexical retrieval will be used")
    except Exception as exc:
        report("ERROR", f"Knowledge-base check failed: {exc}")
        errors += 1

    if errors:
        report("ERROR", f"Readiness check failed with {errors} blocking issue(s)")
        return 1
    report("OK", "Local web application is ready to start")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
