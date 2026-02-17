#!/usr/bin/env bash
# Force Syncthing to rescan all folders (or a specific folder).
# Usage: syncthing-rescan.sh [folder-id]

set -euo pipefail

PORT="${SYNCTHING_PORT:-8384}"
CFG=$(syncthing --paths 2>/dev/null | grep -A1 "Configuration file:" | tail -1 | tr -d '[:space:]')
[[ -z "$CFG" ]] && echo "Could not find Syncthing config (is syncthing in PATH?)" >&2 && exit 1
API_KEY=$(sed -n 's/.*<apikey>\([^<]*\)<.*/\1/p' "$CFG")
[[ -z "$API_KEY" ]] && echo "Could not read API key from $CFG" >&2 && exit 1

URL="https://localhost:${PORT}/rest/db/scan"
[[ -n "${1:-}" ]] && URL="${URL}?folder=$1"

curl -sk -X POST "$URL" -H "X-API-Key: ${API_KEY}"
echo "Rescan triggered."
