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

URL="https://localhost:${PORT}/rest/db/scan"
[[ -n "${1:-}" ]] && URL="${URL}?folder=$1"

curl -sk -X POST "$URL" -H "X-API-Key: ${API_KEY}"
echo "Rescan triggered."
