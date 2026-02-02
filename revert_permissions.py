import os
import re
from pathlib import Path

def parse_diff_and_revert_permissions(diff_content):
    """
    Parses git diff output to identify files with permission changes from 100644 to 100755
    and reverts them back to 644.
    """
    # Regular expression to match diff file headers with mode changes
    file_pattern = re.compile(r"diff --git a/(.*?) b/")
    mode_pattern = re.compile(r"old mode (\d+)\nnew mode (\d+)")
    
    current_file = None
    files_to_revert = set()
    
    # Process diff content line by line
    lines = diff_content.split('\n')
    for line in lines:
        # Check for file headers
        file_match = file_pattern.match(line)
        if file_match:
            current_file = file_match.group(1)
            continue
            
        # Check for mode changes
        if line.startswith('old mode') and current_file:
            next_line_idx = lines.index(line) + 1
            if next_line_idx < len(lines) and lines[next_line_idx].startswith('new mode'):
                combined_modes = f"{line}\n{lines[next_line_idx]}"
                mode_match = mode_pattern.match(combined_modes)
                
                if mode_match and mode_match.group(1) == '100644' and mode_match.group(2) == '100755':
                    files_to_revert.add(current_file)

    # Revert permissions for identified files
    root_dir = Path.cwd()
    count = 0
    
    print("Reverting permissions for the following files:")
    for file_path in sorted(files_to_revert):
        full_path = root_dir / file_path
        if full_path.exists():
            try:
                os.chmod(full_path, 0o644)
                print(f"✓ {file_path}")
                count += 1
            except Exception as e:
                print(f"✗ Error reverting {file_path}: {e}")
        else:
            print(f"✗ File not found: {file_path}")
    
    print(f"\nSuccessfully reverted permissions for {count} files")

# Get the diff content from stdin or file
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        # Read from file if provided
        with open(sys.argv[1], 'r') as f:
            diff_content = f.read()
    else:
        # Otherwise read from stdin
        diff_content = sys.stdin.read()
    
    parse_diff_and_revert_permissions(diff_content)
