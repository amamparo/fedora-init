#!/usr/bin/python3
# Set the display scale of the built-in panel via mutter's DisplayConfig
# D-Bus API. Monitor scale is not a gsettings key — it lives in monitors.xml,
# which mutter owns; ApplyMonitorsConfig method 2 persists there AND applies
# live. Must run as the desktop user (session bus), never under become.
#
# Applies only when the built-in panel ("is-builtin" in the monitor props —
# a bare logical-monitor count would misfire docked lid-closed) is the sole
# active display: positions are in scale-dependent logical pixels, and
# mutter *rejects* a re-scaled layout whose monitors then overlap ("Logical
# monitors overlap") rather than repositioning it. monitors.xml keys configs
# by the set of connected monitors, so a docked layout is a separate config
# arranged in Settings.
#
# Check mode calls only GetCurrentState (read-only) and reports would-change.

DOCUMENTATION = r"""
module: mutter_display_scale
short_description: Set the built-in panel's display scale via mutter DisplayConfig
description:
  - Sets the display scale through mutter's DisplayConfig D-Bus API
    (ApplyMonitorsConfig method 2 = persist to monitors.xml and apply live).
  - Applies only when the built-in panel is the sole active display; other
    layouts are reported unchanged with guidance in the result message.
  - Must run as the desktop user on the session bus, never under become.
author:
  - fedora-init
options:
  scale:
    description: Target scale factor for the built-in panel.
    type: float
    default: 1.0
"""

from ansible.module_utils.basic import AnsibleModule


def main():
    module = AnsibleModule(
        argument_spec={"scale": {"type": "float", "default": 1.0}},
        supports_check_mode=True,
    )
    target = module.params["scale"]

    try:
        from gi.repository import Gio, GLib
    except ImportError as e:
        module.fail_json(msg=f"python3-gobject is required: {e}")

    NAME = "org.gnome.Mutter.DisplayConfig"
    PATH = "/org/gnome/Mutter/DisplayConfig"

    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        def call(method, args=None):
            return bus.call_sync(NAME, PATH, NAME, method, args,
                                 None, Gio.DBusCallFlags.NONE, -1, None)

        serial, monitors, logicals, _ = call("GetCurrentState").unpack()
    except GLib.Error as e:
        module.fail_json(msg=f"mutter DisplayConfig unavailable (not in a GNOME session?): {e}")

    # Connectors backed by the built-in panel.
    builtin = {info[0] for info, _, props in monitors if props.get("is-builtin")}

    if (len(logicals) != 1
            or not all(conn in builtin for conn, *_ in logicals[0][5])):
        module.exit_json(
            changed=False, applied=False,
            msg="The laptop panel isn't the only active display — skipping the"
                " scale change. Set it in Settings > Displays, or re-run undocked.")

    if logicals[0][2] == target:
        module.exit_json(changed=False, applied=True,
                         msg=f"Display scale already {target:g}.")

    if module.check_mode:
        module.exit_json(changed=True, applied=False,
                         msg=f"Would set display scale to {target:g}.")

    # The mode id currently active on each connector, so only scale changes.
    current_mode = {
        info[0]: mode[0]
        for info, modes, _ in monitors
        for mode in modes
        if mode[6].get("is-current")
    }

    x, y, _scale, transform, primary, mons, _ = logicals[0]
    try:
        call("ApplyMonitorsConfig", GLib.Variant(
            "(uua(iiduba(ssa{sv}))a{sv})",
            (serial, 2,
             [(x, y, target, transform, primary,
               [(conn, current_mode[conn], {}) for conn, *_ in mons])],
             {})))
    except GLib.Error as e:
        module.fail_json(msg=f"ApplyMonitorsConfig failed: {e}")

    module.exit_json(changed=True, applied=True,
                     msg=f"Display scale set to {target:g}.")


if __name__ == "__main__":
    main()
