#!/usr/bin/env bash
# Force Syncthing to rescan all folders (or a specific folder).
# Usage: syncthing-rescan.sh [folder-id]

set -euo pipefail

PORT="${SYNCTHING_PORT:-8384}"
API_KEY=$(sed -n 's/.*<apikey>\([^<]*\)<.*/\1/p' ~/.config/syncthing/config.xml 2>/dev/null \
       || sed -n 's/.*<apikey>\([^<]*\)<.*/\1/p' ~/.local/state/syncthing/config.xml)

URL="https://localhost:${PORT}/rest/db/scan"
[[ -n "${1:-}" ]] && URL="${URL}?folder=$1"

curl -sk -X POST "$URL" -H "X-API-Key: ${API_KEY}"
echo "Rescan triggered."
