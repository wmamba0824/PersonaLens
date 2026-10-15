"""Explicit opt-in model preparation, separate from web startup and paid APIs."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.ingest.process import run_bounded, ProcessCleanupError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--model", choices=("small", "base", "tiny"), default="small")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", encoding="utf-8-sig", override=False)
    from app.ingest.video import MODEL_FILES, local_model_path, transcription_status
    target = local_model_path() if args.model == "small" else ROOT / ".runtime/models" / ("faster-whisper-" + args.model)
    ready, message = transcription_status(target)
    print(message)
    if ready or not args.download:
        return 0
    if not args.worker:
        try:
            result = run_bounded([sys.executable, str(Path(__file__).resolve()), "--download", "--model", args.model, "--worker"],
                timeout=600, check=False)
            return result.returncode
        except subprocess.TimeoutExpired:
            print("Model download stopped after 10 minutes; partial files retained. Run explicitly again to resume.", file=sys.stderr)
            return 1
    from huggingface_hub import HfApi, snapshot_download
    repo = "Systran/faster-whisper-" + args.model
    api = HfApi(endpoint="https://huggingface.co", token=False)
    # Pin the returned commit before fetching weights. Never execute repository code.
    info = api.model_info(repo, files_metadata=True, token=False, timeout=15)
    sizes = {item.rfilename: item.size or 0 for item in info.siblings}
    if not all(name in sizes for name in MODEL_FILES) or sum(sizes[name] for name in MODEL_FILES) > 1024 ** 3:
        raise ValueError("Unexpected model files or size; download refused")
    print(json.dumps({"repo": repo, "revision": info.sha, "bytes": sum(sizes[name] for name in MODEL_FILES)}, ensure_ascii=True), flush=True)
    snapshot_download(repo, revision=info.sha, allow_patterns=list(MODEL_FILES),
                      local_dir=str(target), token=False, endpoint="https://huggingface.co", max_workers=2)
    print("model_path=" + str(target))
    if args.model != "small":
        print("Set MD_WHISPER_MODEL_PATH to this directory before starting the web app; small remains the default.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProcessCleanupError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        # Provider errors can contain signed URLs/tokens; never print their bodies.
        print("Local model preparation failed: " + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
