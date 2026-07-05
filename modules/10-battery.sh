#!/usr/bin/env bash
#
# Battery: replace Fedora's default power stack (tuned + tuned-ppd) with TLP.
#
# TLP tunes far more knobs out of the box (this is most of the Ubuntu-vs-Fedora
# battery gap) and adds ThinkPad charge thresholds. powertop is installed only
# as a measurement tool — TLP already applies its tunings, so no autotune
# service (the two would fight over the same sysfs knobs).
#
set -euo pipefail

# --allowerasing: tlp declares conflicts with tuned-ppd, let dnf resolve them
sudo dnf install -y --allowerasing tlp tlp-rdw powertop

# Make sure the competing daemons are gone / can't come back
sudo dnf remove -y tuned tuned-ppd power-profiles-daemon 2>/dev/null || true
sudo systemctl mask power-profiles-daemon.service 2>/dev/null || true

# Our overrides (charge thresholds etc.) on top of TLP's defaults
sudo install -D -m 0644 "$REPO_ROOT/files/tlp/00-battery.conf" /etc/tlp.d/00-battery.conf

# TLP owns radio device state; its docs require masking systemd-rfkill
sudo systemctl mask systemd-rfkill.service systemd-rfkill.socket

sudo systemctl enable --now tlp.service
sudo tlp start

echo "TLP active. Verify with: sudo tlp-stat -s   (audit drains with: sudo powertop)"
