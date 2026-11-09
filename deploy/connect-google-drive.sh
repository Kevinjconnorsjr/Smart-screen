#!/usr/bin/env bash
# Connects the Pi to your Google Drive (read-only) so the Smart Screen can show a Drive folder.
# Run it ON THE PI, at its desktop (a browser window opens to sign in to Google):
#   ./deploy/connect-google-drive.sh
# Then choose the folder in the phone app: Photos -> Google Drive.
set -euo pipefail
if ! command -v rclone >/dev/null; then
  echo "Installing rclone…"
  sudo apt-get install -y rclone
fi
if rclone listremotes | grep -qx "gdrive:"; then
  echo "Google Drive is already connected. To connect a different account first run: rclone config delete gdrive"
else
  echo "A browser window will open. Sign in to Google and click Allow."
  echo "(The Pi only gets permission to READ your Drive. It can never change or delete anything.)"
  rclone config create gdrive drive scope=drive.readonly
fi
echo
echo "Folders at the top of your Drive:"
rclone lsd gdrive: --max-depth 1 | awk '{ $1=$2=$3=$4=""; sub(/^ +/, ""); print "  " $0 }' | head -40 || true
echo
echo "Done. In the phone app open Photos -> Google Drive, type one of these folder names, and turn it on."
