#!/bin/bash
# Hide the mouse pointer on the Pi's desktop (for a TV with no mouse attached).
#   bash hide-cursor.sh        # hide it (takes effect after a reboot)
#   bash hide-cursor.sh off    # bring the normal pointer back
#
# Installs a pointer theme whose cursors are transparent and selects it in
# the labwc desktop's environment file. Nothing outside your home folder changes.

THEME=lineboard-blank
ENV_FILE="$HOME/.config/labwc/environment"
THEME_DIR="$HOME/.icons/$THEME"

if [ "$1" = "off" ]; then
  [ -f "$ENV_FILE" ] && sed -i "/^XCURSOR_THEME=$THEME\$/d" "$ENV_FILE"
  echo "Normal pointer restored - reboot to apply."
  exit 0
fi

mkdir -p "$THEME_DIR/cursors"
printf '[Icon Theme]\nName=%s\nComment=Invisible pointer for the line board TV\n' "$THEME" > "$THEME_DIR/index.theme"

# one transparent 1x1 Xcursor image, written directly (no extra packages needed)
python3 - "$THEME_DIR/cursors/default" <<'EOF'
import struct, sys
IMAGE = 0xfffd0002
size = 24
header = struct.pack('<4sIII', b'Xcur', 16, 0x10000, 1)
toc = struct.pack('<III', IMAGE, size, 16 + 12)
image = struct.pack('<IIIIIIIII', 36, IMAGE, size, 1, 1, 1, 0, 0, 0) + struct.pack('<I', 0)
open(sys.argv[1], 'wb').write(header + toc + image)
EOF

# every common pointer name → the same invisible cursor
cd "$THEME_DIR/cursors" || exit 1
for name in left_ptr arrow top_left_arrow pointer hand1 hand2 text xterm ibeam wait watch \
            progress left_ptr_watch crosshair cross grab grabbing openhand closedhand move fleur \
            all-scroll not-allowed no-drop copy alias context-menu help question_arrow cell \
            vertical-text col-resize row-resize ew-resize ns-resize nesw-resize nwse-resize \
            n-resize s-resize e-resize w-resize ne-resize nw-resize se-resize sw-resize \
            sb_h_double_arrow sb_v_double_arrow size_hor size_ver size_all zoom-in zoom-out; do
  ln -sf default "$name"
done

mkdir -p "$(dirname "$ENV_FILE")"
touch "$ENV_FILE"
sed -i '/^XCURSOR_THEME=/d' "$ENV_FILE"
echo "XCURSOR_THEME=$THEME" >> "$ENV_FILE"
echo "Pointer hidden - reboot to apply. (Undo: bash hide-cursor.sh off)"
