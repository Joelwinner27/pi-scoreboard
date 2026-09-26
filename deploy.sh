#!/bin/bash
# Copy the line board files to the Raspberry Pi.
#   ./deploy.sh
# Only files that actually changed are copied. The TV reloads itself when
# LineBoard.html changes, and (once setup-pi.sh has been run on the Pi) the
# server restarts itself when server.py changes.
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

FILES=(LineBoard.html server.py remote.html start-board.sh setup-pi.sh hide-cursor.sh)

if command -v rsync >/dev/null; then
  # --checksum: compare contents, so unchanged files (and their timestamps) are left alone
  out=$(rsync --checksum --perms --itemize-changes "${FILES[@]}" "$PI:$DIR/") \
    || { echo "Copy failed - is the Pi on and on the same Wi-Fi?"; exit 1; }
  changed=$(printf '%s\n' "$out" | sed -n 's/^<f[^ ]* //p')
  if [ -n "$changed" ]; then printf '  updated: %s\n' $changed; else echo "  nothing changed"; fi
else
  scp "${FILES[@]}" "$PI:$DIR/" || { echo "Copy failed - is the Pi on and on the same Wi-Fi?"; exit 1; }
fi
echo "Deployed to $PI:~/$DIR"
