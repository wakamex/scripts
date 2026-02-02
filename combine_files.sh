#!/bin/bash

# Check for correct number of arguments
if [ $# -ne 3 ]; then
    echo "Usage: $0 TARGET_DIR OUTPUT_FILE FILTER_EXT"
    exit 1
fi

target_dir="$1"
output_file="$2"
filter_ext="$3"

# Check if target directory exists
if [ ! -d "$target_dir" ]; then
    echo "Error: Target directory '$target_dir' does not exist."
    exit 1
fi

# Remove existing output file to start fresh
rm -f "$output_file"

# Process each matching file, handling spaces and special characters in filenames
find "$target_dir" -type f -name "*$filter_ext" -print0 | while IFS= read -r -d '' file; do
    echo "=== START FILE: $file ===" >> "$output_file"
    cat "$file" >> "$output_file"
    printf "\n=== END FILE ===\n\n" >> "$output_file"
done

echo "Successfully combined files into '$output_file'"
