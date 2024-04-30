# %%
import os
import sys

import torch
from dotenv import load_dotenv
from huggingface_hub import HfApi
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

load_dotenv("keys.env")
OPENAI_KEY = os.getenv("OPENAI_KEY")
MISTRAL_KEY = os.getenv("MISTRAL_KEY")
HUGGINGFACE_KEY = os.getenv("HUGGINGFACE_KEY")
hf_api = HfApi(endpoint="https://huggingface.co")

# %%
# load model
device = "cuda:0" if torch.cuda.is_available() else "cpu"
torch_dtype = torch.float16 if torch.cuda.is_available() else torch.float32
model_id = "openai/whisper-large-v3"
model = AutoModelForSpeechSeq2Seq.from_pretrained(
    model_id, torch_dtype=torch_dtype, low_cpu_mem_usage=True, use_safetensors=True
)
model.to(device)
processor = AutoProcessor.from_pretrained(model_id)
pipe = pipeline(
    "automatic-speech-recognition",
    model=model,
    tokenizer=processor.tokenizer,
    feature_extractor=processor.feature_extractor,
    max_new_tokens=128,
    chunk_length_s=30,
    batch_size=16,
    return_timestamps=True,
    torch_dtype=torch_dtype,
    device=device,
)

# %%
if __name__ == "__main__":
    # use file name if provided
    filename = "transcript.txt" if len(sys.argv) < 3 else sys.argv[2]
    # transcribe the first argument to a file
    if len(sys.argv) > 1:
        result = pipe(sys.argv[1])
        print(result["text"])
        # write to transcript.txt
        with open("transcript.txt", "w") as f:
            f.write(result["text"])
    else:
        print("Usage: python speech_to_text.py <audio_file> [transcript_file]")
