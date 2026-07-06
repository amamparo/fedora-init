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
# system-auth) — neither is touched. Takes effect at the next greeter start.
#
set -euo pipefail
REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# Fedora's gdm rpm ships /usr/share/dconf/profile/gdm (with system-db:gdm)
# already — never create /etc/dconf/profile/gdm, which would shadow the
# shipped profile and drop its file-db greeter defaults.
sudo install -D -m 0644 "$REPO_ROOT/files/gdm/10-disable-fingerprint-login" \
    /etc/dconf/db/gdm.d/10-disable-fingerprint-login
sudo install -D -m 0644 "$REPO_ROOT/files/gdm/10-disable-fingerprint-login.locks" \
    /etc/dconf/db/gdm.d/locks/10-disable-fingerprint-login

# dconf update recompiles a db only when the keyfile *directory* mtime is
# newer than the compiled db, and rewriting an existing file doesn't bump
# the directory mtime — touch it so re-runs stay deterministic.
sudo touch /etc/dconf/db/gdm.d
sudo dconf update

# Auto-login is the same failure class (no password typed, nothing to derive
# the keyring key from). This repo never enables it, but flag it if present.
if [[ -f /etc/gdm/custom.conf ]] &&
    grep -Eiq '^[[:space:]]*(AutomaticLogin|TimedLogin)Enable[[:space:]]*=[[:space:]]*true' /etc/gdm/custom.conf; then
    echo "note: GDM auto-login is enabled in /etc/gdm/custom.conf — the keyring will still start locked."
fi

echo "Boot login is password-only from the next boot; the keyring unlocks with it. Fingerprint still works for the lock screen, sudo, and polkit."
