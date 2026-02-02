#!/usr/bin/env python3
"""Fix Parakeet transcription file that saved the entire Hypothesis object"""

import re
import sys

def extract_text_from_hypothesis(filepath):
    """Extract just the text from a saved Hypothesis object"""
    
    with open(filepath, 'r') as f:
        content = f.read()
    
    # Look for the pattern: Hypothesis(score=..., y_sequence=..., text="..."
    # and extract just the text content
    match = re.search(r'text="([^"]+)"', content)
    
    if match:
        # Found the text field
        text = match.group(1)
        print(f"Extracted {len(text)} characters of text")
        return text
    else:
        # Maybe it's already just text, check if it starts with "Hypothesis("
        if content.startswith("Hypothesis("):
            print("ERROR: Could not extract text from Hypothesis object")
            return None
        else:
            print("File appears to already contain plain text")
            return content

def main():
    if len(sys.argv) < 2:
        print("Usage: python fix_parakeet_output.py <transcription_file> [output_file]")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else input_file.replace('.txt', '_fixed.txt')
    
    # Read and process
    text = extract_text_from_hypothesis(input_file)
    
    if text:
        # Save the cleaned text
        with open(output_file, 'w') as f:
            # Check if there's a header to preserve
            with open(input_file, 'r') as original:
                lines = original.readlines()
                # Keep the first few lines if they're metadata
                for line in lines[:10]:
                    if line.startswith("Model:") or line.startswith("Audio:") or line.startswith("==="):
                        f.write(line)
                    elif line.startswith("Transcription:"):
                        f.write(line)
                        break
            
            # Write the cleaned text
            f.write(text + "\n")
        
        print(f"✓ Fixed transcription saved to: {output_file}")
        
        # Show preview
        preview = text[:200] + "..." if len(text) > 200 else text
        print(f"\nPreview: {preview}")
    else:
        print("Failed to extract text")

if __name__ == "__main__":
    main()