"""Short-lived CPU ASR worker. Local model only; never download implicitly."""

import json
from pathlib import Path
import sys


def main():
    audio, model_path, seconds = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
    if not audio.is_file() or not 1 <= seconds <= 600 or not model_path.is_dir():
        return 2
    from faster_whisper import WhisperModel
    model = WhisperModel(str(model_path), device="cpu", compute_type="int8", cpu_threads=4,
                         local_files_only=True)
    segments, info = model.transcribe(str(audio), beam_size=3, vad_filter=True,
        condition_on_previous_text=False, clip_timestamps=f"0,{seconds}")
    output = []
    for segment in segments:
        if segment.no_speech_prob > 0.7 or segment.avg_logprob < -1.0 or segment.compression_ratio > 2.4:
            continue
        if segment.text.strip() and segment.end <= seconds + 1:
            output.append({"from": segment.start, "to": segment.end, "content": segment.text.strip()})
    print(json.dumps({"language": info.language, "segments": output}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
