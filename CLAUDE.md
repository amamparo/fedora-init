# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Keep documentation in sync

When a change alters behavior, commands, module conventions, keybindings, or gotchas, update the documentation in the same change — this file, README.md, and any module header comments that describe the affected behavior. README.md is the user-facing doc (what the modules do and how to run them); CLAUDE.md is the contributor-facing doc (architecture, conventions, gotchas). A PR that changes what a module does but not the docs is incomplete.

## What this is

Idempotent setup scripts for a fresh Fedora Workstation (GNOME) install, targeting Fedora 44 / GNOME 50. There is no build, lint, or test tooling — the only entry point is:

```sh
./install.sh            # run every module
./install.sh battery    # run modules whose filename contains a substring
curl -fsSL https://raw.githubusercontent.com/amamparo/fedora-init/main/install.sh | bash   # no checkout
```

**Running modules mutates the host system** (installs/removes dnf packages, masks systemd services, changes the login shell). Don't execute them to "test" a change unless the user asks; `bash -n modules/NN-name.sh` is the safe syntax check.

## Architecture

- `install.sh` — orchestrator. If it can't find `modules/` next to itself (piped from curl, or a stray lone copy), it bootstraps: downloads the GitHub tarball to a temp dir, re-runs from that copy (forwarding any arguments), and removes it on exit. Otherwise it exports `REPO_ROOT`, primes sudo once (`sudo -v`), then runs `modules/*.sh` in filename order. Arguments filter modules by filename substring.
- `modules/NN-name.sh` — one concern per module, executed in `NN` order. Conventions (enforced by review, not tooling):
  - `set -euo pipefail` and idempotent — every module must be safe *and cheap* to re-run: guard dnf transactions with `rpm -q` and file installs with `cmp -s`/`diff -rq` so an unchanged re-run does no network work and only applies what changed in the repo since the last run
  - derive `REPO_ROOT` with the `${REPO_ROOT:-...}` fallback line (see any module) so the module also works standalone, not just via install.sh
  - call `sudo` per command (never assume a root shell); install.sh caches credentials and keeps them fresh with a background `sudo -n -v` loop
  - static assets live in `files/<name>/` and are referenced via `$REPO_ROOT`
- `files/` — assets the modules install verbatim (TLP drop-in config, zshrc, the GNOME extensions).

## The GNOME extensions

`files/gnome/` holds two bundled, self-authored GNOME Shell extensions (ESM, GNOME 48–51), not vendored third-party ones — edit them directly here:

- `rectangle@amamparo` (installed by `modules/20-window-snapping.sh`). Parts that must stay in sync: keybindings are declared in `schemas/*.gschema.xml` (the `as`-typed keys), consumed by name in `extension.js` (`KEYS`), and the schema is compiled at install time (`glib-compile-schemas`). `_unmaximize()` carries a GNOME ≤48 vs ≥49 API fallback.
- `no-overview@amamparo` (installed by `modules/70-no-overview.sh`) — hides the Activities overview that GNOME opens at session start. No settings schema, so its module has no `glib-compile-schemas` step.

For both: `metadata.json`'s `shell-version` list gates which GNOME versions load it, and Wayland can't hot-reload GNOME Shell — any extension change takes effect only after logout/login.

## Gotchas encoded in the modules

