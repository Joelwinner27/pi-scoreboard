#!/bin/bash
# One-time Raspberry Pi setup. Run on the Pi from the board's folder:
#   bash setup-pi.sh
# - runs server.py as a background service (starts at boot, restarts if it stops,
#   and restarts itself when a new server.py is copied over)
# - opens the board full screen at login, with a watchdog (start-board.sh)
# - keeps Wi-Fi connected: power saving off, retries forever after a power cut,
#   and a watchdog that resets Wi-Fi if the router stops answering
# - turns off screen blanking
# Safe to run again after updates. Asks for your password once (for sudo).

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
ME="$(whoami)"

echo "== Board folder: $DIR (user $ME)"
for f in server.py LineBoard.html remote.html start-board.sh wifi-watchdog.sh; do
  [ -f "$DIR/$f" ] || { echo "Missing $f in $DIR - copy the files first (./deploy.sh)"; exit 1; }
done
chmod +x "$DIR/start-board.sh"

echo "== Stopping any server started by hand"
pkill -f "python3 server.py" 2>/dev/null || true

echo "== Installing the server as a service (lineboard)"
sudo tee /etc/systemd/system/lineboard.service >/dev/null <<EOF
[Unit]
Description=Line board server
After=network-online.target
Wants=network-online.target

[Service]
User=$ME
WorkingDirectory=$DIR
Environment=LINEBOARD_AUTORESTART=1
ExecStart=/usr/bin/python3 $DIR/server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now lineboard
sleep 2
systemctl is-active --quiet lineboard && echo "   server running" || { echo "   server failed to start:"; sudo journalctl -u lineboard -n 20 --no-pager; exit 1; }

echo "== Opening the board at login"
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/lineboard.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Line Board
Exec=$DIR/start-board.sh
X-GNOME-Autostart-enabled=true
EOF
# any older full-screen browser launcher would open a second browser on top
for f in "$HOME/.config/autostart"/*.desktop; do
  [ "$f" = "$HOME/.config/autostart/lineboard.desktop" ] && continue
  if grep -q -- "--kiosk" "$f" 2>/dev/null; then
    mv "$f" "$f.disabled"
    echo "   turned off old kiosk launcher: $(basename "$f") (renamed to $(basename "$f").disabled)"
  fi
done

echo "== Keeping Wi-Fi connected"
if command -v nmcli >/dev/null; then
  # Wi-Fi power saving makes the Pi's built-in Wi-Fi drop out
  sudo tee /etc/NetworkManager/conf.d/lineboard-wifi.conf >/dev/null <<'EOF'
[connection]
wifi.powersave=2
EOF
  # after a power cut the Pi boots faster than the router; by default it gives
  # up on Wi-Fi after 4 tries and stays offline - keep trying instead
  WIFI="$(nmcli -g GENERAL.CONNECTION dev show wlan0 2>/dev/null || true)"
  if [ -n "$WIFI" ]; then
    sudo nmcli con modify "$WIFI" connection.autoconnect-retries 0 802-11-wireless.powersave 2 \
      && echo "   \"$WIFI\": power saving off, retries forever"
  else
    echo "   not on Wi-Fi right now - power saving is still turned off for when it is"
  fi
  # watchdog: turns Wi-Fi off/on (then harder fixes) when the router can't be reached
  sudo install -m 755 "$DIR/wifi-watchdog.sh" /usr/local/bin/lineboard-wifi-watchdog
  sudo tee /etc/systemd/system/lineboard-wifi-watchdog.service >/dev/null <<'EOF'
[Unit]
Description=Line board Wi-Fi watchdog

[Service]
Type=oneshot
ExecStart=/usr/local/bin/lineboard-wifi-watchdog
EOF
  sudo tee /etc/systemd/system/lineboard-wifi-watchdog.timer >/dev/null <<'EOF'
[Unit]
Description=Check the Wi-Fi every 2 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable --now lineboard-wifi-watchdog.timer
  echo "   Wi-Fi watchdog on (log: journalctl -t lineboard-wifi)"
else
  echo "   NetworkManager not found - skipped"
fi

echo "== Turning off screen blanking"
if command -v raspi-config >/dev/null && sudo raspi-config nonint do_blanking 1 2>/dev/null; then
  echo "   done"
else
  echo "   couldn't set it automatically - use: sudo raspi-config -> Display Options -> Screen Blanking -> No"
fi

echo
echo "All set. Close the browser and any terminal running the server, then reboot:"
echo "   sudo reboot"
echo "After the reboot the board opens by itself. To exit full screen: Alt+F4."
