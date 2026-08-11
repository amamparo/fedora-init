#!/usr/bin/python3
# Set the built-in panel's mode (resolution + refresh-rate mode) and scale via
# mutter's DisplayConfig D-Bus API. None of this is a gsettings key — it lives
# in monitors.xml, which mutter owns; ApplyMonitorsConfig method 2 persists
# there AND applies live. Must run as the desktop user (session bus), never
# under become.
#
# Variable refresh rate is NOT a separate flag: mutter exposes each mode twice,
# once fixed and once variable, distinguished by the mode's "refresh-rate-mode"
# property ("variable") and an id suffixed "+vrr" — selecting the mode IS
# enabling VRR, and monitors.xml records it as <ratemode>variable</ratemode>.
# So this module picks a mode by (width, height, refresh-rate-mode) rather than
# by id: ids embed the panel's exact refresh rate to three decimals
# ("1680x1050@59.954+vrr"), which is an EDID detail no playbook should hardcode.
#
# Applies only when the built-in panel ("is-builtin" in the monitor props — a
# bare logical-monitor count would misfire docked lid-closed) is the sole
# active display: positions are in scale-dependent logical pixels, and mutter
# *rejects* a re-scaled layout whose monitors then overlap ("Logical monitors
# overlap") rather than repositioning it. monitors.xml keys configs by the set
# of connected monitors, so a docked layout is a separate config arranged in
# Settings.
#
# Hardware variation is reported, never fatal (the play must survive a
# different panel): a missing resolution skips, and a missing variable-rate
# twin falls back to the fixed mode. A scale the chosen mode does not support
# IS fatal — by then the requested resolution matched, so we are on the
# intended hardware and an unsupported scale is a playbook bug worth surfacing
# instead of letting mutter reject it with an opaque error.
#
# Check mode calls only GetCurrentState (read-only) and reports would-change.

DOCUMENTATION = r"""
module: mutter_display_config
short_description: Set the built-in panel's mode and scale via mutter DisplayConfig
description:
  - Sets resolution, refresh-rate mode (fixed or variable) and scale through
    mutter's DisplayConfig D-Bus API (ApplyMonitorsConfig method 2 = persist to
    monitors.xml and apply live).
  - Selects the highest-refresh mode matching the requested resolution and
    refresh-rate mode; falls back to the fixed-rate twin when variable was
    asked for but the panel offers none.
  - Applies only when the built-in panel is the sole active display; other
    layouts are reported unchanged with guidance in the result message.
  - Must run as the desktop user on the session bus, never under become.
author:
  - fedora-init
options:
  width:
    description: Horizontal resolution of the target mode, in pixels.
    type: int
    required: true
  height:
    description: Vertical resolution of the target mode, in pixels.
    type: int
    required: true
  refresh_rate_mode:
    description:
      - Whether to select the variable-refresh-rate (adaptive sync) variant of
        the mode or the fixed-rate one. Mutter names the property this.
    type: str
    choices: [fixed, variable]
    default: fixed
  scale:
    description: Target scale factor for the built-in panel.
    type: float
    default: 1.0
"""

from ansible.module_utils.basic import AnsibleModule

# Two different comparisons, two tolerances. Matching a declared scale against
# supported_scales compares a YAML float to float32-promoted doubles
# (1.6666666269302368) whose neighbours are ~0.08 apart, so nearest-within-1e-3
# is unambiguous — at 1e-6 a declared 1.6666666 would fail against a list that
# plainly contains it. Comparing against mutter's own echoed logical scale, by
# contrast, should be exact.
SCALE_MATCH_TOL = 1e-3
SCALE_EPSILON = 1e-6


