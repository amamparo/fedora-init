#!/usr/bin/env bash
#
# Multimedia: RPM Fusion + full codec stack. Stock Fedora ships a
# codec-stripped Intel media driver and ffmpeg-free, so there is NO
# H.264/HEVC hardware video decode out of the box — a real battery cost on
# video calls and streaming. Swap in the full builds from RPM Fusion.
#
set -euo pipefail

if ! rpm -q rpmfusion-free-release rpmfusion-nonfree-release >/dev/null 2>&1; then
    sudo dnf install -y \
        "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm" \
        "https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm"
fi

# dnf swap is not idempotent (it fails once the from-package is gone), so
# guard on the target and fall back to a plain install if the source is
# already absent.
swap_pkg() { # swap_pkg <from> <to>
    rpm -q "$2" >/dev/null 2>&1 && return 0
    if rpm -q "$1" >/dev/null 2>&1; then
        sudo dnf swap -y "$1" "$2" --allowerasing
    else
        sudo dnf install -y "$2" --allowerasing
    fi
}

# Codec-capable Intel media driver (this is an Intel Lunar Lake ThinkPad)
swap_pkg libva-intel-media-driver intel-media-driver
# Full ffmpeg for GStreamer/mpv/GNOME Videos consumers
swap_pkg ffmpeg-free ffmpeg

echo "Codecs installed. Verify hw decode with: sudo dnf install libva-utils && vainfo | grep -E 'H264|HEVC'"
