#!/usr/bin/env bash
# Controls the monitor on Raspberry Pi OS (Wayland desktop).
#   screen.sh on | off                  power the monitor on/off (used for sleep)
#   screen.sh rotate normal|90|180|270  rotate the picture (portrait = 90 or 270)
#   screen.sh rotate-from-settings      rotate as set in the Smart Screen settings (used by the kiosk)
set -u
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
if [ -z "${WAYLAND_DISPLAY:-}" ]; then
  WAYLAND_DISPLAY="$(ls -1 "$XDG_RUNTIME_DIR" 2>/dev/null | grep -E '^wayland-[0-9]+$' | head -1)"
  export WAYLAND_DISPLAY
fi
command -v wlr-randr >/dev/null || { echo "wlr-randr is missing: sudo apt install wlr-randr" >&2; exit 1; }
[ -n "$WAYLAND_DISPLAY" ] || { echo "No desktop running yet" >&2; exit 1; }
outputs() { wlr-randr | grep -E '^[^ ]' | cut -d' ' -f1; }

case "${1:-}" in
  on|off)
    for o in $(outputs); do wlr-randr --output "$o" --"$1"; done ;;
  rotate)
    t="${2:-normal}"
    case "$t" in normal|90|180|270) ;; *) echo "rotate: normal, 90, 180 or 270" >&2; exit 2 ;; esac
    for o in $(outputs); do wlr-randr --output "$o" --transform "$t"; done ;;
  rotate-from-settings)
    t="$(curl -fs --max-time 5 http://localhost:8080/api/settings \
         | python3 -c 'import sys,json; print(json.load(sys.stdin).get("transform","normal"))' 2>/dev/null)"
    exec "$0" rotate "${t:-normal}" ;;
  *)
    echo "usage: $0 on|off|rotate <normal|90|180|270>|rotate-from-settings" >&2; exit 2 ;;
esac
