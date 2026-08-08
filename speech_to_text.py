#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["openai", "assemblyai", "python-dotenv"]
# ///

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

API_FILE_SIZE_LIMIT = 25 * 1024 * 1024  # Whisper API request size limit.
UPLOAD_SAFETY_MARGIN = 1024 * 1024
MAX_UPLOAD_FILE_SIZE = API_FILE_SIZE_LIMIT - UPLOAD_SAFETY_MARGIN
CHUNK_AUDIO_BITRATE = "64k"
CHUNK_AUDIO_BITRATE_BPS = 64_000

MIN_SEGMENT_CHARS = 100
MAX_SEGMENT_CHARS = 550
TOPIC_PAUSE_THRESHOLD = 1.2

PARAKEET_PROJECT = Path("/code/transcribe")
LOCAL_WHISPER_PYTHON = Path("/usr/bin/python3")
LOCAL_WHISPER_MODEL = "openai/whisper-large-v3"
LOCAL_WHISPER_CHUNK_SECONDS = 20
LOCAL_WHISPER_BATCH_SIZE = 8
LOCAL_WHISPER_MAX_NEW_TOKENS = 256
GPT_TRANSCRIBE_MODEL = "gpt-transcribe"
GPT_TRANSCRIBE_CHUNK_SECONDS = 600


def format_timestamp(seconds: float):
    return f"{seconds:.1f}s"


def is_sentence_end(text: str):
    return text.strip().endswith((".", "?", "!", '."', '?"', '!"'))


def process_segments(segments):
    grouped = []
    current_texts = []
    current_start = None
    current_end = None
    previous_end = 0.0

    for seg in segments:
        start_time = seg.start
        end_time = seg.end
        text = seg.text.strip()

        pause = start_time - previous_end if current_texts else 0
        previous_end = end_time

        significant_pause = pause > TOPIC_PAUSE_THRESHOLD
        sentence_end = is_sentence_end(text)
        char_count = len(" ".join(current_texts + [text]))

        if (
            (significant_pause and current_texts)
            or (sentence_end and char_count >= MIN_SEGMENT_CHARS)
            or (char_count >= MAX_SEGMENT_CHARS)
        ):
            if current_texts:
                grouped.append(
                    {"start": current_start, "end": current_end, "text": " ".join(current_texts)}
                )
                current_texts = []
                current_start = None

        if not current_texts:
            current_start = start_time
        current_texts.append(text)
        current_end = end_time

    if current_texts:
        grouped.append({"start": current_start, "end": current_end, "text": " ".join(current_texts)})

    final = []
    for seg in grouped:
        if len(seg["text"]) < 80 and final:
            final[-1]["text"] += " " + seg["text"]
            final[-1]["end"] = seg["end"]
        else:
            final.append(seg)

    return final


def get_duration(audio_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", audio_path],
        capture_output=True,
        text=True,
        check=True,
    )
    info = json.loads(result.stdout)
    return float(info["format"]["duration"])


def format_mib(size: int) -> str:
    return f"{size / 1024 / 1024:.1f}MiB"


def chunk_count_for(audio_path: str) -> int:
    duration = get_duration(audio_path)
    max_chunk_duration = MAX_UPLOAD_FILE_SIZE * 8 / CHUNK_AUDIO_BITRATE_BPS
    return max(math.ceil(duration / max_chunk_duration), 1)


def cleanup_chunks(chunks: list[tuple[str, float]]) -> None:
    if chunks:
        shutil.rmtree(os.path.dirname(chunks[0][0]), ignore_errors=True)


def split_audio(audio_path: str, num_chunks: int) -> list[tuple[str, float]]:
    duration = get_duration(audio_path)

    for _ in range(6):
        chunks = create_audio_chunks(audio_path, duration, num_chunks)
        max_chunk_size = max(os.path.getsize(chunk_path) for chunk_path, _ in chunks)

        if max_chunk_size <= MAX_UPLOAD_FILE_SIZE:
            return chunks

        cleanup_chunks(chunks)
        num_chunks = max(
            num_chunks + 1,
            math.ceil(num_chunks * max_chunk_size / MAX_UPLOAD_FILE_SIZE),
        )

    raise RuntimeError(
        f"Could not create chunks under {format_mib(MAX_UPLOAD_FILE_SIZE)} after retries"
    )


