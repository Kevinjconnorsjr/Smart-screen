#!/usr/bin/env bash
# Pulls the latest code from GitHub and restarts the display if anything changed.
# Run automatically every 15 minutes by smart-screen-update.timer; you can also run it by hand:
#   sudo ./deploy/update.sh
#
# Only "fast-forward" updates are applied. If you edited code files on the Pi itself, the update is
# skipped (and logged) instead of overwriting your changes. config.yaml, your photos and
# backgrounds are never in git, so updates don't touch them.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OWNER="$(stat -c %U "$APP_DIR")"
as_owner() { if [ "$(id -un)" = "$OWNER" ]; then "$@"; else runuser -u "$OWNER" -- "$@"; fi; }
git_() { as_owner git -C "$APP_DIR" "$@"; }

BRANCH="$(git_ rev-parse --abbrev-ref HEAD)"
git_ fetch --quiet origin "$BRANCH"

LOCAL="$(git_ rev-parse HEAD)"
REMOTE="$(git_ rev-parse "origin/$BRANCH")"
if [ "$LOCAL" = "$REMOTE" ]; then
  echo "Up to date ($BRANCH @ ${LOCAL:0:7})"
  exit 0
fi
if ! git_ merge-base --is-ancestor "$LOCAL" "$REMOTE"; then
  echo "Not updating: this Pi has commits that aren't on GitHub ($BRANCH). Push or reset them first." >&2
  exit 1
fi
if ! git_ diff --quiet HEAD -- || ! git_ diff --cached --quiet; then
  echo "Not updating: files were edited on this Pi. See them with: git -C $APP_DIR status" >&2
  exit 1
fi

CHANGED="$(git_ diff --name-only "$LOCAL" "$REMOTE")"
git_ merge --ff-only --quiet "$REMOTE"
echo "Updated $BRANCH ${LOCAL:0:7} -> ${REMOTE:0:7}"
echo "$CHANGED" | sed 's/^/  /'

if echo "$CHANGED" | grep -q 'requirements.txt'; then
  echo "Installing new Python packages..."
  as_owner "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
fi
if echo "$CHANGED" | grep -qE 'deploy/(smart-screen\.service|smart-screen-update\.(service|timer))'; then
  echo "Service files changed: re-run ./deploy/install.sh to apply them."
fi

if [ "$(id -u)" = 0 ]; then
  systemctl restart smart-screen
else
  sudo systemctl restart smart-screen
fi
echo "Restarted. The screen reloads itself."
