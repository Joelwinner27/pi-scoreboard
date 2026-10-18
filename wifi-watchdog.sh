#!/bin/bash
# Wi-Fi watchdog for the line board Pi. setup-pi.sh installs it to run every
# 2 minutes as root (lineboard-wifi-watchdog.timer).
#
# If the router can't be reached:
#   3 checks in a row (~6 min)   → switch Wi-Fi off and on
#   6 checks in a row (~12 min)  → restart NetworkManager
#   10+ checks in a row (~20 min) → reboot, at most once an hour
# Anything it does is logged: journalctl -t lineboard-wifi

STATE=/run/lineboard-wifi-fails

router=$(ip route show default 2>/dev/null | awk '/^default/ {print $3; exit}')
if [ -n "$router" ] && ping -c 2 -W 3 "$router" >/dev/null 2>&1; then
  if [ -f "$STATE" ]; then
    logger -t lineboard-wifi "router reachable again after $(cat "$STATE") failed checks"
    rm -f "$STATE"
  fi
  exit 0
fi

fails=$(( $(cat "$STATE" 2>/dev/null || echo 0) + 1 ))
echo "$fails" > "$STATE"
logger -t lineboard-wifi "can't reach the router (${router:-no route}) - check $fails"

if [ "$fails" -eq 3 ]; then
  logger -t lineboard-wifi "switching Wi-Fi off and on"
  nmcli radio wifi off; sleep 5; nmcli radio wifi on
elif [ "$fails" -eq 6 ]; then
  logger -t lineboard-wifi "restarting NetworkManager"
  systemctl restart NetworkManager
elif [ "$fails" -ge 10 ] && [ "$(cut -d. -f1 /proc/uptime)" -gt 3600 ]; then
  logger -t lineboard-wifi "still offline - rebooting"
  systemctl reboot
fi
