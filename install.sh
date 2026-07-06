#!/usr/bin/env bash
#
# fedora-init — run everything: ./install.sh
#               run one module: ./install.sh battery
#               no checkout:    curl -fsSL https://raw.githubusercontent.com/amamparo/fedora-init/main/install.sh | bash
#
set -euo pipefail

# Piped from curl (or a lone install.sh with no repo alongside): fetch the
# tarball into a temp dir, run from that copy, clean up afterwards.
# BASH_SOURCE is unset when bash reads the script from stdin, hence the :-.
script="${BASH_SOURCE[0]:-}"
if [[ ! -f $script || ! -d "$(dirname "$script")/modules" ]]; then
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    curl -fsSL https://github.com/amamparo/fedora-init/archive/main.tar.gz | tar xz -C "$tmp"
    bash "$tmp/fedora-init-main/install.sh" "$@"
    exit
fi

cd "$(dirname "$script")"
export REPO_ROOT="$PWD"

# Select modules: all by default, or any whose filename matches an argument.
mods=()
for m in modules/*.sh; do
    if (($# == 0)); then
        mods+=("$m")
    else
        for want in "$@"; do
            [[ $(basename "$m") == *"$want"* ]] && { mods+=("$m"); break; }
        done
    fi
done
((${#mods[@]})) || { echo "No module matches: $*" >&2; exit 1; }

sudo -v  # prompt for the password once, up front
# Keep the credential fresh: first-boot dnf downloads can outlast sudo's
# 5-minute cache, which would re-prompt (or abort) mid-run.
( while sleep 60; do sudo -n -v || exit; done ) &
SUDO_KEEPALIVE=$!
trap 'kill "$SUDO_KEEPALIVE" 2>/dev/null' EXIT

for m in "${mods[@]}"; do
    echo
    echo "==> ${m#modules/}"
    bash "$m"
done

echo
echo "Done. Log out and back in so GNOME picks up the extension."
