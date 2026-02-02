#!/usr/bin/env python3
"""Test Canary-Qwen-2.5B model for speech recognition"""

import sys
from nemo.collections.speechlm2.models import SALM

def transcribe_audio(audio_path):
    """Transcribe audio using Canary-Qwen-2.5B model"""
    
    print("Loading nvidia/canary-qwen-2.5b model...")
    print("Note: First run will download ~2.5GB model")
    
    try:
        # Load the model
        model = SALM.from_pretrained('nvidia/canary-qwen-2.5b')
        print("✓ Model loaded successfully")
        
        # Prepare prompt for transcription
        prompts = [
            [{
                "role": "user", 
                "content": f"Transcribe the following: {model.audio_locator_tag}",
                "audio": [audio_path]
            }]
        ]
        
        print(f"\nTranscribing: {audio_path}")
        
        # Generate transcription with memory-efficient settings
        import torch
        with torch.no_grad():
            answer_ids = model.generate(
                prompts=prompts,
                max_new_tokens=256,
                do_sample=False,  # Deterministic output
                temperature=0.1,  # Low temperature for consistency
            )
        
        # Convert token IDs to text
        transcription = model.tokenizer.ids_to_text(answer_ids[0].cpu())
        
        return transcription
        
    except Exception as e:
        print(f"Error: {e}")
        return None

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_canary.py <audio_file> [output_file]")
        print("Note: Audio should be 16kHz mono, max 40 seconds")
        sys.exit(1)
    
    audio_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else "transcription.txt"
    
    result = transcribe_audio(audio_file)
    
    if result:
        print("\n=== Transcription ===")
        print(result)
        print("=" * 20)
        
        # Save to file
        with open(output_file, "w") as f:
            f.write(result)
        print(f"\nTranscription saved to: {output_file}")