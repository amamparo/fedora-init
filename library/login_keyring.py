#!/usr/bin/python3
# Blank the GNOME login keyring's password so it auto-unlocks on every login
# (fingerprint, password, or auto-login) and never prompts again. Uses
# gnome-keyring's org.gnome.keyring.InternalUnsupportedGuiltRiddenInterface
# (ChangeWithMasterPassword — internal but stable; gnome-initial-setup
# relies on it), which needs the *current* keyring password. Must run as the
# desktop user (the daemon lives on the session bus), never under become.
#
# State detection needs no D-Bus: a blank-password keyring is a *plaintext*
# keyfile starting with "[keyring]"; an encrypted one is binary starting
# with "GnomeKeyring". Check mode reads only that magic.
#
# The password argument travels as a no_log module arg over the local
# connection's stdin pipe — never argv, never the process environment.

DOCUMENTATION = r"""
module: login_keyring
short_description: Remove the GNOME login keyring's password (blank it)
description:
  - Blanks the login keyring's password via gnome-keyring's
    InternalUnsupportedGuiltRiddenInterface so it auto-unlocks on every
    login (fingerprint, password, or auto-login).
  - Detects state from the keyring file magic, so check mode needs no D-Bus
    and no password.
  - Must run as the desktop user on the session bus, never under become.
author:
  - fedora-init
options:
  password:
    description:
      - Current keyring password (the login password).
      - Required to apply the change; may be omitted in check mode.
    type: str
    no_log: true
"""

import os

from ansible.module_utils.basic import AnsibleModule

KEYRING = os.path.expanduser("~/.local/share/keyrings/login.keyring")


def keyring_state():
    """'absent' | 'blank' | 'encrypted'"""
    try:
        with open(KEYRING, "rb") as f:
            magic = f.read(12)
    except FileNotFoundError:
        return "absent"
    return "blank" if magic.startswith(b"[keyring]") else "encrypted"


def main():
    module = AnsibleModule(
        argument_spec={"password": {"type": "str", "no_log": True}},
        supports_check_mode=True,
    )

    state = keyring_state()
    if state == "absent":
        module.exit_json(
            changed=False, keyring="absent",
            msg="No login keyring yet — log in once with your password, then re-run.")
    if state == "blank":
        module.exit_json(changed=False, keyring="blank",
                         msg="Login keyring already has no password.")

    if module.check_mode:
        module.exit_json(changed=True, keyring="encrypted",
                         msg="Would remove the login keyring's password.")

    password = module.params["password"]
    if password is None:
        module.fail_json(msg="password is required to blank an encrypted keyring")

    try:
        from gi.repository import Gio, GLib
    except ImportError as e:
        module.fail_json(msg=f"python3-gobject is required: {e}")

    NAME, PATH = "org.freedesktop.secrets", "/org/freedesktop/secrets"
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        def call(iface, method, args):
            return bus.call_sync(NAME, PATH, iface, method, args,
                                 None, Gio.DBusCallFlags.NONE, -1, None)

        _, session = call("org.freedesktop.Secret.Service", "OpenSession",
                          GLib.Variant("(sv)", ("plain", GLib.Variant("s", "")))).unpack()

        secret = lambda v: (session, b"", v, "text/plain")  # noqa: E731
        call("org.gnome.keyring.InternalUnsupportedGuiltRiddenInterface",
             "ChangeWithMasterPassword",
             GLib.Variant("(o(oayays)(oayays))",
                          (f"{PATH}/collection/login",
                           secret(password.encode()),
                           secret(b""))))
    except GLib.Error:
        module.fail_json(
            keyring="encrypted",
            msg="That password didn't match the login keyring (changed outside"
                " of login at some point?). Nothing was modified — re-run with"
                " the old password, or reset the keyring in Seahorse.")

    if keyring_state() != "blank":
        module.fail_json(msg="ChangeWithMasterPassword returned but the keyring"
                             " is still encrypted — nothing further was changed.")

    module.exit_json(changed=True, keyring="blank",
                     msg="Login keyring password removed — it now auto-unlocks"
                         " on every login.")


if __name__ == "__main__":
    main()
