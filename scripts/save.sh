#!/usr/bin/env bash
#
# fedora-init — `just save`: capture the live configs dotfiles/manifest.yml
# allowlists into the roles that own them, scan, commit, push. Thin wrapper
# over scripts/dotfiles.py; the only thing it adds is the Bitwarden session,
# because secret files (licence keys) go to the vault, never to git, and
# bw's prompts need a terminal.
#
#   just save                  scan, show the diff, ask, commit, push
#   just save --dry-run        show what would change; writes nothing
#   just save --yes            no question before commit + push
#   just save --no-vault       leave Bitwarden alone
#   just save --only ID        one manifest item (repeatable)
#   (dotfiles.py save --help lists the rest)
#
# A vault session opens ONLY when a secret file's hash differs from the one
# last vaulted (no prompt on an ordinary save), and never on --dry-run. Same
# lifecycle rule as install.sh and seed-bitwarden.sh: a session this run
# opened is locked on exit even when the save aborts; one inherited from the
# shell is borrowed and left unlocked.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/bw-lib.sh

for tool in python3 git; do
    command -v "$tool" >/dev/null 2>&1 || { echo "$tool is required" >&2; exit 1; }
done

cleanup() {
    [[ -z ${bw_tmp:-} ]] || rm -rf "$bw_tmp"
    [[ -z ${bw_session_opened:-} ]] || bw lock >/dev/null 2>&1 || true
}
trap cleanup EXIT

dry=0 vault=1 only=()
prev=""
for arg in "$@"; do
    case $arg in
        --dry-run) dry=1 ;;
        --no-vault) vault=0 ;;
    esac
    [[ $prev == --only ]] && only+=(--only "$arg")
    prev=$arg
done

if ((vault && !dry)) && python3 scripts/dotfiles.py vault-needed "${only[@]}"; then
    bw_ensure_installed
    bw_open_session "saving licence/secret files to the vault"
    [[ -n ${BW_SESSION:-} ]] || { echo "Bitwarden sign-in failed — nothing saved (use --no-vault to skip secret files)." >&2; exit 1; }
    # Edits go to the server, so a cached vault is no use here (the
    # seed-bitwarden.sh rule).
    [[ -n $bw_synced ]] || { echo "The vault could not be synced (offline?) — nothing saved." >&2; exit 1; }
fi

# Not exec: the EXIT trap above must still run to lock a session this run opened.
python3 scripts/dotfiles.py save "$@"