def create_audio_chunks(
    audio_path: str,
    duration: float,
    num_chunks: int,
) -> list[tuple[str, float]]:
    chunk_duration = duration / num_chunks
    chunks = []
    tmpdir = tempfile.mkdtemp()

    for i in range(num_chunks):
        start = i * chunk_duration
        chunk_path = os.path.join(tmpdir, f"chunk_{i}.mp3")
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-y",
                "-ss", str(start), "-t", str(chunk_duration),
                "-i", audio_path,
                "-map", "0:a:0",
                "-vn",
                "-ac", "1",
                "-ar", "16000",
                "-b:a", CHUNK_AUDIO_BITRATE,
                chunk_path,
            ],
            check=True,
        )
        chunks.append((chunk_path, start))

    return chunks


def transcribe_whisper(audio_path: str, language: str, timestamps: bool):
    from openai import OpenAI

    client = OpenAI()
    file_size = os.path.getsize(audio_path)

    if file_size <= MAX_UPLOAD_FILE_SIZE:
        print(f"Transcribing: {audio_path}")
        with open(audio_path, "rb") as f:
            result = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language=language,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
        all_segments = result.segments
    else:
        num_chunks = chunk_count_for(audio_path)
        print(
            f"File is {format_mib(file_size)} "
            f"(API limit {format_mib(API_FILE_SIZE_LIMIT)}, "
            f"safe target {format_mib(MAX_UPLOAD_FILE_SIZE)}), "
            f"splitting audio into {num_chunks} chunks..."
        )
        chunks = split_audio(audio_path, num_chunks)
        all_segments = []
        try:
            for i, (chunk_path, offset) in enumerate(chunks):
                chunk_size = os.path.getsize(chunk_path)
                print(f"Transcribing chunk {i+1}/{len(chunks)} ({format_mib(chunk_size)})...")
                with open(chunk_path, "rb") as f:
                    result = client.audio.transcriptions.create(
                        model="whisper-1",
                        file=f,
                        language=language,
                        response_format="verbose_json",
                        timestamp_granularities=["segment"],
                    )
                for seg in result.segments:
                    seg.start += offset
                    seg.end += offset
                    all_segments.append(seg)
        finally:
            cleanup_chunks(chunks)

    processed = process_segments(all_segments)
    lines = []
    for seg in processed:
        if timestamps:
            lines.append(f"[{format_timestamp(seg['start'])} -> {format_timestamp(seg['end'])}] {seg['text']}")
        else:
            lines.append(seg["text"])
    return lines


def request_gpt_transcription(client, audio_path: str, language: str, prompt: str | None):
    with open(audio_path, "rb") as audio_file:
        return client.audio.transcriptions.create(
            model=GPT_TRANSCRIBE_MODEL,
            file=audio_file,
            prompt=prompt,
            extra_body={"languages": [language]},
        )


def transcribe_gpt(audio_path: str, language: str):
    from openai import OpenAI

    client = OpenAI()
    duration = get_duration(audio_path)
    num_chunks = max(
        chunk_count_for(audio_path),
        math.ceil(duration / GPT_TRANSCRIBE_CHUNK_SECONDS),
    )

    if num_chunks == 1:
        print(f"Transcribing with {GPT_TRANSCRIBE_MODEL}: {audio_path}")
        result = request_gpt_transcription(client, audio_path, language, None)
        return [result.text.strip()]

    print(
        f"Transcribing with {GPT_TRANSCRIBE_MODEL} in {num_chunks} "
        f"audio-only chunks of at most {GPT_TRANSCRIBE_CHUNK_SECONDS}s..."
    )
    chunks = split_audio(audio_path, num_chunks)
    lines = []
    prior = ""
    try:
        for index, (chunk_path, _) in enumerate(chunks):
            print(f"Transcribing chunk {index + 1}/{len(chunks)}...")
            prompt = f"The previous chunk ended: {prior[-800:]}" if prior else None
            result = request_gpt_transcription(client, chunk_path, language, prompt)
            text = result.text.strip()
            if text:
                lines.append(text)
                prior = text
    finally:
        cleanup_chunks(chunks)
    return lines


def transcribe_assemblyai(audio_path: str, language: str, timestamps: bool):
    import assemblyai as aai

    api_key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not api_key:
        print("Error: ASSEMBLYAI_API_KEY not set in environment or .env")
        raise SystemExit(1)

    aai.settings.api_key = api_key

    config = aai.TranscriptionConfig(
        speaker_labels=True,
        language_code=language,
    )

    print(f"Transcribing with speaker diarization: {audio_path}")
    transcriber = aai.Transcriber()
    transcript = transcriber.transcribe(audio_path, config=config)

    if transcript.status == aai.TranscriptStatus.error:
        print(f"Error: {transcript.error}")
        raise SystemExit(1)

    lines = []
    for utterance in transcript.utterances:
        prefix = f"Speaker {utterance.speaker}:"
        if timestamps:
            start = format_timestamp(utterance.start / 1000)
            end = format_timestamp(utterance.end / 1000)
            lines.append(f"[{start} -> {end}] {prefix} {utterance.text}")
        else:
            lines.append(f"{prefix} {utterance.text}")
    return lines