- 10-battery: `tlp` declares `Conflicts: tuned` (`tuned-ppd` merely depends on tuned). The stock stack is removed *before* installing so its orphaned deps get swept in the remove transaction; the removal loop guards with `rpm -q` (exact name match) because `tlp-pd` *provides* `power-profiles-daemon` — a bare `dnf remove` would uninstall tlp-pd on every re-run. `tlp-pd` is what keeps GNOME's power-mode toggle alive. TLP's docs require masking `systemd-rfkill`. powertop is intentionally measurement-only — no autotune service, it would fight TLP over the same sysfs knobs.
- 20-window-snapping: `gnome-extensions enable` fails if the running shell hasn't scanned the new extension dir yet; the module falls back to editing the `enabled-extensions` gsettings list directly. GNOME's stock bindings collide with all four tiling keys (`shift-overview-up/down` defaults to Super+Alt+Up/Down; `switch-to-workspace-left/right` includes Super+Alt+Left/Right) and Mutter resolves duplicates nondeterministically — the module clears the overview pair and rebinds workspace switching to Super+Alt+PageUp/PageDown. Check both `org.gnome.shell.keybindings` and `org.gnome.desktop.wm.keybindings` schemas before adding any new binding. Super+Alt+F is GNOME's native `toggle-maximized`, bound *alongside* its stock Alt+F10 (a bare set would silently drop it); the module also resets `toggle-fullscreen` because an earlier revision bound that to the same key, and machines that ran it would otherwise have two actions on one key.
- 30-zsh: oh-my-zsh is installed via shallow `git clone`, not the curl|sh installer — a failed download must abort loudly (`sh -c "$(curl ...)"` silently no-ops when curl fails). The guard checks for `oh-my-zsh.sh` (payload), not the directory, so a broken half-install gets recloned.
- files/tlp/00-battery.conf deliberately contains only non-default TLP settings; keep it minimal rather than restating defaults.
- 40-multimedia: `dnf swap` is not idempotent — it fails once the from-package is gone — so `swap_pkg` guards on the target with `rpm -q` and falls back to plain install. The RPM Fusion release rpms are guarded the same way. The driver choice (intel-media-driver) is hardware-specific: this is the Intel Lunar Lake T14s Gen 6, not AMD.
- 50-brave: `xdg-settings` must run as the desktop user, never under sudo (it writes `~/.config/mimeapps.list`). The browser-removal loop guards with `rpm -q` because dnf5 errors out — it doesn't no-op — when asked to remove a package that isn't installed, which would break re-runs. The default is set *before* the removals so the http/https handlers never dangle.
- 60-gnome-prefs: gsettings/dconf must run as the desktop user, never under sudo (that would write root's dconf). The Ptyxis profile uuid doesn't exist until Ptyxis first launches, so the palette write is guarded and may need a re-run. Display scale is not a gsettings key: it lives in monitors.xml, owned by mutter's `org.gnome.Mutter.DisplayConfig` D-Bus API (`ApplyMonitorsConfig` method 2 = persist + apply live). The helper applies only when the built-in panel (`is-builtin` in the monitor props — a bare logical-monitor count would misfire docked lid-closed) is the sole active display: positions are in scale-dependent logical pixels, and mutter *rejects* a config whose re-scaled monitors overlap ("Logical monitors overlap") rather than repositioning it — which under `set -e` would abort the whole run. monitors.xml keys configs by the set of connected monitors, so the docked layout is a separate config anyway.
- 70-no-overview: opening the overview at login is hardcoded in gnome-shell — no gsettings key exists (upstream gnome-shell!4009 would add one; unmerged as of GNOME 50). Since GNOME 50, extensions initialize *after* the startup animation has begun, so the overview can only be hidden once `startup-complete` fires (a brief flash is unavoidable); the `sessionMode.hasOverview` override that older extensions used no longer works. Don't add a second hide-overview mechanism (e.g. dash-to-dock's setting) alongside this one — running two is a reported crash source.
- 80-login-keyring: the login keyring's key is derived from the login password, so fingerprint and auto-login can never unlock it (`/etc/pam.d/gdm-fingerprint` has no `pam_gnome_keyring` line; the exact prompt wording is diagnostic — "did not get unlocked" means PAM never tried, "no longer matches" means it tried and the keyring password is stale). The chosen fix blanks the keyring password via gnome-keyring's `org.gnome.keyring.InternalUnsupportedGuiltRiddenInterface` (`ChangeWithMasterPassword`; internal but stable — gnome-initial-setup relies on it), which needs the *current* password: the module prompts, reading from `/dev/tty` because under the curl|bash bootstrap stdin is the exhausted pipe, and passes it via environment, not argv. Idempotency probe: a blank-password keyring is a *plaintext* keyfile starting with `[keyring]`, an encrypted one is binary starting with `GnomeKeyring` — no D-Bus needed to detect the current state. The module also cleans up an earlier revision's greeter drop-ins (`/etc/dconf/db/gdm.d/10-disable-fingerprint-login` + its lock), gated on the keyring being blank first, so a failed blanking never restores fingerprint login while the keyring still needs a password. Rejected alternatives, for the record: greeter password-only (worked, but the user wants fingerprint login), `authselect disable-feature with-fingerprint` (gates only sudo/polkit, not the greeter), true fingerprint unlock (impossible — the sensor yields no key material; upstream TPM-backed work hasn't shipped in Fedora 44).
