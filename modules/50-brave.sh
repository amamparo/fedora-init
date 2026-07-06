#!/usr/bin/env bash
#
# Brave: the only browser on this machine, from Brave's official rpm repo.
# gpgcheck=1 with the key URL in the repo file, so dnf imports and verifies
# the key on install. Sets Brave as the default browser and removes every
# other browser (Fedora ships Firefox).
#
set -euo pipefail
REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

if ! cmp -s "$REPO_ROOT/files/brave/brave-browser.repo" /etc/yum.repos.d/brave-browser.repo; then
    sudo install -D -m 0644 "$REPO_ROOT/files/brave/brave-browser.repo" /etc/yum.repos.d/brave-browser.repo
fi
if ! rpm -q brave-browser >/dev/null 2>&1; then
    sudo dnf install -y brave-browser
fi

# Default browser for the desktop user — no sudo here (xdg-settings writes
# ~/.config/mimeapps.list; under sudo it would set root's default instead).
if [[ "$(xdg-settings get default-web-browser 2>/dev/null || true)" != "brave-browser.desktop" ]]; then
    xdg-settings set default-web-browser brave-browser.desktop
fi

# Remove the other browsers, after the default points at Brave so http/https
# handlers never dangle. Guard with rpm -q: dnf5 errors out (rather than
# no-ops) when asked to remove a package that isn't installed.
for p in firefox chromium epiphany google-chrome-stable; do
    if rpm -q "$p" >/dev/null 2>&1; then
        sudo dnf remove -y "$p"
    fi
done

echo "Brave installed and set as the default browser."