def render_segments(segments: list[dict], timestamps: bool) -> list[str]:
    compatible_segments = [
        SimpleNamespace(
            start=float(segment["start"]),
            end=float(segment["end"]),
            text=str(segment["text"]),
        )
        for segment in segments
        if segment.get("end") is not None and str(segment.get("text", "")).strip()
    ]
    processed = process_segments(compatible_segments)
    if timestamps:
        return [
            f"[{format_timestamp(segment['start'])} -> "
            f"{format_timestamp(segment['end'])}] {segment['text']}"
            for segment in processed
        ]
    return [segment["text"] for segment in processed]


def transcribe_parakeet(audio_path: str, timestamps: bool) -> list[str]:
    try:
        from transcribe_validation.models import load_parakeet_long_form
        from transcribe_validation.worker import (
            _extract_window,
            _keep_segment,
            _timestamp_segment,
            normalize_audio,
            plan_chunks,
            release_inference_memory,
        )
    except ImportError as error:
        raise RuntimeError(
            "Parakeet dependencies are unavailable. Run with "
            "uv run --project /code/transcribe --extra gpu python "
            f"{Path(__file__).resolve()} --engine parakeet ..."
        ) from error

    source = Path(audio_path).resolve()
    all_segments = []
    with tempfile.TemporaryDirectory(prefix="parakeet-transcribe-") as temporary:
        workdir = Path(temporary)
        normalized = workdir / "normalized.wav"
        print("Normalizing audio and detecting quiet boundaries...")
        duration, silences = normalize_audio(source, normalized)
        windows = plan_chunks(duration, silences)
        print(f"Loading local Parakeet model for {len(windows)} section(s)...")
        transcriber = load_parakeet_long_form()
        try:
            for index, window in enumerate(windows):
                chunk = normalized
                if len(windows) > 1:
                    chunk = workdir / f"chunk-{index:03d}.wav"
                    _extract_window(normalized, chunk, window)
                print(f"Transcribing section {index + 1}/{len(windows)}...")
                result = transcriber.transcribe(chunk, {"language": "en"})
                retained = [
                    _timestamp_segment(segment, window)
                    for segment in result.get("segments", [])
                    if str(segment.get("text", "")).strip()
                    and _keep_segment(segment, window, final=index == len(windows) - 1)
                ]
                if retained:
                    all_segments.extend(retained)
                elif text := str(result.get("text", "")).strip():
                    all_segments.append(
                        {
                            "start": round(window.logical_start, 3),
                            "end": round(window.logical_end, 3),
                            "text": text,
                        }
                    )
                del result
                release_inference_memory()
        finally:
            transcriber.close()
            release_inference_memory()
    return render_segments(all_segments, timestamps)


def cuda_compute_processes() -> list[dict[str, str]]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,used_memory,process_name",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        raise RuntimeError("Could not inspect CUDA usage with nvidia-smi") from error

    processes = []
    for line in result.stdout.splitlines():
        fields = [field.strip() for field in line.split(",", 2)]
        if len(fields) == 3:
            processes.append(
                {"pid": fields[0], "memory_mib": fields[1], "name": fields[2]}
            )
    return processes


def require_exclusive_cuda() -> None:
    processes = cuda_compute_processes()
    if not processes:
        return
    usage = "; ".join(
        f"PID {process['pid']} {process['name']} ({process['memory_mib']} MiB)"
        for process in processes
    )
    raise RuntimeError(
        "Parakeet long-form transcription requires exclusive GPU access. "
        f"CUDA is already in use by {usage}. Stop the conflicting workload explicitly "
        "or use --engine whisper-large-v3."
    )


