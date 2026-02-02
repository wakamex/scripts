#!/usr/bin/env python3
"""Minimal Canary transcription with memory monitoring"""

import sys
import os
import psutil
import torch

def get_memory_info():
    """Get current memory usage"""
    process = psutil.Process(os.getpid())
    mem = process.memory_info().rss / 1024 / 1024 / 1024  # GB
    gpu_mem = 0
    if torch.cuda.is_available():
        gpu_mem = torch.cuda.memory_allocated() / 1024 / 1024 / 1024  # GB
    return mem, gpu_mem

def transcribe_minimal(audio_path, output_path="transcription.txt"):
    """Minimal transcription with memory monitoring"""
    
    print(f"Initial memory - RAM: {get_memory_info()[0]:.2f}GB, GPU: {get_memory_info()[1]:.2f}GB")
    
    # Set memory-efficient settings
    os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "max_split_size_mb:512"
    
    from nemo.collections.speechlm2.models import SALM
    
    print("\nLoading model...")
    model = SALM.from_pretrained('nvidia/canary-qwen-2.5b')
    
    print(f"After model load - RAM: {get_memory_info()[0]:.2f}GB, GPU: {get_memory_info()[1]:.2f}GB")
    
    # Move model to GPU if available
    if torch.cuda.is_available():
        model = model.cuda()
        print("Model moved to GPU")
    
    # Prepare prompt
    prompts = [[{
        "role": "user",
        "content": f"Transcribe: {model.audio_locator_tag}",
        "audio": [audio_path]
    }]]
    
    print(f"\nTranscribing: {audio_path}")
    print(f"Before generation - RAM: {get_memory_info()[0]:.2f}GB, GPU: {get_memory_info()[1]:.2f}GB")
    
    # Generate with minimal memory usage
    torch.cuda.empty_cache() if torch.cuda.is_available() else None
    
    with torch.no_grad():
        answer_ids = model.generate(
            prompts=prompts,
            max_new_tokens=128,  # Reduced for memory
            do_sample=False,
        )
    
    print(f"After generation - RAM: {get_memory_info()[0]:.2f}GB, GPU: {get_memory_info()[1]:.2f}GB")
    
    # Convert to text
    result = model.tokenizer.ids_to_text(answer_ids[0].cpu())
    
    # Save result
    with open(output_path, "w") as f:
        f.write(f"Audio: {audio_path}\n")
        f.write(f"Transcription:\n{result}\n")
    
    print(f"\n✓ Saved to: {output_path}")
    print(f"Preview: {result[:200]}..." if len(result) > 200 else f"Result: {result}")
    
    return result

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python canary_simple.py <audio_file> [output_file]")
        sys.exit(1)
    
    audio_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else "transcription.txt"
    
    try:
        transcribe_minimal(audio_file, output_file)
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()