#!/bin/bash
# Copy the line board files to the Raspberry Pi.
#   ./deploy.sh
# The TV reloads itself within a few seconds when LineBoard.html changes, and
# (once setup-pi.sh has been run on the Pi) the server restarts itself when
# server.py changes.
#
# Settings live in .deploy.env next to this script (kept out of git):
#   PI="user@192.168.1.50"   # Pi username @ address (run `hostname -I` on the Pi)
#   DIR="Scorebug"            # folder in the Pi's home directory

cd "$(dirname "$0")" || exit 1
[ -f .deploy.env ] && source .deploy.env
if [ -z "$PI" ] || [ -z "$DIR" ]; then
  echo "Create .deploy.env with PI=\"user@address\" and DIR=\"folder\" first."
  exit 1
fi

scp LineBoard.html server.py remote.html start-board.sh setup-pi.sh hide-cursor.sh "$PI:$DIR/" || { echo "Copy failed - is the Pi on and on the same Wi-Fi?"; exit 1; }
echo "Copied to $PI:~/$DIR"
