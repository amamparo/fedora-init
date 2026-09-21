#!/usr/bin/env bash
#
# fedora-init — shared Bitwarden CLI helpers, sourced (not run) by install.sh
# and scripts/seed-bitwarden.sh. Functions only: sourcing this file has no
# side effects. Both callers own an EXIT trap that must `rm -rf "${bw_tmp:-}"`
# (a download a failure may have stranded) and `bw lock` when
# bw_session_opened is set — a session THIS run opened, locked even when the
# run aborts (locking only on the success path would leave it valid exactly
# when a failed run stranded it), but never one inherited from the shell.

# bw is a single static binary (~45 MB, no rpm exists); unzipped via python3
# (always present — ansible runs on it) to skip an unzip rpm. Presence-guarded
# in ~/.local/bin and never upgraded after, the reaper class. Puts
# ~/.local/bin on PATH for the caller too, which is how a fresh install is
# found on the very next line.
bw_ensure_installed() {
    export PATH="$HOME/.local/bin:$PATH"
    command -v bw >/dev/null 2>&1 && return 0
    echo "Installing the Bitwarden CLI to ~/.local/bin/bw..."
    bw_tmp="$(mktemp -d)"
    curl -fsSL 'https://bitwarden.com/download/?app=cli&platform=linux' -o "$bw_tmp/bw.zip"
    python3 -m zipfile -e "$bw_tmp/bw.zip" "$bw_tmp"
    install -D -m 0755 "$bw_tmp/bw" "$HOME/.local/bin/bw"
    rm -rf "$bw_tmp"
    bw_tmp=""
}

# Open a vault session on the terminal, leaving its key in BW_SESSION
# (exported; empty on failure — never fatal, the caller decides) and syncing
# the vault. Sets the variable directly rather than printing it, because a
# `$(...)` caller would run this in a subshell and lose the flags below. A
# BW_SESSION already in the environment that still unlocks the vault is
# kept as it is and bw_session_opened stays empty — the caller's EXIT trap
# must lock only when that flag is set, so a session the user exported for
# their own bw work survives a run that merely borrowed it. Sign-in state
# persists in ~/.config/Bitwarden CLI (vault items stay encrypted there;
# treat the auth tokens like the blanked login keyring — LUKS is the
# at-rest story), so a fresh session is email + master password + TOTP the
# first time, master password alone after. The Bitwarden cloud can
# additionally demand the personal API key client_secret (its bot check —
# web vault > Settings > Security > Keys). $1 says what the session is for,
# in the prompt.
#
# THE SYNC IS PART OF OPENING: `bw unlock` only derives the key locally, so
# it succeeds even when the persisted login's refresh token has expired or
# been revoked — the first server call is what fails, with `invalid_grant`
# (seen live 2026-09-20: unlock fine, `bw sync` dead, every edit would have
# died the same way). That case is a re-sign-in (`bw logout`, then `bw
# login`), done here so both callers get it. Any OTHER sync failure (no
# network) is reported through bw_synced and left to the caller: install.sh
# can still read a cached vault, the seed script cannot edit one.
bw_session_opened=""
bw_synced=""
bw_open_session() {
    if [[ -n ${BW_SESSION:-} ]] && bw unlock --check >/dev/null 2>&1; then
        :
    else
        bw_session_opened=1
        if bw login --check >/dev/null 2>&1; then
            echo "Bitwarden unlock ($1):"
            BW_SESSION="$(bw unlock --raw)" || BW_SESSION=""
        else
            echo "Bitwarden sign-in ($1):"
            BW_SESSION="$(bw login --raw)" || BW_SESSION=""
        fi
        export BW_SESSION
    fi
    [[ -n ${BW_SESSION:-} ]] || return 0
    local out
    if out="$(bw sync 2>&1)"; then
        bw_synced=1
    elif [[ $out == *invalid_grant* ]]; then
        echo "Bitwarden's stored sign-in has expired (invalid_grant) — signing in again:"
        bw logout >/dev/null 2>&1 || true
        bw_session_opened=1
        BW_SESSION="$(bw login --raw)" || BW_SESSION=""
        export BW_SESSION
        if [[ -n $BW_SESSION ]] && bw sync >/dev/null 2>&1; then
            bw_synced=1
        fi
    else
        echo "warning: Bitwarden sync failed (offline?) — using the cached vault: ${out%%$'\n'*}" >&2
    fi
}
