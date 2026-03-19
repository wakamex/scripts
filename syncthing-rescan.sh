#!/usr/bin/env bash
# Force Syncthing to rescan all folders (or a specific folder).
# Usage: syncthing-rescan.sh [folder-id]
# Works on Linux, macOS, and Windows (Git Bash/MSYS2).

set -euo pipefail

PORT="${SYNCTHING_PORT:-8384}"
CFG=""
METHOD=""

# Find config.xml: try syncthing paths (new) / --paths (old), fall back to known locations
CFG=$(syncthing paths 2>/dev/null | grep -A1 "Configuration file:" | tail -1 | tr -d '[:space:]' || true)
[[ -n "$CFG" && -f "$CFG" ]] && METHOD="syncthing paths"

if [[ -z "$METHOD" ]]; then
    CFG=$(syncthing --paths 2>/dev/null | grep -A1 "Configuration file:" | tail -1 | tr -d '[:space:]' || true)
    [[ -n "$CFG" && -f "$CFG" ]] && METHOD="syncthing --paths"
fi

if [[ -z "$METHOD" ]]; then
    for candidate in \
        "${HOME}/.local/state/syncthing/config.xml" \
        "${HOME}/.config/syncthing/config.xml" \
        "${HOME}/Library/Application Support/Syncthing/config.xml" \
        "${LOCALAPPDATA:-}/Syncthing/config.xml" \
        "${APPDATA:-}/Syncthing/config.xml"; do
        [[ -f "$candidate" ]] && CFG="$candidate" && METHOD="fallback" && break
    done
fi

[[ -z "$CFG" ]] && echo "Could not find Syncthing config" >&2 && exit 1
echo "Config: $CFG (found via $METHOD)"

API_KEY=$(sed -n 's/.*<apikey>\([^<]*\)<.*/\1/p' "$CFG")
[[ -z "$API_KEY" ]] && echo "Could not read API key from $CFG" >&2 && exit 1

BASE_URL="https://localhost:${PORT}"

mapfile -t FOLDERS < <(
    if [[ -n "${1:-}" ]]; then
        printf '%s\n' "$1"
    else
        python3 - "$CFG" <<'PY'
import sys
import xml.etree.ElementTree as ET

root = ET.parse(sys.argv[1]).getroot()
for folder in root.findall("./folder"):
    folder_id = folder.get("id")
    if folder_id:
        print(folder_id)
PY
    fi
)

[[ "${#FOLDERS[@]}" -eq 0 ]] && echo "No Syncthing folders found in $CFG" >&2 && exit 1

TMP_BODY=$(mktemp)
cleanup() {
    rm -f "$TMP_BODY"
}
trap cleanup EXIT

request_scan() {
    local folder="$1"
    local encoded_folder http_code body url

    encoded_folder=$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$folder")
    url="${BASE_URL}/rest/db/scan?folder=${encoded_folder}"

    if ! http_code=$(curl -sk -o "$TMP_BODY" -w "%{http_code}" -X POST "$url" -H "X-API-Key: ${API_KEY}"); then
        echo "Failed to reach Syncthing API for folder: $folder" >&2
        return 1
    fi

    body=$(<"$TMP_BODY")
    if [[ "$http_code" =~ ^2 ]]; then
        echo "Scan triggered for folder: $folder"
        [[ -n "$body" ]] && printf '%s\n' "$body"
        return 0
    fi

    echo "Failed to scan folder: $folder (HTTP $http_code)" >&2
    [[ -n "$body" ]] && printf '%s\n' "$body" >&2
    return 1
}

failures=0
for folder in "${FOLDERS[@]}"; do
    if ! request_scan "$folder"; then
        failures=$((failures + 1))
    fi
done

if (( failures > 0 )); then
    echo "Rescan failed for $failures folder(s)." >&2
    exit 1
fi

echo "Rescan triggered for ${#FOLDERS[@]} folder(s)."
