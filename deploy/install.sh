#!/usr/bin/env bash
# One-time setup on Raspberry Pi OS (64-bit, Bookworm or newer). Run from the project folder (Smart-screen):
#   ./deploy/install.sh
# Safe to run again (for example after an update that changes the service files).
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="$(pwd)"

echo "== Installing system packages"
sudo apt-get update
sudo apt-get install -y git python3-venv python3-picamera2 libegl1 libgles2 cups printer-driver-gutenprint avahi-daemon wlr-randr fonts-dejavu-core
sudo apt-get install -y chromium-browser || sudo apt-get install -y chromium
sudo usermod -aG lpadmin "$USER"   # lets you add printers at http://localhost:631

echo "== Installing Python packages"
# --system-site-packages so the apt-installed picamera2 is visible inside the venv
python3 -m venv --system-site-packages .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

mkdir -p models photos backgrounds
[ -f models/gesture_recognizer.task ] || curl -L -o models/gesture_recognizer.task \
  https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/latest/gesture_recognizer.task
[ -f config.yaml ] || cp config.example.yaml config.yaml

echo "== Starting on boot + automatic updates"
fill() { sed "s#__DIR__#$DIR#g; s#__USER__#$USER#g; s#__UID__#$(id -u)#g" "deploy/$1" | sudo tee "/etc/systemd/system/$1" >/dev/null; }
fill smart-screen.service
fill smart-screen-update.service
fill smart-screen-update.timer
sudo systemctl daemon-reload
sudo systemctl enable --now smart-screen smart-screen-update.timer

echo "== Phone app support"
# Let the phone app find this screen on the Wi-Fi.
sudo cp deploy/smart-screen.avahi /etc/avahi/services/smart-screen.service
sudo systemctl enable --now avahi-daemon
# Let the app's "Check for updates" button start the updater (that one command only, nothing else).
SUDOERS="$(mktemp)"
echo "$USER ALL=(root) NOPASSWD: /usr/bin/systemctl start --no-block smart-screen-update.service" > "$SUDOERS"
if sudo visudo -cf "$SUDOERS" >/dev/null; then
  sudo install -m 0440 "$SUDOERS" /etc/sudoers.d/smart-screen
fi
rm -f "$SUDOERS"

# Open the screen full-screen when the desktop starts (only one of these, so it doesn't open twice).
KIOSK_CMD="$(grep '^Exec=' deploy/mirror-kiosk.desktop | cut -d= -f2-)"
if command -v labwc >/dev/null; then   # Pi OS desktop since late 2024
  mkdir -p ~/.config/labwc
  touch ~/.config/labwc/autostart
  grep -q 'localhost:8080' ~/.config/labwc/autostart || echo "$KIOSK_CMD &" >> ~/.config/labwc/autostart
  rm -f ~/.config/autostart/mirror-kiosk.desktop
else
  mkdir -p ~/.config/autostart
  cp deploy/mirror-kiosk.desktop ~/.config/autostart/
fi

echo
echo "Done!"
echo "  1. Edit $DIR/config.yaml (weather location, calendar links, lights, printer)"
echo "  2. Put photos in $DIR/backgrounds/"
echo "  3. Reboot: sudo reboot"
echo "Logs:     journalctl -u smart-screen -f"
echo "Phone app: install it from https://github.com/Kevinjconnorsjr/Smart-screen/releases/tag/android-app"
echo "Updates:  checked every 15 min (journalctl -u smart-screen-update) - or now: sudo ./deploy/update.sh"
