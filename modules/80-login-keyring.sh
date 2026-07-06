#!/usr/bin/env bash
#
# Login keyring: make the boot login password-only so pam_gnome_keyring can
# unlock the GNOME login keyring. The keyring's encryption key is derived
# from the login password — a fingerprint match yields no key material, and
# /etc/pam.d/gdm-fingerprint has no pam_gnome_keyring line — so a swipe
# login leaves the keyring locked and GNOME prompts "The login keyring did
# not get unlocked..." the moment anything wants a secret (Brave's Safe
# Storage, Google account tokens).
#
# The fix is greeter-scoped: enable-fingerprint-authentication is written to
# GDM's *system* dconf db, which only the login screen reads
# (DCONF_PROFILE=gdm). Fingerprint keeps working at the in-session lock
# screen (user dconf) and for sudo/polkit (authselect's with-fingerprint in
# system-auth) — neither is touched. Takes effect at the next greeter start;
# logging out is enough (GDM spawns a fresh greeter), no reboot needed.
#
set -euo pipefail
REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# Fedora's gdm rpm ships /usr/share/dconf/profile/gdm (with system-db:gdm)
# already — never create /etc/dconf/profile/gdm, which would shadow the
# shipped profile and drop its file-db greeter defaults.
changed=0
if ! cmp -s "$REPO_ROOT/files/gdm/10-disable-fingerprint-login" \
        /etc/dconf/db/gdm.d/10-disable-fingerprint-login; then
    sudo install -D -m 0644 "$REPO_ROOT/files/gdm/10-disable-fingerprint-login" \
        /etc/dconf/db/gdm.d/10-disable-fingerprint-login
    changed=1
fi
if ! cmp -s "$REPO_ROOT/files/gdm/10-disable-fingerprint-login.locks" \
        /etc/dconf/db/gdm.d/locks/10-disable-fingerprint-login; then
    sudo install -D -m 0644 "$REPO_ROOT/files/gdm/10-disable-fingerprint-login.locks" \
        /etc/dconf/db/gdm.d/locks/10-disable-fingerprint-login
    changed=1
fi

# dconf update recompiles every system db unconditionally (the old mtime
# gate died in dconf's 2019 C rewrite), so only run it when a drop-in
# actually changed.
if ((changed)); then
    sudo dconf update
fi

# Auto-login is the same failure class (no password typed, nothing to derive
# the keyring key from). This repo never enables it, but flag it if present.
if [[ -f /etc/gdm/custom.conf ]] &&
    grep -Eiq '^[[:space:]]*(AutomaticLogin|TimedLogin)Enable[[:space:]]*=[[:space:]]*(true|1)[[:space:]]*$' /etc/gdm/custom.conf; then
    echo "note: GDM auto-login is enabled in /etc/gdm/custom.conf — the keyring will still start locked."
fi

echo "GDM login goes password-only at the next login screen (logging out is enough); the keyring unlocks with it. Fingerprint still works for the lock screen, sudo, and polkit."
