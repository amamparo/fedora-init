# fedora-init

Idempotent setup for a fresh Fedora Workstation (GNOME) install. Tested
against Fedora 44 / GNOME 50; the extension also works back to GNOME 48.

## Run it

One command on a stock Fedora install — needs only curl and tar, which are
preinstalled:

```sh
curl -fsSL https://github.com/amamparo/fedora-init/archive/main.tar.gz | tar xz && cd fedora-init-main && ./install.sh
```

Then **log out and back in** (Wayland can't hot-reload GNOME Shell).

Re-run a single module by substring: `./install.sh battery`

Hacking on it? Clone instead:

```sh
sudo dnf install -y git
git clone https://github.com/amamparo/fedora-init.git && cd fedora-init
./install.sh
```

## Modules

### 10-battery

Swaps Fedora's default power stack (`tuned` + `tuned-ppd`) for **TLP**, which
tunes far more hardware knobs out of the box — this is most of the
"Ubuntu-gets-better-battery" gap. Also drops `files/tlp/00-battery.conf` into
`/etc/tlp.d/`:

- PCIe ASPM `powersupersave` + CPU EPP `power` on battery
- ThinkPad charge thresholds **75→80%** for battery longevity
  (`sudo tlp fullcharge` for a one-off 100% before travel)

`powertop` is installed purely as a *measurement* tool (`sudo powertop`) —
TLP already applies equivalent tunings, so no autotune service.

GNOME's power-mode toggle keeps working: **tlp-pd** serves the
power-profiles D-Bus API with TLP as the backend (and TLP still switches
profiles automatically on AC/battery).

### 20-window-snapping

Installs the bundled GNOME Shell extension
`files/gnome/rectangle@amamparo/` (~120 lines, no third-party deps) and one
native keybinding. "Cmd" on a PC keyboard is the **Super** (Windows) key.

| Keys                | Action                                          |
|---------------------|-------------------------------------------------|
| Super+Alt+←         | snap left, cycling widths 1/2 → 2/3 → 1/3       |
| Super+Alt+→         | snap right, cycling widths 1/2 → 2/3 → 1/3      |
| Super+Alt+↑         | snap top, cycling heights 1/2 → 2/3 → 1/3       |
| Super+Alt+↓         | snap bottom, cycling heights 1/2 → 2/3 → 1/3    |
| Super+Alt+F         | toggle fullscreen (GNOME native)                |

GNOME's stock bindings collide with **all four** tiling keys
(`shift-overview-up/down` and `switch-to-workspace-left/right` both default
to Super+Alt+arrows), so the module clears the overview pair and moves
workspace switching to Super+Alt+PageUp/PageDown.

Prefer maximize over fullscreen? In `modules/20-window-snapping.sh`, change
`toggle-fullscreen` to `toggle-maximized`. Rebind the arrows via the `as`
keys in the extension's gschema.

### 30-zsh

Installs zsh + [oh-my-zsh](https://ohmyz.sh) (shallow git clone — all the
official installer does anyway, but a failed download aborts loudly), drops
in `files/zsh/zshrc` (robbyrussell theme, `plugins=(git)` only), and makes
zsh the login shell via `usermod`. A pre-existing `~/.zshrc` that differs is
backed up to `~/.zshrc.pre-fedora-init`.

### 40-multimedia

RPM Fusion (free + nonfree), then swaps stock Fedora's codec-stripped
packages for full builds: `intel-media-driver` (H.264/HEVC hardware decode
on this Intel Lunar Lake ThinkPad — stock has none, which drains battery on
video) and `ffmpeg`. Verify afterwards with `vainfo` (from `libva-utils`).

### 50-brave

Brave browser from its official repo (`files/brave/brave-browser.repo`,
gpg-verified).

### 60-gnome-prefs

The handful of desktop settings that differ from stock: dark mode, battery
percentage, minimize/maximize window buttons, empty dock, touchpad speed,
and the Ptyxis Moonfly palette (applied on re-run if Ptyxis hasn't launched
yet). Runs as the desktop user — no sudo.

## Adding a module

Drop `modules/NN-name.sh` — modules run in filename order. Conventions:

- `set -euo pipefail`, idempotent (safe to re-run)
- static assets live under `files/<name>/`, referenced via `$REPO_ROOT`
  (exported by `install.sh`; each module also derives its own fallback so it
  can run standalone)
- call `sudo` per command; `install.sh` primes the password once
