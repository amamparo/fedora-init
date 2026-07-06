#!/usr/bin/env bash
#
# No overview at login: GNOME Shell (40+) opens the Activities overview at
# every session start and has no setting to turn that off. Install the
# bundled micro-extension (files/gnome/no-overview@amamparo) that hides it
# the moment startup completes. On GNOME 50 the overview still flashes
# briefly — extensions initialize after the startup animation begins, so
# hiding it is the best an extension can do.
#
set -euo pipefail
REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

UUID="no-overview@amamparo"
SRC="$REPO_ROOT/files/gnome/$UUID"
DEST="$HOME/.local/share/gnome-shell/extensions/$UUID"

# Mirror the extension into place, same guard as 20-window-snapping: wipe
# and recopy only when something differs, so unchanged re-runs skip the
# copy. No schemas/ in this extension, so no compile step (and nothing to
# --exclude from the diff).
if ! diff -rq "$SRC" "$DEST" >/dev/null 2>&1; then
    rm -rf "$DEST"
    mkdir -p "$DEST"
    cp -rT "$SRC" "$DEST"
fi

# Enable it. Same fallback as 20-window-snapping: `gnome-extensions enable`
# fails if the running shell hasn't scanned the new directory yet, so fall
# back to editing gsettings directly — either way it loads on next login.
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

echo "no-overview extension installed — after the next login, sessions land on the desktop (a brief overview flash is expected on GNOME 50)."
