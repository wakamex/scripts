#!/usr/bin/env python3
"""Transcribe long audio using NVIDIA Parakeet-TDT-0.6B - optimized for memory"""

import sys
import torch
import nemo.collections.asr as nemo_asr

def transcribe_with_parakeet(audio_path, output_path="transcription.txt"):
    """Transcribe audio using Parakeet-TDT-0.6B"""
    
    print("Loading NVIDIA Parakeet-TDT-0.6B-v2...")
    print("This model is optimized for long audio (up to 3 hours)")
    
    # Load the model
    model = nemo_asr.models.ASRModel.from_pretrained(
        model_name="nvidia/parakeet-tdt-0.6b-v2"
    )
    
    # Move to GPU if available
    if torch.cuda.is_available():
        model = model.cuda()
        print(f"✓ Model loaded on GPU: {torch.cuda.get_device_name(0)}")
        print(f"GPU Memory: {torch.cuda.memory_allocated()/1024**3:.2f}GB")
    else:
        print("✓ Model loaded on CPU")
    
    # For long audio with limited VRAM, enable local attention
    print("\nConfiguring for long audio transcription...")
    try:
        # Enable local attention for efficiency with long audio
        model.change_attention_model("rel_pos_local_attn", [128, 128])
        
        # Enable chunking for subsampling module
        model.change_subsampling_conv_chunking_factor(1)
        print("✓ Local attention enabled for memory efficiency")
    except Exception as e:
        print(f"Note: Could not enable local attention: {e}")
        print("Proceeding with default attention mechanism")
    
    print(f"\nTranscribing: {audio_path}")
    print("This may take a moment for long audio files...")
    
    # Transcribe
    transcriptions = model.transcribe([audio_path])
    
    # Get the transcription text - Parakeet returns Hypothesis objects
    if isinstance(transcriptions, list) and len(transcriptions) > 0:
        transcription = transcriptions[0]
        if hasattr(transcription, 'text'):
            # Extract text from Hypothesis object
            result = transcription.text
        elif isinstance(transcription, tuple):
            # Handle tuple output
            result = transcription[0] if transcription else ""
        elif isinstance(transcription, str):
            # Already a string
            result = transcription
        else:
            # Try to extract text attribute or convert to string
            result = getattr(transcription, 'text', str(transcription))
    else:
        result = str(transcriptions)
    
    # Save to file
    with open(output_path, "w") as f:
        f.write(f"Model: NVIDIA Parakeet-TDT-0.6B-v2\n")
        f.write(f"Audio: {audio_path}\n")
        f.write(f"{'='*50}\n")
        f.write(f"Transcription:\n{result}\n")
    
    print(f"\n✓ Transcription saved to: {output_path}")
    
    # Show preview
    preview_length = 500
    result_text = result if isinstance(result, str) else str(result)
    if len(result_text) > preview_length:
        print(f"\nPreview (first {preview_length} chars):")
        print(result_text[:preview_length] + "...")
    else:
        print(f"\nTranscription:")
        print(result_text)
    
    if torch.cuda.is_available():
        print(f"\nFinal GPU Memory: {torch.cuda.memory_allocated()/1024**3:.2f}GB")
    
    return result

def main():
    if len(sys.argv) < 2:
        print("Usage: python parakeet_transcribe.py <audio_file> [output_file]")
        print("\nFeatures:")
        print("- Handles audio up to 3 hours long")
        print("- 60x faster than real-time")
        print("- Only 0.6B parameters (memory efficient)")
        print("- 6.05% WER on Open ASR Leaderboard")
        sys.exit(1)
    
    audio_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else "transcription.txt"
    
    try:
        transcribe_with_parakeet(audio_file, output_file)
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()