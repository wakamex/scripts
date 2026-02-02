#!/usr/bin/env python3
"""Optimized Canary transcription with reduced memory usage"""

import sys
import os
import torch
import gc

# Set memory optimization flags
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True,max_split_size_mb:256"

def clear_memory():
    """Clear GPU memory"""
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
        gc.collect()

def transcribe_optimized(audio_path, output_path="transcription.txt"):
    """Transcribe with optimized memory usage"""
    
    clear_memory()
    print(f"Initial GPU Memory: {torch.cuda.memory_allocated()/1024**3:.2f}GB")
    
    from nemo.collections.speechlm2.models import SALM
    
    print("Loading Canary model...")
    
    # Load model normally
    model = SALM.from_pretrained('nvidia/canary-qwen-2.5b')
    
    # Convert model to half precision after loading
    model = model.half()  # Convert to float16
    model = model.cuda()
    model.eval()  # Set to evaluation mode
    
    print(f"Model loaded - GPU Memory: {torch.cuda.memory_allocated()/1024**3:.2f}GB")
    
    # Try to free some memory
    clear_memory()
    
    # Prepare minimal prompt
    prompts = [[{
        "role": "user",
        "content": f"Transcribe: {model.audio_locator_tag}",
        "audio": [audio_path]
    }]]
    
    print(f"\nProcessing: {audio_path}")
    
    try:
        # Use gradient checkpointing if available to save memory
        if hasattr(model, 'gradient_checkpointing_enable'):
            model.gradient_checkpointing_enable()
        
        # Generate with minimal memory usage
        with torch.cuda.amp.autocast(dtype=torch.float16):  # Force float16
            with torch.no_grad():
                # Try with very conservative settings first
                answer_ids = model.generate(
                    prompts=prompts,
                    max_new_tokens=32,  # Very short for testing
                    do_sample=False,
                    num_beams=1,  # No beam search
                    early_stopping=True,
                    use_cache=False,  # Disable KV cache to save memory
                )
        
        print(f"Generation complete - GPU Memory: {torch.cuda.memory_allocated()/1024**3:.2f}GB")
        
        # Convert to text
        result = model.tokenizer.ids_to_text(answer_ids[0].cpu())
        
        # Save result
        with open(output_path, "w") as f:
            f.write(f"Audio: {audio_path}\n")
            f.write(f"Settings: max_tokens=32, float16, no_cache\n")
            f.write(f"Transcription:\n{result}\n")
        
        print(f"\n✓ Saved to: {output_path}")
        print(f"\nTranscription: {result}")
        
        # Clean up
        del model
        clear_memory()
        
        return result
        
    except torch.cuda.OutOfMemoryError as e:
        print(f"\nStill OOM with optimizations: {e}")
        print("\nSuggestions:")
        print("1. Close other GPU applications")
        print("2. Use a shorter audio clip (< 10 seconds)")
        print("3. Try with: CUDA_VISIBLE_DEVICES=0 python canary_optimized.py <audio>")
        print("4. Consider using a smaller model like nvidia/canary-1b")
        return None

def check_audio_duration(audio_path):
    """Check audio file duration"""
    try:
        import librosa
        duration = librosa.get_duration(path=audio_path)
        sr = librosa.get_samplerate(audio_path)
        print(f"Audio info: {sr}Hz, {duration:.1f} seconds")
        if duration > 30:
            print("⚠️  Warning: Audio longer than 30s may cause OOM")
        return duration
    except:
        print("Could not check audio duration")
        return None

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python canary_optimized.py <audio_file> [output_file]")
        print("\nNote: For best results, use audio files < 30 seconds")
        sys.exit(1)
    
    audio_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else "transcription.txt"
    
    # Check audio duration
    check_audio_duration(audio_file)
    
    # Check available GPU memory
    if torch.cuda.is_available():
        total_mem = torch.cuda.get_device_properties(0).total_memory / 1024**3
        free_mem = (total_mem * 1024**3 - torch.cuda.memory_allocated()) / 1024**3
        print(f"GPU: {torch.cuda.get_device_name(0)}")
        print(f"Total Memory: {total_mem:.1f}GB, Free: {free_mem:.1f}GB")
    
    try:
        transcribe_optimized(audio_file, output_file)
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()