def main():
    module = AnsibleModule(
        argument_spec={
            "width": {"type": "int", "required": True},
            "height": {"type": "int", "required": True},
            "refresh_rate_mode": {
                "type": "str",
                "choices": ["fixed", "variable"],
                "default": "fixed",
            },
            "scale": {"type": "float", "default": 1.0},
        },
        supports_check_mode=True,
    )
    width = module.params["width"]
    height = module.params["height"]
    rate_mode = module.params["refresh_rate_mode"]
    target_scale = module.params["scale"]
    wanted = f"{width}x{height} ({rate_mode} refresh rate)"

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
                " display config. Set it in Settings > Displays, or re-run undocked.")

    x, y, current_scale, transform, primary, mons, _ = logicals[0]
    active = {conn for conn, *_ in mons}
    modes_by_connector = {info[0]: modes for info, modes, _ in monitors
                          if info[0] in active}

    # Pick one mode per active connector. Fixed-rate modes carry no
    # "refresh-rate-mode" property at all, so absent means "fixed".
    def pick(modes, mode_kind):
        matching = [m for m in modes
                    if m[1] == width and m[2] == height
                    and m[6].get("refresh-rate-mode", "fixed") == mode_kind]
        # Several modes can share a resolution at different rates (this panel
        # lists 1920x1200 at both 60.001 and 59.950 Hz) — take the fastest,
        # and break exact ties on id so the choice never depends on ordering.
        return max(matching, key=lambda m: (m[3], m[0]), default=None)

    chosen = {}
    degraded = False
    for conn, modes in modes_by_connector.items():
        mode = pick(modes, rate_mode)
        if mode is None and rate_mode == "variable":
            # The resolution exists but this panel offers no adaptive-sync
            # twin: apply it fixed rather than leaving the panel alone, and
            # say so (applied=False makes the reporting task print it).
            mode = pick(modes, "fixed")
            degraded = mode is not None
        if mode is None:
            module.exit_json(
                changed=False, applied=False,
                msg=f"The built-in panel offers no {wanted} mode — leaving the"
                    f" display alone. Available on {conn}: "
                    + ", ".join(sorted({f"{m[1]}x{m[2]}" for m in modes})))
        chosen[conn] = mode

    # Scale support is per mode, not per monitor (1920x1200 offers 1.3333 here,
    # 1680x1050 offers 1.75 instead), so validate against the modes actually
    # chosen and then apply mutter's own double — never the YAML float.
    for conn, mode in chosen.items():
        nearest = min(mode[5], key=lambda s: abs(s - target_scale), default=None)
        if nearest is None or abs(nearest - target_scale) > SCALE_MATCH_TOL:
            module.fail_json(
                msg=f"Scale {target_scale:g} is not supported at {wanted} on"
                    f" {conn}. Supported scales: "
                    + ", ".join(f"{s:g}" for s in mode[5]))
        target_scale = nearest

    current_mode = {
        info[0]: mode[0]
        for info, modes, _ in monitors
        for mode in modes
        if mode[6].get("is-current")
    }
    converged = (abs(current_scale - target_scale) < SCALE_EPSILON
                 and all(current_mode.get(conn) == mode[0]
                         for conn, mode in chosen.items()))

    one = next(iter(chosen.values()))
    described = (f"{one[1]}x{one[2]} @ {one[3]:.3f} Hz"
                 f" ({one[6].get('refresh-rate-mode', 'fixed')} refresh rate),"
                 f" scale {target_scale:g}")

    if converged:
        module.exit_json(changed=False, applied=not degraded,
                         msg=f"Built-in panel already at {described}."
                             + (f" No {wanted} mode exists on this panel."
                                if degraded else ""))

    if module.check_mode:
        module.exit_json(changed=True, applied=False,
                         msg=f"Would set the built-in panel to {described}.")

    # Empty per-monitor props. The dict's only implemented keys are
    # underscanning, color-mode and rgb-range, and monitors.xml currently
    # records none of them — but if HDR is ever switched on in Settings,
    # color-mode must be echoed back from the monitor props or this apply may
    # drop the panel to SDR. Note mutter silently ignores keys it doesn't know
    # (an "enable_vrr" would return success and do nothing), so a mistake here
    # is invisible: VRR lives in the mode id, never in this dict.
    try:
        call("ApplyMonitorsConfig", GLib.Variant(
            "(uua(iiduba(ssa{sv}))a{sv})",
            (serial, 2,
             [(x, y, target_scale, transform, primary,
               [(conn, chosen[conn][0], {}) for conn, *_ in mons])],
             {})))
    except GLib.Error as e:
        module.fail_json(msg=f"ApplyMonitorsConfig failed: {e}")

    module.exit_json(changed=True, applied=not degraded,
                     msg=f"Built-in panel set to {described}."
                         + (f" No {wanted} mode exists on this panel."
                            if degraded else ""))


if __name__ == "__main__":
    main()
