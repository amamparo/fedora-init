#!/usr/bin/env bash
#
# GNOME prefs: the handful of desktop settings that differ from stock
# Fedora. Runs gsettings/dconf/D-Bus as the desktop user — no sudo here.
#
set -euo pipefail

gsettings set org.gnome.desktop.interface color-scheme 'prefer-dark'
gsettings set org.gnome.desktop.interface show-battery-percentage true
gsettings set org.gnome.desktop.wm.preferences button-layout 'appmenu:minimize,maximize,close'
gsettings set org.gnome.shell favorite-apps '[]' # empty dock
gsettings set org.gnome.desktop.peripherals.touchpad speed 0.19

# Displays > Scale: 100% (Fedora defaults this panel to 125%). Monitor scale
# is not a gsettings key — it lives in monitors.xml, owned by mutter's
# DisplayConfig D-Bus API. Method 2 = persist to monitors.xml *and* apply
# live. Applied only when the laptop panel is the sole display: positions
# are in scale-dependent logical pixels and mutter *rejects* a re-scaled
# layout whose monitors then overlap ("Logical monitors overlap") rather
# than repositioning it. monitors.xml keys configs by the set of connected
# monitors anyway, so a docked layout is a separate config arranged in
# Settings.
python3 - <<'PY'
from gi.repository import Gio, GLib

NAME = "org.gnome.Mutter.DisplayConfig"
PATH = "/org/gnome/Mutter/DisplayConfig"

bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

def call(method, args=None):
    return bus.call_sync(NAME, PATH, NAME, method, args,
                         None, Gio.DBusCallFlags.NONE, -1, None)

serial, monitors, logicals, _ = call("GetCurrentState").unpack()

# Connectors backed by the built-in panel ("is-builtin" in monitor props).
builtin = {info[0] for info, _, props in monitors if props.get("is-builtin")}

if (len(logicals) != 1
        or not all(conn in builtin for conn, *_ in logicals[0][5])):
    print("The laptop panel isn't the only active display — skipping the"
          " 100% scale. Set it in Settings > Displays, or re-run undocked.")
    raise SystemExit
if logicals[0][2] == 1.0:
    print("Display scale already 100%.")
    raise SystemExit

# The mode id currently active on each connector, so only scale changes.
current_mode = {
    info[0]: mode[0]
    for info, modes, _ in monitors
    for mode in modes
    if mode[6].get("is-current")
}

x, y, _scale, transform, primary, mons, _ = logicals[0]
call("ApplyMonitorsConfig", GLib.Variant(
    "(uua(iiduba(ssa{sv}))a{sv})",
    (serial, 2,
     [(x, y, 1.0, transform, primary,
       [(conn, current_mode[conn], {}) for conn, *_ in mons])],
     {})))
print("Display scale set to 100%.")
PY

# Ptyxis terminal palette. The profile uuid only exists once Ptyxis has
# launched, so on a fresh install this may defer to a later re-run.
uuid="$(gsettings get org.gnome.Ptyxis default-profile-uuid 2>/dev/null | tr -d "'" || true)"
if [[ -n "$uuid" ]]; then
    dconf write "/org/gnome/Ptyxis/Profiles/$uuid/palette" "'Moonfly'"
else
    echo "Ptyxis hasn't launched yet — re-run this module later for the Moonfly palette."
fi

echo "GNOME prefs applied."
