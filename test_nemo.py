#!/usr/bin/env python3
"""Test NeMo installation and Canary model loading"""

import torch
print(f"PyTorch version: {torch.__version__}")
print(f"CUDA available: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"CUDA device: {torch.cuda.get_device_name(0)}")

# Test basic NeMo import
try:
    import nemo
    print(f"NeMo version: {nemo.__version__}")
    print("✓ NeMo imported successfully")
except ImportError as e:
    print(f"✗ Failed to import NeMo: {e}")
    exit(1)

# Test ASR collections
try:
    from nemo.collections import asr
    print("✓ ASR collections available")
except ImportError as e:
    print(f"✗ Failed to import ASR collections: {e}")

# Test if speechlm2 is available (needed for Canary)
try:
    from nemo.collections.speechlm2.models import SALM
    print("✓ SpeechLM2 SALM model class available")
    
    # Try to load the model (this will download it if not cached)
    print("\nAttempting to load nvidia/canary-qwen-2.5b model...")
    print("Note: This will download ~2.5GB if not already cached")
    
    model = SALM.from_pretrained('nvidia/canary-qwen-2.5b')
    print("✓ Model loaded successfully!")
    print(f"Model type: {type(model)}")
    
except ImportError as e:
    print(f"✗ SpeechLM2 not available: {e}")
    print("\nTrying alternative import paths...")
    
    # Try alternative imports
    try:
        from nemo.collections.asr.models import EncDecSpeechLMModel
        print("✓ Found EncDecSpeechLMModel")
    except:
        pass
    
    try:
        from nemo.collections.asr.models import ASRModel
        print("✓ Found ASRModel base class")
        
        # List available models
        print("\nAvailable pretrained ASR models:")
        from nemo.collections.asr.models import ASRModel
        available_models = ASRModel.list_available_models()
        for model in available_models[:5]:  # Show first 5
            print(f"  - {model}")
    except Exception as e:
        print(f"Could not list models: {e}")
        
except Exception as e:
    print(f"✗ Error loading model: {e}")
    print("\nThis might be normal if the model requires additional dependencies.")
    print("The model may also need to be downloaded from HuggingFace first.")