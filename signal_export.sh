#!/bin/bash
# Export Signal Desktop conversations from the Windows PC as text.
#
#   signal_export.sh CONVERSATION OUTDIR
#
# Runs sigtop on the Windows PC (SSH alias `windows`) into a temporary folder
# there, copies the text exports into OUTDIR, one .txt per conversation that
# matches CONVERSATION (a group or contact name, as sigtop's -c takes it), and
# deletes the Windows copy. Files are written mode 600 and replace same-named
# files in OUTDIR. Exits 2 without changes if the PC is unreachable, and 1 if
# no conversation matched.
set -euo pipefail

[[ $# -eq 2 ]] || { echo "usage: signal_export.sh CONVERSATION OUTDIR" >&2; exit 64; }
CONVERSATION=$1
OUTDIR=$2
SIGTOP='C:\Users\Mihai\go\bin\sigtop.exe'
REMOTE_DIR="C:/Users/Mihai/apps/signal-export-$(date +%s)-$$"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=10 windows)

"${SSH[@]}" "exit 0" || { echo "windows unreachable; nothing exported" >&2; exit 2; }

STAGE=$(mktemp -d)
cleanup() {
    rm -rf "$STAGE"
    "${SSH[@]}" "if (Test-Path '$REMOTE_DIR') { Remove-Item -Recurse -Force '$REMOTE_DIR' }" || true
}
trap cleanup EXIT

"${SSH[@]}" "$SIGTOP export-messages -f text -c '$CONVERSATION' '$REMOTE_DIR'"
scp -q -o BatchMode=yes "windows:$REMOTE_DIR/*.txt" "$STAGE/" 2>/dev/null || true

if ! compgen -G "$STAGE/*.txt" >/dev/null; then
    echo "no Signal conversation matched '$CONVERSATION'; nothing exported" >&2
    exit 1
fi

umask 077
mkdir -p "$OUTDIR"
chmod 600 "$STAGE"/*.txt
cp -p "$STAGE"/*.txt "$OUTDIR/"
ls "$STAGE"
