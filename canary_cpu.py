#!/usr/bin/env python3
"""Run Canary model on CPU to avoid CUDA/cuDNN issues"""

import sys
import os
import torch

# Force CPU usage
os.environ["CUDA_VISIBLE_DEVICES"] = ""
torch.cuda.is_available = lambda: False

def transcribe_cpu(audio_path, output_path="transcription.txt"):
    """Transcribe using CPU only"""
    
    print("Running on CPU (avoiding CUDA/cuDNN issues)")
    print(f"PyTorch: {torch.__version__}")
    print(f"CUDA available: {torch.cuda.is_available()}")
    
    from nemo.collections.speechlm2.models import SALM
    
    print("\nLoading model on CPU...")
    
    # Load model with CPU settings
    model = SALM.from_pretrained(
        'nvidia/canary-qwen-2.5b',
        map_location='cpu',
        torch_dtype=torch.float32  # Use float32 on CPU
    )
    print("✓ Model loaded on CPU")
    
    # Prepare prompt
    prompts = [[{
        "role": "user",
        "content": f"Transcribe: {model.audio_locator_tag}",
        "audio": [audio_path]
    }]]
    
    print(f"\nTranscribing: {audio_path}")
    print("Note: CPU inference will be slower than GPU")
    
    # Generate
    with torch.no_grad():
        answer_ids = model.generate(
            prompts=prompts,
            max_new_tokens=128,
            do_sample=False,
        )
    
    # Convert to text
    result = model.tokenizer.ids_to_text(answer_ids[0])
    
    # Save result
    with open(output_path, "w") as f:
        f.write(f"Audio: {audio_path}\n")
        f.write(f"Mode: CPU\n")
        f.write(f"Transcription:\n{result}\n")
    
    print(f"\n✓ Saved to: {output_path}")
    print(f"\nTranscription: {result}")
    
    return result

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python canary_cpu.py <audio_file> [output_file]")
        sys.exit(1)
    
    audio_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else "transcription.txt"
    
    try:
        transcribe_cpu(audio_file, output_file)
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()