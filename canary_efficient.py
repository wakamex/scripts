#!/usr/bin/env python3
"""Memory-efficient Canary transcription"""

import sys
import os
import torch
import gc

# Set memory optimization flags
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True,max_split_size_mb:512"

def transcribe_efficient(audio_path, output_path="transcription.txt"):
    """Transcribe with minimal memory usage"""
    
    # Clear any existing GPU cache
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
    
    print(f"GPU Memory before load: {torch.cuda.memory_allocated()/1024**3:.2f}GB allocated")
    
    from nemo.collections.speechlm2.models import SALM
    
    print("Loading model with memory optimizations...")
    
    # Load model with float16 precision for lower memory
    model = SALM.from_pretrained(
        'nvidia/canary-qwen-2.5b',
        torch_dtype=torch.float16,  # Use half precision
    )
    
    # Move to GPU and set to eval mode
    model = model.cuda()
    model.eval()  # Important: set to eval mode to disable dropout
    
    print(f"GPU Memory after load: {torch.cuda.memory_allocated()/1024**3:.2f}GB allocated")
    
    # Clear cache before inference
    torch.cuda.empty_cache()
    
    # Prepare prompt with minimal tokens
    prompts = [[{
        "role": "user",
        "content": f"Transcribe: {model.audio_locator_tag}",
        "audio": [audio_path]
    }]]
    
    print(f"\nTranscribing: {audio_path}")
    print(f"GPU Memory before generation: {torch.cuda.memory_allocated()/1024**3:.2f}GB")
    
    try:
        # Generate with memory-efficient settings
        with torch.cuda.amp.autocast():  # Use automatic mixed precision
            with torch.no_grad():
                answer_ids = model.generate(
                    prompts=prompts,
                    max_new_tokens=64,  # Reduced for memory
                    do_sample=False,
                    num_beams=1,  # Greedy decoding uses less memory than beam search
                    use_cache=True,  # KV cache optimization
                )
        
        print(f"GPU Memory after generation: {torch.cuda.memory_allocated()/1024**3:.2f}GB")
        
        # Convert to text
        result = model.tokenizer.ids_to_text(answer_ids[0].cpu())
        
        # Save result
        with open(output_path, "w") as f:
            f.write(f"Audio: {audio_path}\n")
            f.write(f"Transcription:\n{result}\n")
        
        print(f"\n✓ Saved to: {output_path}")
        print(f"\nTranscription: {result}")
        
        # Clean up
        del model
        torch.cuda.empty_cache()
        gc.collect()
        
        return result
        
    except torch.cuda.OutOfMemoryError as e:
        print(f"\nOOM Error: {e}")
        print("\nTrying with CPU fallback...")
        
        # Move model to CPU and try again
        model = model.cpu()
        torch.cuda.empty_cache()
        
        with torch.no_grad():
            answer_ids = model.generate(
                prompts=prompts,
                max_new_tokens=32,
                do_sample=False,
            )
        
        result = model.tokenizer.ids_to_text(answer_ids[0])
        
        with open(output_path, "w") as f:
            f.write(f"Audio: {audio_path}\n")
            f.write(f"Mode: CPU (fallback)\n")
            f.write(f"Transcription:\n{result}\n")
        
        print(f"\n✓ Saved to: {output_path} (CPU mode)")
        print(f"\nTranscription: {result}")
        
        return result

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python canary_efficient.py <audio_file> [output_file]")
        sys.exit(1)
    
    audio_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else "transcription.txt"
    
    try:
        transcribe_efficient(audio_file, output_file)
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()