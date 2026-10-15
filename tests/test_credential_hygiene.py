"""Offline guards against putting credentials back into application source."""

import ast
from pathlib import Path
import re

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
CREDENTIAL_NAMES = {"DEEPSEEK_API_KEY", "EMBEDDING_KEY", "ADMIN_PASSWORD"}
SECRET_PATTERN = re.compile(
    r"\bsk-[A-Za-z0-9_-]{20,}\b|\bAIza[0-9A-Za-z_-]{30,}\b|\bAKIA[0-9A-Z]{16}\b"
)


def test_application_source_has_no_secret_looking_literals():
    files = set(ROOT.glob("*.py")) | set(ROOT.glob("*.ps1"))
    for directory in ("app", "scripts", "instances", "site/dist"):
        files.update(
            path for path in (ROOT / directory).rglob("*")
            if path.is_file() and path.suffix in {".py", ".mjs", ".js", ".json", ".toml"}
        )
    findings = [
        path.relative_to(ROOT).as_posix() for path in sorted(files)
        if SECRET_PATTERN.search(path.read_text(encoding="utf-8-sig"))
    ]
    # Report paths only: never put credential values in assertion output.
    assert not findings, "Secret-looking literals found in: " + ", ".join(findings)


def test_chat_credentials_are_environment_only_without_embedded_defaults():
    tree = ast.parse((ROOT / "app/config/settings.py").read_text(encoding="utf-8-sig"))
    checked = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name) or target.id not in CREDENTIAL_NAMES:
                continue
            assert isinstance(node.value, ast.Call), target.id
            assert isinstance(node.value.func, ast.Name), target.id
            assert node.value.func.id == "optional_env", target.id
            assert len(node.value.args) == 1 and not node.value.keywords, target.id
            argument = node.value.args[0]
            assert isinstance(argument, ast.Constant) and argument.value == target.id, target.id
            checked.add(target.id)
    assert checked == CREDENTIAL_NAMES


def test_example_env_files_contain_only_blank_credentials():
    names = CREDENTIAL_NAMES | {"BILI_SESSDATA", "BILI_JCT", "BUVID3", "DEDEUSERID",
                                "XQ_TOKEN", "XQ_R_TOKEN", "XQ_ID_TOKEN"}
    for filename in (".env.example", ".env.tools.example"):
        values = dotenv_values(ROOT / filename, interpolate=False)
        populated = sorted(name for name in names if values.get(name))
        assert not populated, filename + " has populated credentials: " + ", ".join(populated)
