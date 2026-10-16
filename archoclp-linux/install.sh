#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/archoclp"
BIN="${XDG_BIN_HOME:-$HOME/.local/bin}"
mkdir -p "$DEST" "$BIN"
install -m 0755 "$ROOT/archoclp.py" "$DEST/archoclp.py"
install -m 0644 "$ROOT/archoclp_features.py" "$DEST/archoclp_features.py"
install -m 0644 "$ROOT/archoclp_gui.py" "$DEST/archoclp_gui.py"
install -m 0644 "$ROOT/models.json" "$DEST/models.json"
cat > "$BIN/archoclp" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec python3 "$DEST/archoclp.py" "\$@"
EOF
chmod 0755 "$BIN/archoclp"
printf 'Installed ArchOCLP command to %s/archoclp\n' "$BIN"
printf 'Make sure %s is in PATH.\n' "$BIN"

cat > "$BIN/archoclp-gui" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec python3 "$DEST/archoclp_gui.py" "\$@"
EOF
chmod 0755 "$BIN/archoclp-gui"
printf 'GUI launcher installed to %s/archoclp-gui (requires Tk: pacman -S tk).\n' "$BIN"

APPLICATIONS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPLICATIONS"
cat > "$APPLICATIONS/archoclp.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=ArchOCLP
Comment=Experimental OpenCore EFI builder and macOS installer downloader
Exec=$BIN/archoclp-gui
Icon=computer
Terminal=false
Categories=System;Utility;
StartupNotify=true
EOF
chmod 0644 "$APPLICATIONS/archoclp.desktop"
printf 'Desktop app shortcut installed to %s/archoclp.desktop.\n' "$APPLICATIONS"
