#!/usr/bin/env bash
#
# fedora-init — run everything: ./install.sh
#               run one module: ./install.sh battery
#
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
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

for m in "${mods[@]}"; do
    echo
    echo "==> ${m#modules/}"
    bash "$m"
done

echo
echo "Done. Log out and back in so GNOME picks up the extension."
