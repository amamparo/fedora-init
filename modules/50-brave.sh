#!/usr/bin/env bash
#
# Brave: daily browser, from Brave's official rpm repo. gpgcheck=1 with the
# key URL in the repo file, so dnf imports and verifies the key on install.
#
set -euo pipefail
REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

sudo install -D -m 0644 "$REPO_ROOT/files/brave/brave-browser.repo" /etc/yum.repos.d/brave-browser.repo
sudo dnf install -y brave-browser

echo "Brave installed."