def transcribe_local_whisper(
    audio_path: str,
    language: str,
    timestamps: bool,
) -> list[str]:
    os.environ["PYTORCH_ALLOC_CONF"] = "expandable_segments:True"
    try:
        import torch
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline
    except ImportError as error:
        raise RuntimeError(
            "Local Whisper dependencies are unavailable in /usr/bin/python3"
        ) from error

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if torch.cuda.is_available() else torch.float32
    print(f"Loading local {LOCAL_WHISPER_MODEL} on {device}...")
    model = AutoModelForSpeechSeq2Seq.from_pretrained(
        LOCAL_WHISPER_MODEL,
        dtype=dtype,
        low_cpu_mem_usage=True,
        use_safetensors=True,
    )
    model.to(device)
    model.eval()
    processor = AutoProcessor.from_pretrained(LOCAL_WHISPER_MODEL)
    transcriber = pipeline(
        "automatic-speech-recognition",
        model=model,
        tokenizer=processor.tokenizer,
        feature_extractor=processor.feature_extractor,
        chunk_length_s=LOCAL_WHISPER_CHUNK_SECONDS,
        batch_size=LOCAL_WHISPER_BATCH_SIZE,
        return_timestamps=True,
        dtype=dtype,
        device=device,
    )
    try:
        print(f"Transcribing locally: {audio_path}")
        with torch.no_grad():
            result = transcriber(
                audio_path,
                generate_kwargs={
                    "language": language,
                    "max_new_tokens": LOCAL_WHISPER_MAX_NEW_TOKENS,
                },
            )
        segments = [
            {
                "start": float(chunk["timestamp"][0]),
                "end": float(chunk["timestamp"][1]),
                "text": str(chunk["text"]).strip(),
            }
            for chunk in result.get("chunks", [])
            if chunk.get("timestamp") and None not in chunk["timestamp"]
        ]
        return render_segments(segments, timestamps)
    finally:
        del transcriber
        del model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def restart_with_parakeet_runtime() -> None:
    command = [
        "uv",
        "run",
        "--project",
        str(PARAKEET_PROJECT),
        "--extra",
        "gpu",
        "python",
        str(Path(__file__).resolve()),
        *sys.argv[1:],
        "--parakeet-runtime-ready",
    ]
    environment = os.environ.copy()
    environment.pop("LD_LIBRARY_PATH", None)
    environment.pop("VIRTUAL_ENV", None)
    os.execvpe(command[0], command, environment)


def restart_with_local_whisper_runtime() -> None:
    command = [
        str(LOCAL_WHISPER_PYTHON),
        str(Path(__file__).resolve()),
        *sys.argv[1:],
        "--local-whisper-runtime-ready",
    ]
    environment = os.environ.copy()
    environment.pop("VIRTUAL_ENV", None)
    os.execve(command[0], command, environment)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Transcribe audio to text")
    parser.add_argument("audio_file", help="Path to audio file")
    parser.add_argument("-o", "--output", default="transcript.txt", help="Output file (default: transcript.txt)")
    parser.add_argument("-l", "--language", default="en", help="Language code (default: en)")
    parser.add_argument(
        "--engine",
        choices=("whisper", "gpt-transcribe", "whisper-large-v3", "parakeet"),
        default="whisper",
        help=(
            "Engine: remote whisper-1, remote gpt-transcribe, local Whisper Large v3, "
            "or local Parakeet"
        ),
    )
    parser.add_argument("--speakers", action="store_true", help="Enable speaker diarization (uses AssemblyAI)")
    parser.add_argument("--timestamps", action="store_true", help="Include timestamps")
    parser.add_argument("--parakeet-runtime-ready", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--local-whisper-runtime-ready", action="store_true", help=argparse.SUPPRESS)
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.engine != "whisper" and args.speakers:
        parser.error(f"--speakers is not supported with --engine {args.engine}")
    if args.engine == "gpt-transcribe" and args.timestamps:
        parser.error("--timestamps is not supported with --engine gpt-transcribe")
    if args.engine == "parakeet" and args.language != "en":
        parser.error("Parakeet TDT 0.6B v3 supports English only; use --language en")

    if args.engine == "parakeet" and not args.parakeet_runtime_ready:
        try:
            require_exclusive_cuda()
        except RuntimeError as error:
            parser.error(str(error))

    if args.engine == "parakeet" and not args.parakeet_runtime_ready:
        restart_with_parakeet_runtime()
    if args.engine == "whisper-large-v3" and not args.local_whisper_runtime_ready:
        restart_with_local_whisper_runtime()
    if args.engine == "parakeet":
        lines = transcribe_parakeet(args.audio_file, args.timestamps)
    elif args.engine == "gpt-transcribe":
        lines = transcribe_gpt(args.audio_file, args.language)
    elif args.engine == "whisper-large-v3":
        lines = transcribe_local_whisper(args.audio_file, args.language, args.timestamps)
    elif args.speakers:
        lines = transcribe_assemblyai(args.audio_file, args.language, args.timestamps)
    else:
        lines = transcribe_whisper(args.audio_file, args.language, args.timestamps)

    print("\nTranscript:")
    with open(args.output, "w") as f:
        for line in lines:
            print(line)
            f.write(line + "\n")

    print(f"\nSaved to: {args.output}")


if __name__ == "__main__":
    main()
