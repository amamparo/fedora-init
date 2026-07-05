# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Keep documentation in sync

When a change alters behavior, commands, module conventions, keybindings, or gotchas, update the documentation in the same change — this file, README.md, and any module header comments that describe the affected behavior. README.md is the user-facing doc (what the modules do and how to run them); CLAUDE.md is the contributor-facing doc (architecture, conventions, gotchas). A PR that changes what a module does but not the docs is incomplete.

## What this is

Idempotent setup scripts for a fresh Fedora Workstation (GNOME) install, targeting Fedora 44 / GNOME 50. There is no build, lint, or test tooling — the only entry point is:

```sh
./install.sh            # run every module
./install.sh battery    # run modules whose filename contains a substring
```

**Running modules mutates the host system** (installs/removes dnf packages, masks systemd services, changes the login shell). Don't execute them to "test" a change unless the user asks; `bash -n modules/NN-name.sh` is the safe syntax check.

## Architecture

- `install.sh` — orchestrator. Exports `REPO_ROOT`, primes sudo once (`sudo -v`), then runs `modules/*.sh` in filename order. Arguments filter modules by filename substring.
- `modules/NN-name.sh` — one concern per module, executed in `NN` order. Conventions (enforced by review, not tooling):
  - `set -euo pipefail` and idempotent — every module must be safe to re-run
  - derive `REPO_ROOT` with the `${REPO_ROOT:-...}` fallback line (see any module) so the module also works standalone, not just via install.sh
  - call `sudo` per command (never assume a root shell); install.sh caches credentials and keeps them fresh with a background `sudo -n -v` loop
  - static assets live in `files/<name>/` and are referenced via `$REPO_ROOT`
- `files/` — assets the modules install verbatim (TLP drop-in config, zshrc, the GNOME extension).

## The GNOME extension

`files/gnome/rectangle@amamparo/` is a bundled, self-authored GNOME Shell extension (ESM, GNOME 48–51), not a vendored third-party one — edit it directly here. Parts that must stay in sync:

- Keybindings are declared in `schemas/*.gschema.xml` (the `as`-typed keys), consumed by name in `extension.js` (`KEYS`), and the schema is compiled by `modules/20-window-snapping.sh` at install time (`glib-compile-schemas`).
- `metadata.json`'s `shell-version` list gates which GNOME versions load it; `_unmaximize()` in extension.js carries a GNOME ≤48 vs ≥49 API fallback for the same reason.
- Wayland can't hot-reload GNOME Shell — any extension change takes effect only after logout/login.

## Gotchas encoded in the modules

- 10-battery: `tlp` declares `Conflicts: tuned` (`tuned-ppd` merely depends on tuned). The stock stack is removed *before* installing so its orphaned deps get swept in the remove transaction; the removal loop guards with `rpm -q` (exact name match) because `tlp-pd` *provides* `power-profiles-daemon` — a bare `dnf remove` would uninstall tlp-pd on every re-run. `tlp-pd` is what keeps GNOME's power-mode toggle alive. TLP's docs require masking `systemd-rfkill`. powertop is intentionally measurement-only — no autotune service, it would fight TLP over the same sysfs knobs.
- 20-window-snapping: `gnome-extensions enable` fails if the running shell hasn't scanned the new extension dir yet; the module falls back to editing the `enabled-extensions` gsettings list directly. GNOME's stock bindings collide with all four tiling keys (`shift-overview-up/down` defaults to Super+Alt+Up/Down; `switch-to-workspace-left/right` includes Super+Alt+Left/Right) and Mutter resolves duplicates nondeterministically — the module clears the overview pair and rebinds workspace switching to Super+Alt+PageUp/PageDown. Check both `org.gnome.shell.keybindings` and `org.gnome.desktop.wm.keybindings` schemas before adding any new binding.
- 30-zsh: oh-my-zsh is installed via shallow `git clone`, not the curl|sh installer — a failed download must abort loudly (`sh -c "$(curl ...)"` silently no-ops when curl fails). The guard checks for `oh-my-zsh.sh` (payload), not the directory, so a broken half-install gets recloned.
- files/tlp/00-battery.conf deliberately contains only non-default TLP settings; keep it minimal rather than restating defaults.
- 40-multimedia: `dnf swap` is not idempotent — it fails once the from-package is gone — so `swap_pkg` guards on the target with `rpm -q` and falls back to plain install. The RPM Fusion release rpms are guarded the same way. The driver choice (intel-media-driver) is hardware-specific: this is the Intel Lunar Lake T14s Gen 6, not AMD.
- 60-gnome-prefs: gsettings/dconf must run as the desktop user, never under sudo (that would write root's dconf). The Ptyxis profile uuid doesn't exist until Ptyxis first launches, so the palette write is guarded and may need a re-run.
