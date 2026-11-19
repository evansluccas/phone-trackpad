#!/bin/bash
# Start Phone Trackpad automatically every time you log in to your Mac.
#
#   ./autostart.sh install     start now and at every login
#   ./autostart.sh uninstall   stop it and remove autostart
#   ./autostart.sh restart     restart it (e.g. after allowing Accessibility)
#   ./autostart.sh status      is it running? shows the phone URL
#   ./autostart.sh log         show the server log
set -euo pipefail

LABEL="local.phone-trackpad"
DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/phone-trackpad.log"
DOMAIN="gui/$(id -u)"

case "${1:-}" in
  install)
    PY="$(python3 -c 'import sys; print(sys.executable)')"
    mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>-u</string>
    <string>$DIR/server.py</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    : > "$LOG"
    launchctl bootstrap "$DOMAIN" "$PLIST"
    sleep 2
    echo "Installed. Phone Trackpad now starts every time you log in."
    "$0" status
    ;;
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Removed. Phone Trackpad will no longer start at login."
    ;;
  restart)
    : > "$LOG"
    launchctl kickstart -k "$DOMAIN/$LABEL"
    sleep 2
    "$0" status
    ;;
  status)
    if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
      echo "Running in the background."
      grep -E "http://|!|Accessibility|turn ON|python3  \(" "$LOG" | tail -6 || true
    else
      echo "Not installed. Run: ./autostart.sh install"
    fi
    ;;
  log)
    tail -n 40 "$LOG"
    ;;
  *)
    echo "Usage: $0 install | uninstall | restart | status | log"
    exit 1
    ;;
esac
