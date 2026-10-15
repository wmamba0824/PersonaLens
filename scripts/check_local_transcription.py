"""Opt-in local-file ASR check. No collection, cloud API or model download."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--audio", type=Path, help="Your own local audio, at most 40 MB")
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--expect", nargs="*", default=[], help="Optional words expected in the transcript")
    args = parser.parse_args(argv)
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", encoding="utf-8-sig", override=False)
    from app.ingest.video import transcription_status, transcribe_local, MAX_AUDIO_BYTES
    ready, message = transcription_status()
    print(message.encode("ascii", errors="backslashreplace").decode(), flush=True)
    if not args.execute:
        print("Dry run. --execute --audio YOUR_FILE transcribes locally; no model downloads or paid API calls.")
        return 0
    if not 15 <= args.seconds <= 600 or args.audio is None:
        parser.error("--execute requires --audio and --seconds between 15 and 600")
    audio = args.audio.resolve()
    if not audio.is_file() or not 0 < audio.stat().st_size <= MAX_AUDIO_BYTES:
        parser.error("Audio must exist and be between 1 byte and 40 MB")
    if not ready:
        parser.error("Prepare the local model explicitly before running this check")
    input_hash = hashlib.sha256(audio.read_bytes()).hexdigest()
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "audio_file": audio.name,
              "max_seconds": args.seconds, "cloud_api_calls": 0, "model_downloads": 0,
              "kind": "local_audio_smoke_check_not_creator_quality_evaluation"}
    started = time.monotonic()
    try:
        body = transcribe_local(audio, sample_seconds=args.seconds, timeout=300)
        text = "".join(str(segment.get("content") or "") for segment in body.get("segments", []))
        report.update(language=body.get("language"), text=text, segments=body.get("segments", []),
                      expected_words={word: word in text for word in args.expect})
        report["passed"] = bool(text.strip()) and all(report["expected_words"].values())
    except Exception as exc:
        report.update(passed=False, error_type=type(exc).__name__)
    report.update(seconds=round(time.monotonic() - started, 2),
                  input_unchanged=hashlib.sha256(audio.read_bytes()).hexdigest() == input_hash)
    directory = ROOT / ".runtime"
    directory.mkdir(exist_ok=True)
    path = directory / ("asr_check_" + uuid4().hex[:10] + ".json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in {"text", "segments"}}, ensure_ascii=True), flush=True)
    print("report=" + path.relative_to(ROOT).as_posix(), flush=True)
    return 0 if report["passed"] and report["input_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
