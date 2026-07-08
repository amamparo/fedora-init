# fedora-init

Idempotent setup for a fresh Fedora Workstation (GNOME) install, as a local
Ansible playbook. Tested against Fedora 44 / GNOME 50; the bundled shell
extensions also work back to GNOME 48.

## Run it

One command on a stock Fedora install — needs only curl and tar, which are
preinstalled:

```sh
curl -fsSL https://raw.githubusercontent.com/amamparo/fedora-init/main/install.sh | bash
```

The script fetches the repo tarball into a temp dir, authenticates sudo
once (fingerprint or password), installs its own toolchain (ansible-core
and friends, ~6 small distro rpms) plus a one-line sudoers drop-in
(`/etc/sudoers.d/fedora-init` — `Defaults timestamp_type=global`, so that
single authentication also covers the play's per-task sudo; Fedora's
fingerprint-first PAM can't hand a password to ansible's background sudo
calls, and the very first run asks you to authenticate twice while the
policy switches over). Then it runs the playbook. Afterwards **log out and
back in** (Wayland can't hot-reload GNOME Shell). To restore per-terminal
sudo tickets, delete the drop-in.

Run a subset of roles by substring: `./install.sh battery zsh` — or without
a checkout, append `-s battery` after `bash` in the one-liner.

Prove idempotency instead of trusting it:

```sh
./install.sh --check        # dry run: shows what would change, changes nothing
```

On a converged machine that prints every task `ok` or `skipped` with zero
diffs and no prompts beyond sudo — and, outside the updates role (whose
whole job is asking upstream what changed), zero network traffic. Any
dash-prefixed argument (`--check`, `--tags`, `-v`...) passes straight
through to `ansible-playbook`.

Hacking on it? Clone instead:

```sh
sudo dnf install -y git
git clone https://github.com/amamparo/fedora-init.git && cd fedora-init
./install.sh
```

## Roles

Each role is one concern, run in order; the tag (= role name with hyphens)
is what `./install.sh <substring>` matches against.

### updates

Everything Fedora's Software app would report, applied: all rpm updates,
firmware via fwupd/LVFS (reboot-staged ones get called out), and flatpak
updates for anything you've added. Runs first so the rest of the play
resolves against fresh metadata.

### battery

Swaps Fedora's default power stack (`tuned` + `tuned-ppd`) for **TLP**, which
tunes far more hardware knobs out of the box — this is most of the
"Ubuntu-gets-better-battery" gap. Also drops a config overlay into
`/etc/tlp.d/`:

- PCIe ASPM `powersupersave` + CPU EPP `power` on battery
- ThinkPad charge thresholds **75→80%** for battery longevity
  (`sudo tlp fullcharge` for a one-off 100% before travel)

`powertop` is installed purely as a *measurement* tool (`sudo powertop`) —
TLP already applies equivalent tunings, so no autotune service. GNOME's
power-mode toggle keeps working: **tlp-pd** serves the power-profiles D-Bus
API with TLP as the backend. Verify with `sudo tlp-stat -s`.

### window-snapping

Installs the bundled GNOME Shell extension
`roles/window_snapping/files/rectangle@amamparo/` (~120 lines, no
third-party deps) and one native keybinding. "Cmd" on a PC keyboard is the
**Super** (Windows) key.

| Keys                | Action                                          |
|---------------------|-------------------------------------------------|
| Super+Alt+←         | snap left, cycling widths 1/2 → 2/3 → 1/3       |
| Super+Alt+→         | snap right, cycling widths 1/2 → 2/3 → 1/3      |
| Super+Alt+↑         | snap top, cycling heights 1/2 → 2/3 → 1/3       |
| Super+Alt+↓         | snap bottom, cycling heights 1/2 → 2/3 → 1/3    |
| Super+Alt+F         | toggle maximize (GNOME native)                  |

GNOME's stock bindings collide with **all four** tiling keys
(`shift-overview-up/down` and `switch-to-workspace-left/right` both default
to Super+Alt+arrows), so the role clears the overview pair and moves
workspace switching to Super+Alt+PageUp/PageDown.

The arrows also snap straight out of a maximized (or fullscreen) window —
Super+Alt+F then Super+Alt+← goes directly to the left half. Maximize fills
the work area but keeps the top bar, and Alt+F10 (its stock binding) still
works. Prefer true fullscreen? See the comment in
`roles/window_snapping/tasks/main.yml`.

### zsh

Installs zsh + [oh-my-zsh](https://ohmyz.sh) (shallow git clone, never
auto-updated afterwards), drops in `roles/zsh/files/zshrc` (robbyrussell
theme, `plugins=(git)` only), and makes zsh the login shell. A pre-existing
`~/.zshrc` that differs is backed up once to `~/.zshrc.pre-fedora-init`.

### git-workspace

GitHub-ready ed25519 SSH key (no passphrase — same LUKS trade-off as
login-keyring; never regenerates an existing key, and prints the public
key + [github.com/settings/keys](https://github.com/settings/keys) when it
makes one), a `~/git` checkout dir, and a Files sidebar bookmark for it
(label yours however you like — the role won't rename it back).

It also sets your global git identity and turns on **SSH commit signing** —
commits and tags are signed with that same ed25519 key (git's SSH backend, no
separate GPG key). For GitHub to show commits as *Verified*, add the key a
second time at [github.com/settings/keys](https://github.com/settings/keys) as
a **Signing** key (a separate slot from the Authentication key, same key). A
`~/.config/git/allowed_signers` file is written so `git log --show-signature`
verifies locally.

### github-cli

The [GitHub CLI](https://cli.github.com) (`gh`) from Fedora's own repos —
`gh` for PRs, issues, `gh repo clone`, `gh api`, gists. Run `gh auth login`
once to authenticate (browser/device flow). `./install.sh github` targets just
this role; `./install.sh gh` also sweeps ghostty (harmless).

### multimedia

RPM Fusion (free + nonfree), then swaps stock Fedora's codec-stripped
packages for full builds: `intel-media-driver` (H.264/HEVC hardware decode
on this Intel Lunar Lake ThinkPad — stock has none, which drains battery on
video) and `ffmpeg`. Verify afterwards with
`sudo dnf install libva-utils && vainfo | grep -E 'H264|HEVC'`.

### steam

[Steam](https://store.steampowered.com) from RPM Fusion nonfree (enabled
by the multimedia role).

### brave

Brave browser from its official repo (gpg-verified), set as the default
browser (declaratively, via mime handlers), and every other browser removed —
Fedora's stock Firefox, plus chromium/epiphany/chrome if present. Also
force-installs PWAs via browser policy (Tidal, YouTube TV, tastytrade):
real apps — own windows, manifest icons, app-grid launcher entries, no
Desktop clutter — installed during the play itself (the role nudges Brave
awake headlessly if it isn't running). Edit the list in
`roles/brave/files/policies.json`; while an app is listed it can't be
uninstalled from Brave's UI, only by removing it there.

### gnome-prefs

The handful of desktop settings that differ from stock: dark mode, battery
percentage, minimize/maximize window buttons, empty dock, touchpad speed,
and 100% display scale (Fedora defaults to 125%; applied via mutter's D-Bus
API since Displays ▸ Scale isn't a gsettings key — laptop panel only, so
run it undocked or set docked layouts in Settings). Runs as the desktop
user — no privilege escalation.

### ghostty

[Ghostty](https://ghostty.org) as the terminal, from the COPR its own
install docs point Fedora at, themed Moonfly (edit
`roles/ghostty/files/config` — it's repo-owned). Fedora's stock Ptyxis is
removed once ghostty is in place, so it's gone from app search and
launching entirely; searching "terminal" finds Ghostty (its desktop entry
ships the keyword).

### no-overview

GNOME opens the Activities overview at every login and has no setting to
turn that off. Installs the second bundled micro-extension
(`roles/no_overview/files/no-overview@amamparo/`, ~10 lines), which hides
the overview the moment session startup completes, so logins land on the
desktop. On GNOME 50 the overview still *flashes* briefly — the shell
starts its login animation before extensions load, so hiding it is the
best any extension can do.

### appindicator

GNOME dropped legacy tray icons years ago, so apps that still use them —
Claude Desktop and the Tailscale applet, both installed here — show nothing in
the top bar. Fedora already ships the AppIndicator extension (usually as a
dependency) but leaves it off; this role installs it if missing and enables
it, so those tray icons appear. Takes effect at your next login.

### login-keyring

Kills the "login keyring did not get unlocked" prompt that appears after a
fingerprint login — while keeping fingerprint login. The keyring is
encrypted with your *password*, and a fingerprint match can't stand in for
it (the sensor yields a yes/no, not key material), so the prompt fires as
soon as anything needs a secret (Brave, Google accounts). The role
therefore removes the login keyring's password — it asks for your login
password once, and only when the keyring actually needs blanking: the
keyring then auto-unlocks on every login and never prompts again.

The trade-off, made deliberately: keyring contents (Brave's cookie/password
key, account tokens) are stored unencrypted in `~/.local/share/keyrings`.
With LUKS full-disk encryption that changes little in practice — offline
access is already gated by the disk password, and anything running as you
could read the secrets through the unlocked keyring anyway.

The role also removes the greeter password-only config an earlier revision
installed, restoring fingerprint at the login screen. If it reports your
password doesn't match the keyring, the account password was changed
outside PAM at some point — re-run with the old password, or reset the
keyring in Seahorse (`sudo dnf install seahorse`).

### vscode

VS Code from [Microsoft's official repo](https://code.visualstudio.com/docs/setup/linux)
(`roles/vscode/files/vscode.repo`, the documented content verbatim,
gpg-verified). Updates then arrive with normal `dnf upgrade`.

### jetbrains-toolbox

[JetBrains Toolbox](https://www.jetbrains.com/toolbox-app/) from the
official tarball (sha256-verified — JetBrains ships no rpm), installed
headlessly into `~/.local/share/JetBrains/Toolbox`. The Toolbox window
opens at the next login to sign in and install IDEs, and the app keeps
itself up to date from then on. Entirely user-level, no sudo.

### claude-code

[Claude Code](https://code.claude.com/docs) via Anthropic's native
installer: the launcher lands at `~/.local/bin/claude` (on PATH via the
zshrc from the zsh role) and self-updates from then on. Run `claude` once
to sign in. Also installs the
[caveman](https://github.com/JuliusBrussee/caveman) plugin — compresses
agent output (~65% fewer tokens) while keeping code, commands, and errors
verbatim, active automatically from the first message. Switch compression
with `/caveman [lite|full|ultra]`, or disable with `claude plugin disable
caveman` — the role won't re-enable a plugin you turned off.

### claude-desktop

[Claude Desktop](https://claude.com/download) — the GUI chat app, installed
for **local MCP servers** (a browser PWA can't spawn them). Anthropic ships
no Fedora build (the official Linux beta is Debian/Ubuntu-only), so this uses
the [aaddrick/claude-desktop-debian](https://github.com/aaddrick/claude-desktop-debian)
rpm, which repackages Anthropic's official Linux `.deb` and serves it from a
signed DNF repo — new versions arrive through the `updates` role's
`dnf upgrade`. Launch it and sign in once. Configure MCP servers in
`~/.config/Claude/claude_desktop_config.json` (quit the app before editing —
it rewrites that file on exit). Note this is an *unofficial* repackaging
signed with the maintainer's key, not Anthropic's; switch to an official
Fedora rpm if one ships.

The role also drops a per-user copy of the launcher entry in
`~/.local/share/applications/` so the **running** window shows the Claude name
and icon (the app reports its window as `claude-desktop`, which the vendor's
`/usr/share` entry doesn't match — the app-grid entry looks right but the
running window would otherwise be nameless and icon-less). Quit and relaunch
Claude once after install for it to take effect.

### podman

Podman over Docker, deliberately: Fedora-native, daemonless, rootless by
default (no root-equivalent `docker` group) — and kept at the latest
version on every run. `podman-docker` keeps the
`docker` CLI working (nag silenced via `/etc/containers/nodocker`),
`podman-tui` gives a terminal dashboard (containers/images/pods), and the
user API socket is enabled for docker-API tools — compose and
testcontainers mostly auto-detect it; if one doesn't, point it at
`DOCKER_HOST=unix:///run/user/$UID/podman/podman.sock`.

### tailscale

[Tailscale](https://tailscale.com) from its official repo, daemon enabled
at boot. Login is a browser flow no script can do: run `sudo tailscale up`
once — the role reminds you for as long as that's pending.

### mise

[mise](https://mise.jdx.dev) instead of pyenv + nvm + jenv: one manager
that installs *and* pins python, node, and JDKs (jenv never installed
anything) per project or globally, reads existing
`.nvmrc`/`.python-version` files, and hooks the shell once from the zshrc —
no shims, none of nvm's startup drag. Get runtimes with
`mise use -g node@lts python@3.13 java@temurin-21`, or drop the `-g` inside
a project.

## Adding a role

Drop `roles/<name>/` with a `tasks/main.yml` and add it to `site.yml` —
roles run in the order listed there, tagged with the role name
(underscores become hyphens). Conventions, in brief (CLAUDE.md has the full
contributor rules):

- one concern per role, and add-ons live in their host's role (the caveman
  plugin is part of claude-code, podman-tui part of podman) — new role only
  for a new standalone concern
- declarative modules over shell; every remaining command guarded and
  `changed_when`-honest, so `./install.sh --check` stays truthful
- package tasks guarded on `ansible_facts.packages` — an unchanged re-run
  must do zero network work
- `become: true` per task, only for system mutations; anything touching the
  user session (dconf, `$HOME`, session D-Bus) runs as the user
- static assets live in `roles/<name>/files/`
- lint with `ansible-lint --offline` before committing
