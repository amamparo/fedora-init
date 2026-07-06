#!/usr/bin/env bash
#
# Login keyring: keep fingerprint login AND kill the "login keyring did not
# get unlocked" prompt — by removing the keyring's password. A fingerprint
# can never *unlock* the keyring (its encryption key is derived from the
# login password; a sensor match yields no key material), so instead the
# login keyring is blanked: gnome-keyring then stores it in plaintext and
# auto-unlocks it on every login, fingerprint or otherwise. The trade-off:
# secrets (Brave's Safe Storage key, account tokens) rest unencrypted in
# ~/.local/share/keyrings — acceptable under LUKS full-disk encryption,
# which already gates offline access to the same files.
#
# Blanking uses gnome-keyring's org.gnome.keyring
# InternalUnsupportedGuiltRiddenInterface (what gnome-initial-setup uses to
# seed the keyring); it needs the *current* keyring password once, so the
# module prompts for the login password. Runs as the desktop user — the
# daemon lives on the session bus.
#
# An earlier revision instead forced the GDM greeter to password-only; that
# drop-in is removed here so fingerprint login comes back — but only after
# the keyring is confirmed blank, so a failed blanking never resurrects the
# prompt (password login still unlocks an encrypted keyring via PAM).
#
set -euo pipefail

KEYRING="$HOME/.local/share/keyrings/login.keyring"
blank=0

# A blank-password keyring is stored as a text keyfile beginning with
# "[keyring]"; an encrypted one is binary beginning with "GnomeKeyring".
if [[ ! -f "$KEYRING" ]]; then
    echo "No login keyring yet — log in once with your password, then re-run this module."
elif [[ "$(head -c 9 "$KEYRING")" == "[keyring]" ]]; then
    echo "Login keyring already has no password."
    blank=1
else
    # /dev/tty, not stdin: under the curl|bash bootstrap stdin is the pipe.
    read -rs -p "Login password (removes the keyring's password so fingerprint logins stop prompting): " pw < /dev/tty
    echo
    # Password via environment, not argv (argv is visible in ps).
    if KEYRING_PW="$pw" python3 - <<'PY'
import os
from gi.repository import Gio, GLib

NAME, PATH = "org.freedesktop.secrets", "/org/freedesktop/secrets"
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

def call(iface, method, args):
    return bus.call_sync(NAME, PATH, iface, method, args,
                         None, Gio.DBusCallFlags.NONE, -1, None)

_, session = call("org.freedesktop.Secret.Service", "OpenSession",
                  GLib.Variant("(sv)", ("plain", GLib.Variant("s", "")))).unpack()

secret = lambda v: (session, b"", v, "text/plain")
call("org.gnome.keyring.InternalUnsupportedGuiltRiddenInterface",
     "ChangeWithMasterPassword",
     GLib.Variant("(o(oayays)(oayays))",
                  (f"{PATH}/collection/login",
                   secret(os.environ["KEYRING_PW"].encode()),
                   secret(b""))))
PY
    then
        unset pw
        echo "Login keyring password removed — it now auto-unlocks on every login."
        blank=1
    else
        unset pw
        echo "That password didn't match the login keyring (changed outside of login" >&2
        echo "at some point?). Nothing was modified — re-run with the old password," >&2
        echo "or reset the keyring in Seahorse." >&2
        exit 1
    fi
fi

# Retire the earlier greeter-password-only approach, once safe to do so.
if ((blank)); then
    removed=0
    for f in /etc/dconf/db/gdm.d/10-disable-fingerprint-login \
             /etc/dconf/db/gdm.d/locks/10-disable-fingerprint-login; do
        if [[ -e "$f" ]]; then
            sudo rm "$f"
            removed=1
        fi
    done
    if ((removed)); then
        sudo dconf update
        echo "Fingerprint login re-enabled at the login screen (was disabled by an earlier revision)."
    fi
fi
