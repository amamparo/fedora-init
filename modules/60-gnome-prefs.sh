#!/usr/bin/env bash
#
# GNOME prefs: the handful of desktop settings that differ from stock
# Fedora. Runs gsettings/dconf as the desktop user — no sudo here.
#
set -euo pipefail

gsettings set org.gnome.desktop.interface color-scheme 'prefer-dark'
gsettings set org.gnome.desktop.interface show-battery-percentage true
gsettings set org.gnome.desktop.wm.preferences button-layout 'appmenu:minimize,maximize,close'
gsettings set org.gnome.shell favorite-apps '[]' # empty dock
gsettings set org.gnome.desktop.peripherals.touchpad speed 0.19

# Ptyxis terminal palette. The profile uuid only exists once Ptyxis has
# launched, so on a fresh install this may defer to a later re-run.
uuid="$(gsettings get org.gnome.Ptyxis default-profile-uuid | tr -d "'")"
if [[ -n "$uuid" ]]; then
    dconf write "/org/gnome/Ptyxis/Profiles/$uuid/palette" "'Moonfly'"
else
    echo "Ptyxis hasn't launched yet — re-run this module later for the Moonfly palette."
fi

echo "GNOME prefs applied."
