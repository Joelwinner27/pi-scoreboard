#!/bin/bash
# One-time Raspberry Pi setup. Run on the Pi from the board's folder:
#   bash setup-pi.sh
# - runs server.py as a background service (starts at boot, restarts if it stops,
#   and restarts itself when a new server.py is copied over)
# - opens the board full screen at login, with a watchdog (start-board.sh)
# - turns off screen blanking
# Asks for your password once (for sudo).

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
ME="$(whoami)"

echo "== Board folder: $DIR (user $ME)"
for f in server.py LineBoard.html remote.html start-board.sh; do
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
