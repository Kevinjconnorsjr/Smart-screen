#!/usr/bin/env bash
# Turns the monitor on or off on Raspberry Pi OS (Wayland desktop). Usage: screen.sh on|off
# Use it in config.yaml: screen_off_command: "<path>/deploy/screen.sh off"
set -u
case "${1:-}" in on|off) ;; *) echo "usage: $0 on|off" >&2; exit 2 ;; esac
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
if [ -z "${WAYLAND_DISPLAY:-}" ]; then
  WAYLAND_DISPLAY="$(ls -1 "$XDG_RUNTIME_DIR" 2>/dev/null | grep -E '^wayland-[0-9]+$' | head -1)"
  export WAYLAND_DISPLAY
fi
command -v wlr-randr >/dev/null || { echo "wlr-randr is missing: sudo apt install wlr-randr" >&2; exit 1; }
for output in $(wlr-randr | grep -E '^[^ ]' | cut -d' ' -f1); do
  wlr-randr --output "$output" --"$1"
done
