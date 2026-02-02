#!/usr/bin/env python3
"""Compare Whisper and Parakeet transcriptions"""

import difflib
import re

# Read Whisper transcription
whisper_file = "/home/mihai/defi/Davis1.txt"
with open(whisper_file, 'r') as f:
    whisper_lines = f.readlines()

# Extract just text from Whisper (remove timestamps)
whisper_text = ""
for line in whisper_lines:
    # Remove timestamp pattern [XXs -> XXs]
    text = re.sub(r'\[\d+\.?\d*s -> \d+\.?\d*s\]\s*', '', line.strip())
    if text:
        whisper_text += text + " "

# Read Parakeet transcription
parakeet_file = "/home/mihai/Downloads/transcription_parakeet.txt"
with open(parakeet_file, 'r') as f:
    content = f.read()
    
# Extract text from Parakeet (it's in the text="" field)
parakeet_match = re.search(r'text="([^"]+)"', content)
if parakeet_match:
    parakeet_text = parakeet_match.group(1)
else:
    parakeet_text = "Could not extract Parakeet text"

# Basic statistics
print("TRANSCRIPTION STATISTICS")
print("=" * 50)
print(f"Whisper word count: {len(whisper_text.split())}")
print(f"Parakeet word count: {len(parakeet_text.split())}")
print(f"Whisper char count: {len(whisper_text)}")
print(f"Parakeet char count: {len(parakeet_text)}")

# Sample comparison (first 500 chars)
print("\n\nFIRST 500 CHARACTERS COMPARISON")
print("=" * 50)
print("WHISPER:")
print(whisper_text[:500])
print("\nPARAKEET:")
print(parakeet_text[:500])

# Find differences in first 1000 chars
print("\n\nKEY DIFFERENCES (first 1000 chars)")
print("=" * 50)

# Create word-level comparison
whisper_words = whisper_text[:1000].split()
parakeet_words = parakeet_text[:1000].split()

differ = difflib.unified_diff(
    whisper_words[:50],  # First 50 words
    parakeet_words[:50],
    lineterm='',
    fromfile='Whisper',
    tofile='Parakeet'
)

for line in differ:
    if line.startswith('-'):
        print(f"Whisper only: {line}")
    elif line.startswith('+'):
        print(f"Parakeet only: {line}")

# Calculate similarity ratio
matcher = difflib.SequenceMatcher(None, whisper_text[:5000], parakeet_text[:5000])
similarity = matcher.ratio()
print(f"\n\nSIMILARITY SCORE (first 5000 chars): {similarity:.2%}")

# Check for specific differences
print("\n\nSPECIFIC DIFFERENCES")
print("=" * 50)

# Check acronyms
if "OSAP" in whisper_text[:5000] and "ISAP" in parakeet_text[:5000]:
    print("⚠️ Acronym difference: Whisper=OSAP, Parakeet=ISAP")
if "ATIP" in whisper_text[:5000] and "ATIP" in parakeet_text[:5000]:
    print("✓ Both have ATIP correctly")

# Check names
names_to_check = ["Dave Grush", "Jay Stratton", "George Bush", "Eric Davis"]
for name in names_to_check:
    in_whisper = name in whisper_text[:5000]
    in_parakeet = name in parakeet_text[:5000]
    if in_whisper and in_parakeet:
        print(f"✓ Both have: {name}")
    elif in_whisper and not in_parakeet:
        print(f"⚠️ Only Whisper has: {name}")
    elif not in_whisper and in_parakeet:
        print(f"⚠️ Only Parakeet has: {name}")