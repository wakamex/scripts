#!/usr/bin/env bash
# Show recent Syncthing activity: scans, syncs, and file changes.
# Usage: syncthing-status.sh [N]  (default: last 20 events)

set -euo pipefail

LIMIT="${1:-20}"
PORT="${SYNCTHING_PORT:-8384}"
CFG=""
METHOD=""

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
URL="https://localhost:${PORT}"

curl -sk "$URL/rest/events?since=0&limit=$LIMIT" -H "X-API-Key: $API_KEY" | python3 -c "
import sys, json

events = json.load(sys.stdin)
if not events:
    print('No recent events.')
    sys.exit()

for e in events:
    ts = e['time'][:19].replace('T', ' ')
    typ = e['type']
    d = e.get('data', {})

    if typ == 'StateChanged':
        print(f'{ts}  {d.get(\"folder\",\"?\"): <20} {d.get(\"from\")} → {d.get(\"to\")}')
    elif typ in ('ItemFinished', 'ItemStarted'):
        action = d.get('action', d.get('type', '?'))
        print(f'{ts}  {d.get(\"folder\",\"?\"): <20} {action: <8} {d.get(\"item\",\"?\")}')
    elif typ == 'FolderCompletion':
        pct = d.get('completion', 0)
        if pct < 100:
            print(f'{ts}  {d.get(\"folder\",\"?\"): <20} sync {pct:.1f}%')
    elif typ == 'FolderSummary':
        s = d.get('summary', {})
        print(f'{ts}  {d.get(\"folder\",\"?\"): <20} files:{s.get(\"inSyncFiles\",0)}/{s.get(\"globalFiles\",0)}')
    elif typ in ('FolderScanProgress', 'FolderErrors'):
        print(f'{ts}  {d.get(\"folder\",\"?\"): <20} {typ}')
    elif typ == 'LocalIndexUpdated':
        print(f'{ts}  {d.get(\"folder\",\"?\"): <20} index updated ({d.get(\"items\",0)} items)')
    elif typ == 'RemoteIndexUpdated':
        print(f'{ts}  {d.get(\"folder\",\"?\"): <20} remote index ({d.get(\"device\",\"?\")[:7]}… {d.get(\"items\",0)} items)')
    else:
        print(f'{ts}  {typ}')
"
