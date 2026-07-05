#!/usr/bin/env bash
#
# Battery: replace Fedora's default power stack (tuned + tuned-ppd) with TLP.
#
# TLP tunes far more knobs out of the box (this is most of the Ubuntu-vs-Fedora
# battery gap) and adds ThinkPad charge thresholds. tlp-pd keeps GNOME's
# power-mode toggle working by serving the power-profiles D-Bus API with TLP
# as the backend. powertop is installed only as a measurement tool — TLP
# already applies its tunings, so no autotune service (the two would fight
# over the same sysfs knobs).
#
set -euo pipefail
REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# Remove the stock stack first so its now-unused deps get swept in the same
# transaction. Guard with rpm -q (exact name match): a bare `dnf remove
# power-profiles-daemon` would also match tlp-pd, which *provides* that name.
for p in tuned tuned-ppd power-profiles-daemon; do
    if rpm -q "$p" >/dev/null 2>&1; then
        sudo dnf remove -y "$p"
    fi
done
sudo systemctl mask power-profiles-daemon.service

# --allowerasing: tlp declares Conflicts: tuned; let dnf resolve any leftovers
sudo dnf install -y --allowerasing tlp tlp-rdw tlp-pd powertop

# Our overrides (charge thresholds etc.) on top of TLP's defaults
sudo install -D -m 0644 "$REPO_ROOT/files/tlp/00-battery.conf" /etc/tlp.d/00-battery.conf

# TLP owns radio device state; its docs require masking systemd-rfkill
sudo systemctl mask systemd-rfkill.service systemd-rfkill.socket

sudo systemctl enable --now tlp.service tlp-pd.service
sudo tlp start

echo "TLP active. Verify with: sudo tlp-stat -s   (audit drains with: sudo powertop)"
