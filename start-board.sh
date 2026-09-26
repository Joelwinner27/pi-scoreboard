#!/bin/bash
# Opens the line board full screen on the Pi's display and keeps it healthy.
# Started automatically at login (setup-pi.sh installs it); safe to run by hand.
#
# Watchdog: the board checks in with server.py every few seconds. If it hasn't
# for 5 minutes while the browser is open (frozen or half-loaded page), the
# browser is restarted. Closing the browser yourself (Alt+F4) ends the
# watchdog - it won't reopen.
#
# Browser messages (including page errors) go to ~/.cache/lineboard-browser.log

URL="http://localhost:8080/"
PROFILE="$HOME/.config/lineboard-chromium"   # own profile, so these settings always apply
LOG="$HOME/.cache/lineboard-browser.log"
STALE_SECONDS=300

BROWSER=$(command -v chromium || command -v chromium-browser)
if [ -z "$BROWSER" ]; then
  echo "Chromium not found"; exit 1
fi
mkdir -p "$(dirname "$LOG")"

# wait for the server (it starts at boot, may take a moment)
for _ in $(seq 1 60); do
  curl -s -o /dev/null "$URL" && break
  sleep 2
done

launch() {
  # keep the log from growing forever
  [ -f "$LOG" ] && [ "$(stat -c %s "$LOG" 2>/dev/null || echo 0)" -gt 5000000 ] && mv "$LOG" "$LOG.old"
  echo "=== $(date) starting browser" >> "$LOG"
  "$BROWSER" --kiosk "$URL" \
    --user-data-dir="$PROFILE" \
    --noerrdialogs --disable-infobars --no-first-run \
    --disable-session-crashed-bubble --password-store=basic \
    --disable-background-timer-throttling \
    --disable-backgrounding-occluded-windows \
    --disable-renderer-backgrounding \
    --enable-logging=stderr --log-level=0 \
    >> "$LOG" 2>&1 &
  BROWSER_PID=$!
  last_ok=$(date +%s)
}

# seconds since the board last checked in with the server (-1 = not since the server started)
board_age() {
  curl -s -m 5 http://localhost:8080/api/status \
    | python3 -c "import json,sys; a=json.load(sys.stdin).get('age'); print(int(a) if a is not None else -1)" 2>/dev/null \
    || echo -1
}

launch
sleep 90   # give the board time to load

while kill -0 "$BROWSER_PID" 2>/dev/null; do
  now=$(date +%s)
  age=$(board_age)
  if [ "$age" -ge 0 ] && [ "$age" -le 60 ]; then
    last_ok=$now
  fi
  if [ $((now - last_ok)) -gt "$STALE_SECONDS" ]; then
    echo "=== $(date) board hasn't checked in for $((now - last_ok))s - restarting browser" >> "$LOG"
    kill "$BROWSER_PID" 2>/dev/null
    sleep 5
    pkill -f -- "--user-data-dir=$PROFILE" 2>/dev/null
    sleep 2
    launch
    sleep 90
    continue
  fi
  sleep 60
done
