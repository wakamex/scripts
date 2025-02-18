import sys

import torch
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

# Transcription configuration
CHUNK_LENGTH_SECONDS = 25
BATCH_SIZE = 16
MAX_NEW_TOKENS = 256

# Text grouping configuration
MIN_SEGMENT_CHARS = 100
MAX_SEGMENT_CHARS = 550
TOPIC_PAUSE_THRESHOLD = 1.2
SENTENCE_END_THRESHOLD = 0.8

# Model initialization
device = "cuda:0" if torch.cuda.is_available() else "cpu"
torch_dtype = torch.float16 if torch.cuda.is_available() else torch.float32
model_id = "openai/whisper-large-v3"

def initialize_pipeline():
    model = AutoModelForSpeechSeq2Seq.from_pretrained(
        model_id,
        torch_dtype=torch_dtype,
        low_cpu_mem_usage=True,
        use_safetensors=True
    )
    model.to(device)

    processor = AutoProcessor.from_pretrained(model_id)

    return pipeline(
        "automatic-speech-recognition",
        model=model,
        tokenizer=processor.tokenizer,
        feature_extractor=processor.feature_extractor,
        chunk_length_s=CHUNK_LENGTH_SECONDS,
        batch_size=BATCH_SIZE,
        return_timestamps=True,
        torch_dtype=torch_dtype,
        device=device,
    )

def format_timestamp(seconds: float):
    return f"{seconds:.1f}s"

def is_sentence_end(text: str):
    return text.strip().endswith(('.', '?', '!', '."', '?"', '!"'))

def process_segments(chunks):
    segments = []
    current_segment = []
    current_start = None
    current_end = None
    previous_chunk_end = 0.0

    for chunk in chunks:
        if not chunk.get('timestamp') or None in chunk['timestamp']:
            continue

        start_time, end_time = chunk['timestamp']
        text = chunk['text'].strip()

        pause_duration = start_time - previous_chunk_end if current_segment else 0
        previous_chunk_end = end_time

        significant_pause = pause_duration > TOPIC_PAUSE_THRESHOLD
        sentence_end = is_sentence_end(text)
        char_count = len(' '.join(current_segment + [text]))

        if (significant_pause and len(current_segment) > 0) or \
           (sentence_end and char_count >= MIN_SEGMENT_CHARS) or \
           (char_count >= MAX_SEGMENT_CHARS):

            if current_segment:
                segments.append({
                    'start': current_start,
                    'end': current_end,
                    'text': ' '.join(current_segment)
                })
                current_segment = []
                current_start = None

        if not current_segment:
            current_start = start_time
        current_segment.append(text)
        current_end = end_time

    if current_segment:
        segments.append({
            'start': current_start,
            'end': current_end,
            'text': ' '.join(current_segment)
        })

    final_segments = []
    for seg in segments:
        text = seg['text']

        if len(text) < 80 and final_segments:
            final_segments[-1]['text'] += " " + text
            final_segments[-1]['end'] = seg['end']
        else:
            final_segments.append(seg)

    return final_segments

if __name__ == "__main__":
    filename = "transcript.txt" if len(sys.argv) < 3 else sys.argv[2]
    language = "en" if len(sys.argv) < 4 else sys.argv[3]

    if len(sys.argv) > 1:
        pipe = initialize_pipeline()

        result = pipe(
            sys.argv[1],
            generate_kwargs={
                "language": language,
                "max_new_tokens": MAX_NEW_TOKENS,
            },
        )

        print("\nImproved Transcript:")
        processed = process_segments(result["chunks"])

        with open(filename, "w") as f:
            for seg in processed:
                line = f"[{format_timestamp(seg['start'])} -> {format_timestamp(seg['end'])}] {seg['text']}\n"
                print(line, end="")
                f.write(line)
    else:
        print("Usage: python speech_to_text.py <audio_file> [transcript_file] [language]")
