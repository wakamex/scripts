#!/usr/bin/env bash
# Update Syncthing to the latest GitHub release.
# Usage: syncthing-update.sh (will sudo for the copy step)

set -euo pipefail

latest=$(gh release view --repo syncthing/syncthing --json tagName -q .tagName)
current=$(syncthing --version | awk '{print $2}')

echo "Current: $current, Latest: $latest"

if [[ "$latest" == "$current" ]]; then
  echo "Already up to date."
  exit 0
fi

echo "Downloading $latest..."
gh release download "$latest" --repo syncthing/syncthing --pattern "syncthing-linux-amd64-*.tar.gz" --dir /tmp
tar xzf "/tmp/syncthing-linux-amd64-${latest}.tar.gz" -C /tmp
sudo cp "/tmp/syncthing-linux-amd64-${latest}/syncthing" /usr/bin/syncthing
rm -rf "/tmp/syncthing-linux-amd64-${latest}" "/tmp/syncthing-linux-amd64-${latest}.tar.gz"
systemctl --user restart syncthing
echo "Updated to $latest and restarted."
