#!/usr/bin/env bash
#
# Window snapping: install the bundled "rectangle" GNOME Shell extension
# (Super+Alt+arrows, cycling 1/2 -> 2/3 -> 1/3) and bind Super+Alt+F to
# GNOME's native maximize toggle.
#
set -euo pipefail
REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

UUID="rectangle@amamparo"
SRC="$REPO_ROOT/files/gnome/$UUID"
DEST="$HOME/.local/share/gnome-shell/extensions/$UUID"

# Mirror the extension into place. A bare cp -rT would leave files deleted
# from the repo copy lingering in $DEST, so wipe and recopy — but only when
# something actually differs, so unchanged re-runs skip the copy and the
# schema recompile entirely (gschemas.compiled only exists in $DEST).
if ! diff -rq --exclude=gschemas.compiled "$SRC" "$DEST" >/dev/null 2>&1; then
    rm -rf "$DEST"
    mkdir -p "$DEST"
    cp -rT "$SRC" "$DEST"
    glib-compile-schemas "$DEST/schemas"
fi

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

# Super+Alt+F -> maximize: fills the work area but keeps the top bar, unlike
# fullscreen. Alt+F10 is toggle-maximized's stock binding — keep it. Prefer
# fullscreen? Set toggle-fullscreen to ['<Super><Alt>f'] alone (leave Alt+F10
# out; the reset then returns it to toggle-maximized) and point the reset
# below at toggle-maximized instead. The reset exists because an earlier
# revision bound toggle-fullscreen to this key, and a key shared by two
# actions hits the nondeterministic-conflict problem below.
gsettings set org.gnome.desktop.wm.keybindings toggle-maximized "['<Super><Alt>f', '<Alt>F10']"
gsettings reset org.gnome.desktop.wm.keybindings toggle-fullscreen

# GNOME's stock bindings collide with all four tiling keys: shift-overview
# up/down and switch-to-workspace left/right both default to Super+Alt+arrows,
# and Mutter resolves duplicates nondeterministically. Clear the overview pair
# and move workspace switching to Super+Alt+PageUp/PageDown.
gsettings set org.gnome.shell.keybindings shift-overview-up "[]"
gsettings set org.gnome.shell.keybindings shift-overview-down "[]"
gsettings set org.gnome.desktop.wm.keybindings switch-to-workspace-left "['<Super><Alt>Page_Up']"
gsettings set org.gnome.desktop.wm.keybindings switch-to-workspace-right "['<Super><Alt>Page_Down']"

echo "Extension installed and enabled — takes effect after you log out and back in."
