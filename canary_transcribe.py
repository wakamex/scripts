#!/usr/bin/env python3
"""Transcribe audio using Canary-Qwen-2.5B model with proper preprocessing"""

import sys
import torch
import torchaudio
import numpy as np
from nemo.collections.speechlm2.models import SALM

def preprocess_audio(audio_path, target_sample_rate=16000, max_duration=40):
    """Load and preprocess audio for Canary model"""
    
    print(f"Loading audio: {audio_path}")
    
    # Load audio
    waveform, sample_rate = torchaudio.load(audio_path)
    print(f"Original: {sample_rate}Hz, {waveform.shape[0]} channels, {waveform.shape[1]} samples")
    
    # Convert to mono if stereo
    if waveform.shape[0] > 1:
        waveform = torch.mean(waveform, dim=0, keepdim=True)
        print("Converted to mono")
    
    # Resample if needed
    if sample_rate != target_sample_rate:
        resampler = torchaudio.transforms.Resample(sample_rate, target_sample_rate)
        waveform = resampler(waveform)
        print(f"Resampled to {target_sample_rate}Hz")
    
    # Trim to max duration
    max_samples = max_duration * target_sample_rate
    if waveform.shape[1] > max_samples:
        waveform = waveform[:, :max_samples]
        print(f"Trimmed to {max_duration} seconds")
    
    # Convert to numpy and squeeze
    audio_array = waveform.squeeze().numpy()
    
    duration = len(audio_array) / target_sample_rate
    print(f"Preprocessed: {target_sample_rate}Hz, {len(audio_array)} samples, {duration:.2f} seconds")
    
    return audio_array, target_sample_rate

def transcribe_with_canary(audio_path, language="en"):
    """Transcribe audio using Canary model"""
    
    try:
        # Preprocess audio
        audio_array, sample_rate = preprocess_audio(audio_path)
        
        print("\nLoading Canary model...")
        model = SALM.from_pretrained('nvidia/canary-qwen-2.5b')
        print("✓ Model loaded")
        
        # Save preprocessed audio to temporary file
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_file:
            temp_path = tmp_file.name
            torchaudio.save(temp_path, torch.from_numpy(audio_array).unsqueeze(0), sample_rate)
            print(f"Saved preprocessed audio to: {temp_path}")
        
        # Create prompt
        audio_tag = model.audio_locator_tag if hasattr(model, 'audio_locator_tag') else "<audio>"
        
        # Try different prompt formats
        prompts = [
            [{
                "role": "user",
                "content": f"Transcribe the following audio: {audio_tag}",
                "audio": [temp_path]
            }]
        ]
        
        print("\nGenerating transcription...")
        
        # Generate with different parameters
        answer_ids = model.generate(
            prompts=prompts,
            max_new_tokens=512,
            temperature=0.1,
            do_sample=False,
        )
        
        # Convert to text
        transcription = model.tokenizer.ids_to_text(answer_ids[0].cpu())
        
        # Clean up temp file
        import os
        os.unlink(temp_path)
        
        return transcription
        
    except Exception as e:
        import traceback
        print(f"\nError during transcription: {e}")
        print("\nFull traceback:")
        traceback.print_exc()
        return None

def main():
    if len(sys.argv) < 2:
        print("Usage: python canary_transcribe.py <audio_file> [language]")
        print("Languages: en (English), default: en")
        print("Audio requirements: Will be auto-converted to 16kHz mono, max 40s")
        sys.exit(1)
    
    audio_file = sys.argv[1]
    language = sys.argv[2] if len(sys.argv) > 2 else "en"
    
    result = transcribe_with_canary(audio_file, language)
    
    if result:
        print("\n" + "="*50)
        print("TRANSCRIPTION:")
        print("="*50)
        print(result)
        print("="*50)
    else:
        print("\nTranscription failed.")

if __name__ == "__main__":
    main()