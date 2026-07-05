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
  - call `sudo` per command (never assume a root shell); install.sh has already cached credentials
  - static assets live in `files/<name>/` and are referenced via `$REPO_ROOT`
- `files/` — assets the modules install verbatim (TLP drop-in config, zshrc, the GNOME extension).

## The GNOME extension

`files/gnome/rectangle@amamparo/` is a bundled, self-authored GNOME Shell extension (ESM, GNOME 48–51), not a vendored third-party one — edit it directly here. Parts that must stay in sync:

- Keybindings are declared in `schemas/*.gschema.xml` (the `as`-typed keys), consumed by name in `extension.js` (`KEYS`), and the schema is compiled by `modules/20-window-snapping.sh` at install time (`glib-compile-schemas`).
- `metadata.json`'s `shell-version` list gates which GNOME versions load it; `_unmaximize()` in extension.js carries a GNOME ≤48 vs ≥49 API fallback for the same reason.
- Wayland can't hot-reload GNOME Shell — any extension change takes effect only after logout/login.

## Gotchas encoded in the modules

- 10-battery: TLP conflicts with `tuned-ppd`/`power-profiles-daemon` (hence `--allowerasing`, the removals, and the masks), and TLP's docs require masking `systemd-rfkill`. powertop is intentionally measurement-only — no autotune service, it would fight TLP over the same sysfs knobs.
- 20-window-snapping: `gnome-extensions enable` fails if the running shell hasn't scanned the new extension dir yet; the module falls back to editing the `enabled-extensions` gsettings list directly.
- files/tlp/00-battery.conf deliberately contains only non-default TLP settings; keep it minimal rather than restating defaults.
