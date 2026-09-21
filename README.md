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

If a role needs secrets (**aws** and **litellm**), the script also installs
the Bitwarden CLI and signs into your vault right on the terminal — master
password + TOTP, no browser — before the play starts. That only happens
while a secret is still unseeded (`~/.aws/credentials` missing; the
`ANTHROPIC_API_KEY=` line missing from `/etc/litellm/litellm.env`, a file
the litellm role also keeps its generated keys in); a converged machine
never prompts. And it's never a roadblock: no vault yet, or a failed sign-in,
just means everything else still configures and the role prints a
reminder — re-run `./install.sh aws` (or `litellm`) whenever you're ready.
To put the secrets *into* the vault from this machine, `just seed-bitwarden`
(see [Bitwarden items](#bitwarden-items)).

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
updates (Podman Desktop, plus anything you've added). Runs first so the rest
of the play resolves against fresh metadata. It also brings the four
installs that live outside dnf's repos current: ollama (re-installed from
the upstream release whenever one moved — a ~1.4 GB download), Goose (the
upstream rpm, re-installed when a release moved — relaunch it afterwards),
the litellm venv (`uv pip install --upgrade`, its Prisma client
regenerated when the schema changed, then a restart) and opencode
(`opencode upgrade`). Under `--check` the version probes run but the
upgrades themselves are skipped; pending ollama and Goose upgrades are
printed.

### snapshots

Automatic btrfs snapshots of the system, so a bad update — or a playbook
change that goes sideways — is an undo, not a reinstall. Every dnf
transaction (the updates role's upgrades included) gets pre/post
[snapper](http://snapper.io) snapshots via a dnf5 hook, and a timeline
timer adds a few hourly/daily ones on top; retention prunes itself
(20 transaction pairs, 5 hourly / 7 daily / 2 weekly / 1 monthly).
Inspect and revert with `sudo snapper list` / `sudo snapper undochange`,
or the bundled **Btrfs Assistant** GUI.

Two honest limits: snapshots live on the same SSD — they're an undo
button, not a backup — and Fedora keeps `/home` on its own subvolume, so
system snapshots don't cover your files. `/var` *is* inside root, so a
full rollback also rewinds logs and system-side container state
(rootless podman under `~` is unaffected).

### hostname

Names the machine **thinkpad** and makes it answer on the local network as
**thinkpad.local**, the way a Mac is reachable at `<name>.local` out of the
box. Stock Fedora never sets a hostname at all — it falls back to the
`fedora` baked into `/usr/lib/os-release`, which is both anonymous and the
same name every other stock Workstation on the network is using.

Nothing here invents the `.local` mechanism: Workstation already runs
[Avahi](https://avahi.org) and ships `nss-mdns`. The role sets the name,
makes sure Avahi is running, and restarts it — Avahi reads the hostname only
at startup, so without that restart the machine keeps answering as
`fedora.local` until the next reboot.

To call it something else, change the one name in
`roles/system/hostname/tasks/main.yml`. Renaming it in GNOME Settings instead works
until the next `./install.sh`, which puts it back — the name lives in the
role.

**Check it from another machine** — `ping thinkpad.local`, or
`avahi-resolve -n thinkpad.local` here. Checking with `getent` or
`resolvectl` on this laptop proves nothing: systemd-resolved answers your own
`.local` name locally, whatever Avahi is actually publishing. If some other
device on the network already claims the name, Avahi quietly publishes
`thinkpad-2` instead.

Who can reach it: macOS and iOS natively; Windows 10 (1703+) and 11 natively
— don't install Apple's Bonjour for Windows, it fights the built-in
responder; other Linux boxes need `nss-mdns`. Note that `.local` is
same-network only and never travels over Tailscale — remotely this machine is
`thinkpad.<tailnet>.ts.net`, a different name rather than a fallback.

Renaming also cleans up after itself in one place you'd never guess. Chromium
— so Brave, and every Electron app — tags the profile it has open with the
machine's name, and refuses to touch a profile that some *other* machine
appears to be holding, which is exactly what a leftover tag looks like once
the machine has been renamed. The browser then won't start **and shows no
error at all**, because the message explaining why is drawn by the window that
never opens; clicking the launcher just does nothing. A fresh install can't
hit this — the rename happens long before Brave is installed — but the first
`./install.sh` on a laptop you'd already been using can. The role clears those
stale tags, and only ever clears one whose process is genuinely gone, so a
browser left open while `./install.sh` runs is untouched and tidies up after
itself on exit. Flatpak apps are not swept (their lock lives under
`~/.var/app` and records a sandbox pid): if the Podman Desktop flatpak won't
start after the rename, close it and `rm ~/.var/app/<id>/config/*/Singleton*`.

Two knock-on effects, both intended: GNOME Settings ▸ System shows **Device
Name** `thinkpad`, and that's the name phones and headphones see when pairing
over Bluetooth. Already-open terminals keep the old name in their prompt
until you log back in. And on a non-Workstation base without `avahi`/
`nss-mdns` installed, the rename still happens but the `.local` half silently
skips. `./install.sh host` also sweeps ghostty (g·host·ty); use
`./install.sh hostname`.

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
`roles/desktop/window_snapping/files/rectangle@amamparo/` (~120 lines, no
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
`roles/desktop/window_snapping/tasks/main.yml`.

### zsh

Installs zsh + [oh-my-zsh](https://ohmyz.sh) (shallow git clone, never
auto-updated afterwards), drops in `roles/dev/zsh/files/zshrc` (robbyrussell
theme, `plugins=(git)` only), and makes zsh the login shell. A pre-existing
`~/.zshrc` that differs is backed up once to `~/.zshrc.pre-fedora-init`.

### cli-tools

The modern-CLI baseline, all from Fedora's own repos: `fzf` (Ctrl+R fuzzy
history, Ctrl+T file picker, Alt+C fuzzy cd — wired up in the zsh role's
zshrc), `rg` (ripgrep), `fd`, `bat`, `jq`, `yq` (Fedora ships mikefarah's
Go yq, the v4 syntax modern docs assume), `btop`, `eza`, `zoxide`
(`z proj` jumps to a dir you've visited once), `uv` (with `uvx`,
which many MCP-server configs expect on PATH — mise still owns runtime
pins), [`just`](https://just.systems) (a command runner: `just
<recipe>` runs recipes from a project's `justfile`, with tab-completion
out of the box — it has no build graph, so it complements a Makefile
rather than replacing one), and `vim` — the real editor, which a stock
Fedora install does *not* give you: the base group ships only `vi`, a
tiny build with no syntax highlighting and no scripting, so the rpm here
is `vim-enhanced`. It aliases `vi` to `vim` in interactive shells on its
own, so there is nothing to learn. `./install.sh tools` targets only this
role (`cli` also sweeps github-cli).

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

Git output also gets [delta](https://github.com/dandavison/delta) as the
pager: `git diff`/`show`/`blame` render with syntax highlighting,
within-line change emphasis, and `n`/`N` to jump between files. Terminal
only — scripts and tools see plain git output. The pager settings are
repo-declared (same class as the aws region defaults): edit them in
`roles/dev/git_workspace/tasks/main.yml`, not with `git config`, which a later
run would revert.

### github-cli

The [GitHub CLI](https://cli.github.com) (`gh`) from Fedora's own repos —
`gh` for PRs, issues, `gh repo clone`, `gh api`, gists. Run `gh auth login`
once to authenticate (browser/device flow). `./install.sh github` targets just
this role — note `./install.sh gh` reaches only ghostty (the tag `github-cli`
doesn't contain "gh").

### aws

The AWS CLI v2 from Fedora's own repos, with `~/.aws` set up the way
`aws configure` would: region/output defaults in `~/.aws/config` (edit them
in `roles/dev/aws/tasks/main.yml` — the role puts them back if changed
elsewhere), and the access keys seeded into `~/.aws/credentials` (0600)
from your **Bitwarden** vault. install.sh signs in on the terminal (master
password + TOTP; the Bitwarden cloud sometimes also asks for your personal
API-key `client_secret` as a bot check) and the role reads the login item
named `aws` — username = Access Key ID, password = Secret Access Key.

One-time prep in Bitwarden: a free account works; enable **TOTP two-step
login** (the CLI can't do passkeys/FIDO2 or Duo — keep an authenticator
app enrolled) and create the `aws` item. The seed runs only while
`~/.aws/credentials` is missing — after that the file is yours: rotate keys
with plain `aws configure`, or delete the file and re-run.

### multimedia

RPM Fusion (free + nonfree), then swaps stock Fedora's codec-stripped
packages for full builds: `intel-media-driver` (H.264/HEVC hardware decode
on this Intel Panther Lake ThinkPad — stock has none, which drains battery on
video) and `ffmpeg`. Verify afterwards with
`sudo dnf install libva-utils && vainfo | grep -E 'H264|HEVC'`.

### steam

[Steam](https://store.steampowered.com) from RPM Fusion nonfree (enabled
by the multimedia role).

### brave

Brave browser from its official repo (gpg-verified), set as the default
browser (declaratively, via mime handlers), and every other browser removed —
Fedora's stock Firefox, plus chromium/epiphany/chrome if present. The
leftover Anaconda installer UI (`anaconda-webui`) goes too: fresh
Workstation installs leave it behind and it depends on Firefox, blocking
the removal. Web apps (PWAs) are deliberately not managed here — install
them by hand from Brave's ⋮ ▸ Cast, save, and share ▸ Install page as app.

In the app grid it's called **Web Browser** — typing "brave" still finds it.
That's done by generating a copy of Brave's launcher entry into
`~/.local/share/applications` with a new `Name=` and a `Keywords=` line, and
regenerating it from the vendor's own file on every run, so `Exec`, the MIME
handler list and `StartupWMClass` are never a frozen snapshot of Brave's.
Since system updates run first in the same playbook, an upgrade to Brave is
always followed by a fresh copy before the run ends. To go back to the vendor
name, delete `~/.local/share/applications/brave-browser.desktop` and drop the
task.

The app icon is swapped for a Netscape-styled Brave lion
(`roles/desktop/brave/files/icons/`, with the unscaled master kept beside the
installed sizes). It's installed into `~/.local/share/icons/hicolor`, which
overrides the icon *theme* rather than naming a file in the launcher entry —
so the new mark is used everywhere the icon appears, windows and
notifications included, and because Brave regenerates its `/usr/share` icons
from a postinstall scriptlet on every upgrade, keeping the override under `~`
is what makes it survive `dnf upgrade`. To go back to the stock mark, delete
`~/.local/share/icons/hicolor/*/apps/brave-browser.png` and drop the task.

### gnome-prefs

The handful of desktop settings that differ from stock: dark mode, battery
percentage, minimize/maximize window buttons, empty dock, touchpad speed,
the wallpaper (`roles/desktop/gnome_prefs/files/amber-d.jxl`, staged into
`~/.local/share/backgrounds` and set for both light and dark), and the
display config: 1680×1050 with a variable refresh rate at 100% scale (the
panel is 1920×1200 and Fedora defaults it to 125%). None of those three is a
gsettings key — they live in `monitors.xml` and are applied via mutter's
D-Bus API, laptop panel only, so run it undocked or set docked layouts in
Settings. The playbook owns those three: a resolution, refresh rate or scale
picked by hand in Settings ▸ Displays is put back on the next run, so change
them in `roles/desktop/gnome_prefs/tasks/main.yml` instead. Runs as the desktop
user — no privilege escalation.

### ghostty

[Ghostty](https://ghostty.org) as the terminal, from the COPR its own
install docs point Fedora at, themed Moonfly (edit
`roles/desktop/ghostty/files/config` — it's repo-owned). Fedora's stock Ptyxis is
removed once ghostty is in place, so it's gone from app search and
launching entirely; searching "terminal" finds Ghostty (its desktop entry
ships the keyword).

### no-overview

GNOME opens the Activities overview at every login and has no setting to
turn that off. Installs the second bundled micro-extension
(`roles/desktop/no_overview/files/no-overview@amamparo/`, ~10 lines), which hides
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

### fingerprint

Keeps the fingerprint reader working across suspend. `fprintd` is a
transient daemon — the login screen starts it on demand and it exits ~30
seconds after the last check — and if the machine sleeps inside that window
it rides through the suspend holding a USB handle that resume invalidates.
From then on every fingerprint check fails silently, so GDM stops offering
fingerprint and asks for your password instead, until the stuck daemon
finally times out. That is the whole "the sensor sometimes goes offline"
symptom: whether it works depends only on whether you happened to shut the
lid within half a minute of the last fingerprint prompt.

The role installs a two-line systemd override
(`/etc/systemd/system/fprintd.service.d/stop-before-sleep.conf`) that stops
`fprintd` before the machine sleeps. Nothing needs re-enabling on resume —
the next fingerprint prompt starts it again against a freshly opened
sensor. Safe here because the reader can't wake the laptop anyway
(`power/wakeup` is `disabled`), so there's no wake-on-finger to give up.

If it ever does wedge mid-session, `sudo systemctl restart fprintd` clears
it immediately. Despite appearances this is **not** a TLP or power-saving
problem: USB autosuspend on the reader is stock Fedora behaviour (systemd's
hwdb marks the device autosuspend-safe) and works correctly — turning it
off changes nothing.

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

Blanking alone doesn't hold, so the role also stops GNOME from undoing it.
Logging in at the greeter with your **password** used to silently re-encrypt
the keyring: `pam_gnome_keyring` hands the typed password to gnome-keyring,
which notices the keyring has no password and "fixes" that by re-keying it to
your login password — after which every fingerprint login prompts again. (It
usually bites right after a system update, because the reboot is when you're
most likely to type a password at the greeter — the update itself is
innocent.) The role removes the two lines that pass the password along, in
`/etc/pam.d/gdm-password` and `gdm-switchable-auth`, and keeps the
`session … auto_start` line that actually starts the keyring daemon. Those
files belong to the `gdm` package and can be reset by a gdm update, so the
fix is re-applied on every `./install.sh` run.

The role also removes the greeter password-only config an earlier revision
installed, restoring fingerprint at the login screen. If it reports your
password doesn't match the keyring, the account password was changed
outside PAM at some point — re-run with the old password, or reset the
keyring in Seahorse (`sudo dnf install seahorse`).

### vscode

VS Code from [Microsoft's official repo](https://code.visualstudio.com/docs/setup/linux)
(`roles/dev/vscode/files/vscode.repo`, the documented content verbatim,
gpg-verified). Updates then arrive with normal `dnf upgrade`.

### claude-code

[Claude Code](https://code.claude.com/docs) via Anthropic's native
installer: the launcher lands at `~/.local/bin/claude` (on PATH via the
zshrc from the zsh role) and self-updates from then on. Run `claude` once
to sign in. Also installs the
[caveman](https://github.com/JuliusBrussee/caveman) plugin — compresses
agent output (~65% fewer tokens) while keeping code, commands, and errors
verbatim, active automatically from the first message. Switch compression
with `/caveman [lite|full|ultra]`, or disable with `claude plugin disable
caveman` — the role won't re-enable a plugin you turned off. Finally, sets
**ultracode** as the default in `~/.claude/settings.json` (xhigh reasoning
effort plus standing multi-agent workflow orchestration) — the in-session
toggle never persists, so a settings key is the only way to make it the
default. Only this one key is merged in; the rest of the file is left for
Claude Code to manage.

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

An earlier version of this role kept a per-user copy of the launcher entry in
`~/.local/share/applications/` to fix the **running** window's name and icon.
The vendor fixed that upstream, and the stale copy then *caused* the bug it
once fixed (a blank icon in alt+tab, while app search still looked fine), so
the role now deletes the override instead — the packaged entry is correct.

### podman

Podman over Docker, deliberately: Fedora-native, daemonless, rootless by
default (no root-equivalent `docker` group) — and kept at the latest
version on every run. `podman-docker` keeps the
`docker` CLI working (nag silenced via `/etc/containers/nodocker`),
`podman-tui` gives a terminal dashboard (containers/images/pods), and the
user API socket is enabled for docker-API tools — compose and
testcontainers mostly auto-detect it; if one doesn't, point it at
`DOCKER_HOST=unix:///run/user/$UID/podman/podman.sock`. Also installs
[Podman Desktop](https://podman-desktop.io) — the GUI for containers, pods,
images and Kubernetes — as its official Flathub flatpak
(`io.podman_desktop.PodmanDesktop`, already exposed by Fedora's stock
flathub remote), kept current by the updates role's `flatpak update`. It
finds the user socket above on its own.

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

### pipewire

PipeWire config for using a USB audio interface from a DAW — two small
drop-ins under `~/.config`, nothing system-wide:

- **The Quad Cortex mini gets WirePlumber's "Pro Audio" profile.** Left to
  itself, WirePlumber reads the mini's USB descriptor — which declares a
  cinema 7.1 speaker layout — and files it as an "Analog Surround 7.1"
  device: the eight inputs come out remapped and named `LFE`, `RL`, `SR`…,
  which is meaningless for a guitar interface. Pro Audio opens the card raw:
  every channel is its own port, `AUX0`–`AUX7` in device order, no mixing
  or resampling, and the node is called *Quad Cortex mini Pro*. In REAPER
  the track input dropdown then reads `Quad Cortex mini Pro:capture_AUX0`
  and so on. Add another interface by its USB ids in `site.yml`
  (`pipewire_pro_audio_devices`). A profile you pick yourself in Settings ▸
  Sound is remembered and wins over the rule; `wpctl set-profile <id>
  pro-audio` hands control back.
- **JACK clients run at 128 frames (2.7 ms).** REAPER's Linux audio system
  is JACK by default, and on Fedora that *is* PipeWire — no jackd. PipeWire
  would otherwise run it at the desktop's 1024-frame buffer (21 ms each
  way). The lower buffer applies only while a JACK client is open, and the
  quantum is locked for its lifetime; verified xrun-free on the mini. Raise
  `pipewire_jack_latency` in `site.yml` to `256/48000` if a session ever
  crackles.

What it doesn't touch: the default output and input devices. WirePlumber
makes the mini the default *output* whenever it's plugged in (USB outranks
the laptop's card) and leaves the default *input* wherever Settings ▸ Sound
last put it — change either there, and the choice sticks. That input choice
is what orders REAPER's inputs: pick *Quad Cortex mini Pro* there and it's
Inputs 1–8; leave the laptop mic and it's Inputs 3–10, still labelled by
name. MIDI over USB needs nothing — it already shows up as *Midi-Bridge:
Quad Cortex mini*. Anything fancier is a qpwgraph patch.

### qpwgraph

[qpwgraph](https://gitlab.freedesktop.org/rncbc/qpwgraph) — a patchbay GUI
for PipeWire. Route REAPER to specific devices, record Brave/system audio
into a track, or see the actual audio graph when something is silent.
Patchbay files persist and auto-reconnect, so a recurring REAPER routing
survives replugs and reboots. (It's a Qt app, so it looks a little
non-native on GNOME — cosmetic only.)

### reaper

[REAPER](https://www.reaper.fm) (DAW) via the official Linux tarball's
installer — latest version at install time, into `/opt/REAPER` with a
desktop entry and a `reaper` symlink on PATH. It's licensed shareware:
the 60-day evaluation starts on first run; buy a license when it fits.

- **[Reapertips](https://www.reapertips.com) theme, dark variant** — plus its
  toolbar and track icons, its colour palette and the Fira Sans fonts it asks
  for (Roboto comes from Fedora's own package). The theme ships in the repo
  rather than being downloaded, so a fresh install needs no network for it,
  and the dark dialogs and menus come with it (see below). Tune it with
  REAPER's built-in *Default_7.0_theme_adjuster* script.

  To update it: download the current package from Reapertips, then replace
  `roles/audio/reaper/files/Reapertips Theme.ReaperThemeZip` and
  `reapertips-license.txt` from it. Diff the rest before touching it — on the
  v1.9 → v1.93b update every other asset was byte-identical. Two traps: the
  `RT_`-prefixed icons and *all* the track icons come from the separate
  *Essential Icons for REAPER* pack, not the theme package, so a theme update
  must not sweep them (only the 257 `RTH_` files belong to the theme); and the
  vendored file is deliberately unversioned, so to see which version is on
  disk, look at the folder inside the zip
  (`python3 -m zipfile -l '…/Reapertips Theme.ReaperThemeZip'`).
- **Ableton-shaped navigation.** Bare two-finger scroll moves vertically,
  Shift scrolls the timeline, **Ctrl zooms** and Alt changes track height —
  Live's scroll/Cmd/Option scheme, with Ctrl standing in for Cmd. Dragging
  the ruler scrolls and zooms at once (Ctrl still sets loop points);
  middle-drag hand-scrolls. Horizontal scroll pans touchpad-naturally,
  correcting a sign bug in REAPER's Linux layer rather than a GNOME setting.
  Zoom stays cursor-anchored like Live's; vertical zoom follows the pointer,
  also like Live. *Two Live gestures are impossible here:* pinch-to-zoom
  (REAPER's Linux layer receives the gesture and discards it — Ctrl+scroll
  is the stand-in) and smooth/inertial panning.
- **SWS and ReaPack extensions** dropped into `UserPlugins` (fetched only
  when missing, like the REAPER install itself).
- **Dark dialogs and menus** via `libSwell-user.colortheme`, the theme's own
  Linux extra — the tarball build has no GTK theming, so this file is what
  "dark mode" means on Linux (REAPER only offers it as a setting on Windows).
  It belongs to *this* theme: under a light-dialog theme the same file makes
  button labels invisible.
- **Bigger, easier-to-hit dialogs.** That same file is the only place REAPER
  exposes the *size* of its Linux dialogs, and the stock values are cramped:
  the Media Explorer, action list, FX browser and Preferences get a 14px font
  (from 13), a 17px scrollbar with a 20px minimum thumb (from 14 and a
  near-unhittable 4), a 24px combo box and a taller menu bar. Tune them in
  `reaper_swell_metrics` in the role. The font is integer pixels, so 14 is the
  only step between stock and 15 — and the click-target sizes are independent
  of it, so text and hit area can be traded off separately. Changes appear
  when the theme is reloaded or REAPER restarts.
- **First-run settings** seeded into `reaper.ini` — *only when the file
  doesn't exist yet*; after REAPER's first run the file belongs to REAPER.
  Includes: theme selection, Live-style "Follow" scrolling, new-project
  startup, auto-save every 3 minutes to `~/Music/REAPER/Backups`, peak
  caches in `~/Music/REAPER/Peaks`, straight grid lines, Time timebase,
  clean recording filenames, smoother meters, snappier media buffering.
- **Reapertips' own [*Perfect Setup*](docs/reapertips-perfect-setup.pdf)
  guide**, by the same author as the theme — vendored in `docs/`, and the
  tips that map to a documented config variable are applied for you: no
  automatic item fades, no tiny fade-in on playback start, the full set of
  media-item buttons, incomplete loop takes discarded at a 90% threshold,
  one MIDI editor per project, item edges that extend instead of looping,
  and an uncluttered 18px grid. Everything else the guide suggests —
  toolbars, screensets, mouse modifiers, the REAPER 7 settings with no
  documented key — is printed as a numbered checklist with page numbers the
  first time the role touches your `reaper.ini`.
- **Audio through PipeWire's JACK, with room for the interface.** REAPER's
  Linux default audio system is JACK, which PipeWire serves (see
  [pipewire](#pipewire)), so nothing to switch. What a fresh REAPER *does*
  get wrong is opening only two JACK inputs and outputs — enough for the
  laptop mic and not the interface behind it — so the seed sets 16 inputs
  and 8 outputs. Inputs are named after the PipeWire port they're patched
  to (`Quad Cortex mini Pro:capture_AUX0`), so picking the interface is a
  dropdown, not a routing exercise.
- **Already-configured machines get the settings too.** A machine that has
  run REAPER before doesn't get the seed, so the keys worth having are
  back-filled individually instead — each one added *only if absent*, so
  anything you've set yourself, in Preferences or by hand, always wins.
- **Retired themes clean themselves up.** The role briefly shipped LCS Flat 7;
  its files are now deleted from the machine on every run, permanently, so a
  machine that ran that revision tidies itself.

There is no Ableton Live theme here, and that's deliberate: none exists that
works on REAPER 7. Both serious candidates were tested and fail — one never
applies its layout, the other ships no toolbar art. The Ableton *feel* is in
the navigation above, which is what actually survives a theme change — and it
did: the look went back to Reapertips, the navigation stayed.

The *Perfect Setup* guide and the Ableton goal disagree in exactly two places,
and they're resolved on purpose. Dragging an item's right edge now **extends**
it rather than repeating the content, which is the guide's call and not Live's.
Horizontal zoom stays **cursor-anchored** rather than following the mouse,
which is Live's call and not the guide's.

### gimp

[GIMP](https://www.gimp.org) — the raster image editor, from Fedora's own
repos. Just the editor: the per-language offline manuals, the extra brush
and pattern data, and the third-party plugin packages Fedora also carries
(`gimp-data-extras`, `gimp-dds-plugin`, `gimpfx-foundry`…) are all left to
`sudo dnf install` if you ever want them.

### ollama

[Ollama](https://ollama.com) — the local model server, as a system service
on `127.0.0.1:11434` (loopback only; litellm sits in front). Installed from
the upstream release, not Fedora's rpm: F44's package is frozen at 0.12.11
(it can't load any 2026 model family) and drags 2 GiB of AMD ROCm onto an
Intel machine. The role fetches the current release tarball, verifies it
against the release's checksums, and unpacks `bin/ollama` + `lib/ollama`
into `/usr/local` with the CUDA runtimes left out (97 MiB instead of
2.1 GiB). A dedicated `ollama` user runs it with its models in
`/var/lib/ollama/models` — a nested btrfs subvolume, so the snapshots role's
pre/post snapshots never pin 19 GB model blobs. Ollama has no self-update
and cuts a release every few days, so the **updates** role re-installs it
whenever upstream moved (a ~1.4 GB download each time).

The models to pull are declared in `site.yml` (`ollama_models`, default
`qwen3-coder:30b` — a 19 GB tool-calling coding model with 3B active
parameters, so it generates at small-model speed) and pulled once, guarded
on what the server already has. Local context is `llm_local_context`
(32k; agents like more, RAM permitting), the K/V cache is 8-bit, a model
stays loaded 30 minutes after its last request, and ollama.com cloud
features are off (`OLLAMA_NO_CLOUD=1`, which also stops a signed
phone-home on every start). The Intel iGPU is used through Vulkan
(`ollama_igpu: true`): measured here on the default model at ~190 tokens/s
prompt processing and ~23 tokens/s generation, against 12 and 7 on the
CPU's four P-cores (6 and 3 at llama.cpp's own 2-thread default) — the
difference between an unusable and a usable local coding agent. Two things to know: a model loaded while the desktop
is using a lot of RAM may land only partly on the GPU (`ollama ps` should
say 100% GPU; `ollama stop <model>` and retry), and inference shares the
GPU with games and the compositor. Set `ollama_igpu: false` for CPU-only
(4 threads, the P-cores — 8 is slower). The first bare `ollama` you type
shows a sign-in/continue-locally screen; "continue locally" is the answer.
`journalctl -u ollama` logs one line per API request.

### litellm

[LiteLLM](https://docs.litellm.ai) proxy — one OpenAI-compatible endpoint,
`http://127.0.0.1:4000/v1`, in front of ollama and Anthropic, with its
**admin UI at `http://litellm.localhost/ui`** (the `.localhost` name
resolves to loopback everywhere with no configuration; port 80 is a
systemd socket handing connections to `systemd-socket-proxyd`, which
forwards to :4000 — no reverse-proxy package, and no TLS because browsers
already treat `*.localhost` as a secure context over plain http). goose and
opencode talk only to it, so a local model and Claude are one model-name
apart: `ollama/<tag>` for anything ollama has pulled (a wildcard route —
`ollama pull` something new and it's reachable), `anthropic/<id>`
(`anthropic/claude-opus-5`…) for Claude. No rpm exists, so it's a python
venv under `/opt/litellm` built with the stock `uv`, run as a hardened
systemd service (`litellm.service`, dedicated dynamic user);
`/etc/litellm/config.yaml` is the baseline routes and settings; whatever
you change in the admin UI (models, settings, its own pages — the Chat page
is off until you switch it on under Settings) is kept in the database and
layered over that file, so a re-run never undoes it. Health:
`curl 127.0.0.1:4000/health/liveliness`. Upgrades ride the updates role.

The UI needs a **master key** and a **database**, and the role provides
both: the key is generated once into `/etc/litellm/litellm.env` (root-only —
`just litellm-master-key` prints it; UI login is user `admin`, password =
that key), and the database is Fedora's own PostgreSQL rpm, reached over
its unix socket with peer auth (no password; its localhost TCP port stays
on ident auth, which no login can pass).
The Prisma toolchain the database path needs — Node (`nodejs22` rpms),
the `openssl` CLI, the Prisma CLI + engines — is staged read-only under
`/opt/litellm`, so the service starts offline. Clients never hold the
master key: the role mints one **virtual key per client** (goose,
opencode — `litellm_clients` in `site.yml`) into `~/.config/litellm/keys/`
and re-mints it if the database ever forgets it; each is its own row under
Virtual Keys, with its own spend and logs.

The Anthropic key is the one vault secret: seeded once into the same env
file from the Bitwarden login item named `anthropic` (password = the API
key), exactly like the aws credentials — the role prints a reminder while
it's missing, and `anthropic/*` routes answer with an AuthenticationError
until then. Rotate by editing that line (and `systemctl restart litellm`),
or delete it and re-run.

### goose

[Goose](https://github.com/aaif-goose/goose) — Block's coding agent, as the
**desktop app**, from upstream's per-release rpm (there is no repo, no
Fedora package and the flatpak bundle can't see the config below; the app
can't update itself on Linux, so the updates role installs each new
release). It replaces Fedora's `goose` CLI rpm, which the role removes:
the desktop bundles that very CLI as its backend
(`/usr/lib/Goose/resources/bin/goose`, if you ever want it in a terminal).
Pre-configured through `/etc/goose/config.yaml`, a system layer goose
merges under your own `~/.config/goose/config.yaml` and never writes:
provider `litellm`, `LITELLM_HOST` pointed at the proxy, the default model
from `site.yml` (`llm_default_model`), telemetry off, secrets in
`~/.config/goose/secrets.yaml` (where the role keeps goose's own litellm
key current) — so the first launch skips the provider wizard and the
consent dialog. Your own choices in
Settings land in the user file and win. The role also generates a launcher
shadow in `~/.local/share/applications` so the window binds to its icon
(the app's Wayland id is `goose`, the vendor entry doesn't say so). Tools
run without an approval step by default; `GOOSE_MODE: smart_approve` in
the user file if you'd rather confirm shell commands. Two upstream quirks:
inside Goose, `uvx`/`npx`/`node` are bundled shims that fetch their own
toolchains into `~/.config/goose/mcp-hermit` (name absolute paths in
extension configs if you want the host's), and after the updates role
installs a new release, quit and relaunch a running Goose.

### opencode

[OpenCode 2](https://opencode.ai) — the terminal coding agent, installed
from its npm platform package (the V2 line's distribution; the V1 line is
what GitHub's releases page still shows) into `~/.opencode/bin` (the
vendor's layout; the zsh role puts it on PATH — log out and back in the
first time) and pre-configured for the proxy. Two files in
`~/.config/opencode`: `opencode.json` is managed from `site.yml` (the
`litellm` provider with every local and Anthropic model listed — opencode
can't discover a custom provider's models — and its key as a `{file:}`
reference to `~/.config/litellm/keys/opencode`), `opencode.jsonc` is seeded once
with the default model and then yours (that's also where MCP servers,
agents and keybinds go). Pick models in the TUI; V2's updater only notifies
by default, so the updates role runs `opencode upgrade`.

## Bitwarden items

Two roles seed secrets from your vault, both matched by exact item name:

| item | type | fields | seeds |
|---|---|---|---|
| `aws` | login | username = Access Key ID, password = Secret Access Key | `~/.aws/credentials` (aws) |
| `anthropic` | login | password = the API key | `/etc/litellm/litellm.env` (litellm) |

Create them in the web vault or push them from here: `just seed-bitwarden`
upserts them from `SEED_AWS_ACCESS_KEY_ID`, `SEED_AWS_SECRET_ACCESS_KEY`
and `SEED_ANTHROPIC_API_KEY` — or hidden prompts for whichever is unset
(blank skips, prompted or exported). Only what you supply is written: an
item you skip is left alone, an existing item keeps every field you didn't
give (a new secret key keeps its key id), and nothing else in the vault is
touched. Two refusals: *creating* `aws` needs both fields (a half item would
seed a broken credentials file), and an existing item of another type named
`aws` or `anthropic` must be renamed first — the roles match by name alone.
A Bitwarden session you already have exported is used and left unlocked.
If the CLI's stored sign-in has expired (the master password is accepted
but the sync fails with `invalid_grant`), both this and `./install.sh` sign
you in again instead of failing.
`just` alone lists the recipes: `install` runs `./install.sh` with any
arguments passed through (`just install battery --check`), `check` runs the
safe lint gate, `litellm-master-key` prints the admin UI password.

## Adding a role

Roles live under `roles/<group>/<name>/` — `system`, `desktop`, `dev`,
`ai` or `audio`; `site.yml`, the tags and `./install.sh <substring>` all
use the bare role name. Those five groups are the ones `roles_path` in
`ansible.cfg` lists, so a new group needs an entry there too. Drop
`roles/<group>/<name>/` with a `tasks/main.yml` and add it to `site.yml` —
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
- static assets live in `roles/<group>/<name>/files/`
- lint with `ansible-lint --offline` (`just check` runs the whole safe gate) before committing
