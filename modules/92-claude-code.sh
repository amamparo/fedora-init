#!/usr/bin/env bash
#
# Claude Code via Anthropic's native installer (the documented default:
# curl https://claude.ai/install.sh | bash). It puts the launcher at
# ~/.local/bin/claude — a symlink into ~/.local/share/claude/versions/ —
# and the binary self-updates in the background from then on, so the
# launcher's presence is the terminal state. The guard matters: the
# installer has no already-installed check and re-downloads the full
# ~260 MB binary on every run. ~/.local/bin is on PATH via files/zsh/zshrc.
#
# The installer is downloaded to a file first, then run: piping curl
# straight into bash can execute a truncated script when the download dies
# mid-stream. Runs as the desktop user — it writes only under $HOME, and
# its docs warn against sudo installs.
#
set -euo pipefail

# command -v too: an existing npm or dnf install also counts — the docs
# warn against stacking a second copy on top.
if [[ ! -x "$HOME/.local/bin/claude" ]] && ! command -v claude >/dev/null 2>&1; then
    installer="$(mktemp)"
    trap 'rm -f "$installer"' EXIT
    curl -fsSL -o "$installer" https://claude.ai/install.sh
    bash "$installer"
fi

echo "Claude Code installed — first run: 'claude' to sign in."
