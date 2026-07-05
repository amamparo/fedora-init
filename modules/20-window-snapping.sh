#!/usr/bin/env bash
#
# Window snapping: install the bundled "rectangle" GNOME Shell extension
# (Super+Alt+arrows, cycling 1/2 -> 2/3 -> 1/3) and bind Super+Alt+F to
# GNOME's native fullscreen toggle.
#
set -euo pipefail

UUID="rectangle@amamparo"
SRC="$REPO_ROOT/files/gnome/$UUID"
DEST="$HOME/.local/share/gnome-shell/extensions/$UUID"

mkdir -p "$DEST"
cp -rT "$SRC" "$DEST"
glib-compile-schemas "$DEST/schemas"

# Enable it. `gnome-extensions enable` fails if the running shell hasn't
# scanned the new directory yet, so fall back to editing gsettings directly —
# either way it loads on next login.
gnome-extensions enable "$UUID" 2>/dev/null || python3 - "$UUID" <<'PY'
import ast, subprocess, sys

uuid = sys.argv[1]
out = subprocess.run(
    ["gsettings", "get", "org.gnome.shell", "enabled-extensions"],
    capture_output=True, text=True, check=True,
).stdout.strip()
current = [] if out.startswith("@as") else ast.literal_eval(out)
if uuid not in current:
    current.append(uuid)
    subprocess.run(
        ["gsettings", "set", "org.gnome.shell", "enabled-extensions", str(current)],
        check=True,
    )
PY

# Super+Alt+F -> fullscreen (swap in 'toggle-maximized' if you prefer maximize)
gsettings set org.gnome.desktop.wm.keybindings toggle-fullscreen "['<Super><Alt>f']"

echo "Extension installed and enabled — takes effect after you log out and back in."
