#!/usr/bin/env bash
#
# Visual Studio Code from Microsoft's official rpm repo. The repo file is
# the content code.visualstudio.com/docs/setup/linux documents, verbatim —
# autorefresh/type are zypper-isms dnf5 silently ignores; keep them so the
# file stays diffable against the docs. gpgcheck=1 with the key URL in the
# repo file, so dnf imports and verifies Microsoft's key on install, same
# as 50-brave.
#
set -euo pipefail
REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

if ! cmp -s "$REPO_ROOT/files/vscode/vscode.repo" /etc/yum.repos.d/vscode.repo; then
    sudo install -D -m 0644 "$REPO_ROOT/files/vscode/vscode.repo" /etc/yum.repos.d/vscode.repo
fi

# The docs precede this with `dnf check-update`, deliberately dropped here:
# it exits 100 whenever any updates are available, which aborts a set -e
# script. dnf5 refreshes metadata on install anyway, and once the repo is
# in place new VS Code releases arrive via normal `dnf upgrade`.
if ! rpm -q code >/dev/null 2>&1; then
    sudo dnf install -y code
fi

echo "VS Code installed."
