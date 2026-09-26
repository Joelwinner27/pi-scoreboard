#!/bin/bash
# Opens the line board full screen on the Pi's display and keeps it healthy.
# Started automatically at login (setup-pi.sh installs it); safe to run by hand.
#
# Watchdog: if the board stops checking in with server.py for 5 minutes while
# the browser is still open (page frozen), the browser is restarted.
# Closing the browser yourself (Alt+F4) ends the watchdog - it won't reopen.

URL="http://localhost:8080/"
PROFILE="$HOME/.config/lineboard-chromium"   # own profile, so these settings always apply
STALE_SECONDS=300

BROWSER=$(command -v chromium || command -v chromium-browser)
if [ -z "$BROWSER" ]; then
  echo "Chromium not found"; exit 1
fi

# wait for the server (it starts at boot, may take a moment)
for _ in $(seq 1 60); do
  curl -s -o /dev/null "$URL" && break
  sleep 2
done

launch() {
  "$BROWSER" --kiosk "$URL" \
    --user-data-dir="$PROFILE" \
    --noerrdialogs --disable-infobars --no-first-run \
    --disable-session-crashed-bubble --password-store=basic \
    --disable-background-timer-throttling \
    --disable-backgrounding-occluded-windows \
    --disable-renderer-backgrounding \
    >/dev/null 2>&1 &
  BROWSER_PID=$!
}

board_age() {
  curl -s -m 5 http://localhost:8080/api/status \
    | python3 -c "import json,sys; a=json.load(sys.stdin).get('age'); print(int(a) if a is not None else -1)" 2>/dev/null \
    || echo -1
}

launch
sleep 120   # give the board time to load and check in

while kill -0 "$BROWSER_PID" 2>/dev/null; do
  age=$(board_age)
  if [ "$age" -gt "$STALE_SECONDS" ]; then
    echo "$(date) board silent for ${age}s - restarting browser"
    kill "$BROWSER_PID" 2>/dev/null
    sleep 5
    pkill -f -- "--user-data-dir=$PROFILE" 2>/dev/null
    sleep 2
    launch
    sleep 120
  fi
  sleep 60
done
