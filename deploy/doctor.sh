#!/usr/bin/env bash
# One-shot health check for the Smart Screen. Run it on the Pi and send the output (or a photo of it):
#   ./deploy/doctor.sh
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR" || exit 1
ok()  { printf '\033[32m✔ %s\033[0m\n' "$*"; }
bad() { printf '\033[31m✘ %s\033[0m\n' "$*"; }

echo "== Smart Screen check ($(date '+%b %d %H:%M'))"
echo "Folder:   $APP_DIR"
echo "Version:  $(git log -1 --format='%h %cd' --date=format:'%b %d %H:%M' 2>/dev/null)"
echo "Address:  $(hostname -I | awk '{print $1}')   Name: $(hostname)"
echo "Python:   $(.venv/bin/python --version 2>&1)"

state="$(systemctl is-active smart-screen 2>/dev/null)"
[ "$state" = active ] && ok "Service running" || bad "Service: ${state:-not installed}  (install: ./deploy/install.sh)"
if curl -fs --max-time 3 http://localhost:8080/api/pair/status >/dev/null; then ok "Screen answering on port 8080"; else bad "Nothing answering on port 8080"; fi
[ -f models/gesture_recognizer.task ] && ok "Gesture model present" || bad "Gesture model missing"
ls /dev/video* >/dev/null 2>&1 && ok "Camera device(s): $(ls /dev/video* | head -3 | tr '\n' ' ')" || bad "No camera found (/dev/video*)"
[ -f /etc/avahi/services/smart-screen.service ] && ok "Phone app announcement installed" || bad "Phone app announcement missing (re-run ./deploy/install.sh)"
systemctl is-active --quiet smart-screen-update.timer && ok "Auto-update on" || bad "Auto-update timer off"

echo
echo "== Last errors from the Smart Screen"
journalctl -u smart-screen -n 200 --no-pager -o cat 2>/dev/null \
  | grep -E "Error|error|Traceback|Exception|No module|missing|failed|Camera" | grep -v "^ *$" | tail -12
echo
echo "== Last 8 log lines"
journalctl -u smart-screen -n 8 --no-pager -o cat 2>/dev/null
