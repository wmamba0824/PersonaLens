"""Read-only source audit. Never prints credential values or changes Git history."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
IGNORED_DIRECTORIES = {".git", ".venv", "venv", ".runtime", "__pycache__", ".pytest_cache", "node_modules"}
PRIVATE_DIRECTORIES = {"audio_trans_txt", "dynamic_txt", "xueqiu_luohuitou", "audio", "video", "videos"}
EXAMPLE_ENVS = {".env.example", ".env.tools.example"}
SECRET_PATTERNS = {
    "secret-looking key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b|\bAIza[0-9A-Za-z_-]{30,}\b|\bAKIA[0-9A-Z]{16}\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def source_paths(root):
    """Use the exact Git candidate set when initialized, else a pruned draft scan."""
    if (root / ".git").exists():
        result = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                                cwd=str(root), capture_output=True, check=True)
        return sorted(set(result.stdout.decode("utf-8").split("\0")) - {""})
    names = []
    for current, directories, files in os.walk(str(root), followlinks=False):
        for directory in list(directories):
            path = Path(current) / directory
            if path.is_symlink():
                names.append(path.relative_to(root).as_posix())
                directories.remove(directory)
            elif directory in IGNORED_DIRECTORIES:
                directories.remove(directory)
        for filename in files:
            if filename not in EXAMPLE_ENVS and (filename == ".env" or filename.endswith(".env")):
                continue  # Local configuration is never a draft release candidate.
            names.append((Path(current) / filename).relative_to(root).as_posix())
    return sorted(names)


def audit(root=ROOT):
    root = Path(root).resolve()
    names = source_paths(root)
    findings, total_bytes = [], 0
    for name in names:
        path, parts = root / name, Path(name).parts
        if path.is_symlink():
            findings.append({"file": name, "reason": "symlink refused"})
            continue
        path.resolve().relative_to(root)
        if not path.is_file():
            continue
        private_path = (any(part in IGNORED_DIRECTORIES | PRIVATE_DIRECTORIES for part in parts)
                        or (name not in EXAMPLE_ENVS and (path.name == ".env" or path.name.endswith(".env")))
                        or path.name == "secrets.toml" or name == "persona.md"
                        or (parts[0] == "knowledge" and path.suffix.lower() == ".txt")
                        or path.suffix.lower() in {".npy", ".npz", ".bin", ".pt", ".pkl"})
        if private_path:
            findings.append({"file": name, "reason": "private data or generated artifact is a release candidate"})
            continue
        total_bytes += path.stat().st_size
        if path.stat().st_size > 1024 * 1024:
            findings.append({"file": name, "reason": "unexpected large file"})
            continue
        try:
            content = path.read_text(encoding="utf-8-sig")
        except (UnicodeError, OSError):
            findings.append({"file": name, "reason": "non-text or unreadable source"})
            continue
        for line_number, line in enumerate(content.splitlines(), 1):
            for reason, pattern in SECRET_PATTERNS.items():
                if pattern.search(line):
                    findings.append({"file": name, "line": line_number, "reason": reason})
        if name in EXAMPLE_ENVS:
            from dotenv import dotenv_values
            credentials = re.compile(r"API_KEY|EMBEDDING_KEY|PASSWORD|SESSDATA|BILI_JCT|BUVID3|DEDEUSERID|TOKEN", re.I)
            populated = [key for key, value in dotenv_values(path, interpolate=False).items() if credentials.search(key) and value]
            if populated:
                findings.append({"file": name, "reason": "example contains nonempty credential", "fields": sorted(populated)})

    for name in names:
        path = root / name
        if path.suffix.lower() != ".md" or not path.is_file() or path.is_symlink():
            continue
        for link in re.findall(r"\]\(([^)\s]+)\)", path.read_text(encoding="utf-8-sig")):
            if "://" in link or link.startswith("#"):
                continue
            if not (path.parent / link.split("#", 1)[0]).is_file():
                findings.append({"file": name, "reason": "missing local link", "target": link})
    license_present = (root / "LICENSE").is_file()
    return {"source_files": len(names), "source_bytes": total_bytes, "findings": findings,
            "license_present": license_present, "ready_for_publication": not findings and license_present,
            "note": "Pattern audit, not proof of content rights, complete secret detection or historical key revocation"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-license", action="store_true", help="Fail publication readiness until a license is chosen")
    args = parser.parse_args()
    report = audit()
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 1 if report["findings"] or (args.require_license and not report["license_present"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
