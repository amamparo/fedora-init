#!/usr/bin/env bash
#
# fedora-init — run everything:      ./install.sh
#               run some concerns:   ./install.sh battery zsh
#               prove idempotency:   ./install.sh --check
#               no checkout:         curl -fsSL https://raw.githubusercontent.com/amamparo/fedora-init/main/install.sh | bash
#
# Bootstraps its own toolchain (ansible-core + collections, all stock Fedora
# rpms) and runs site.yml locally. Bare arguments select roles by substring,
# like the old per-module filenames; dash arguments (--check, --diff, --tags,
# -v...) pass through to ansible-playbook.
#
set -euo pipefail

# Piped from curl (or a stray lone install.sh): fetch the tarball into a
# temp dir, run from that copy, clean up afterwards. BASH_SOURCE is unset
# when bash reads the script from stdin, hence the :-.
script="${BASH_SOURCE[0]:-}"
if [[ ! -f $script || ! -f "$(dirname "$script")/site.yml" ]]; then
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    curl -fsSL https://github.com/amamparo/fedora-init/archive/main.tar.gz | tar xz -C "$tmp"
    bash "$tmp/fedora-init-main/install.sh" "$@"
    exit
fi

cd "$(dirname "$script")"

# Under curl|bash stdin is the exhausted pipe; the sudo prompt below and the
# login-keyring password prompt (an ansible pause task) both need the
# terminal, so reattach.
[[ -t 0 ]] || exec </dev/tty

# Authenticate sudo once, interactively — password or fingerprint both work
# here. Ansible's per-task sudo can NOT be fed a password on this setup:
# Fedora's fingerprint-first PAM stack (pam_fprintd before pam_unix) blocks
# inside the fingerprint wait and never reaches a password prompt in
# ansible's tty-less become context (verified by probing sudo's conversation
# in exactly that context — silence for 12s+, while ansible gives up at 10).
# So instead of a become password, sudo keeps ONE per-user timestamp record
# (the drop-in below) and every become call rides this authentication.
sudo -v

# sudo's default tty-scoped timestamp records can never match ansible's
# become calls (each runs on a fresh throwaway pty), so scope the record to
# the user instead. Deliberate trade-off on a single-user machine: while a
# record is live (5 min; keepalive below), any process of yours can sudo
# without re-auth — the tty isolation of sudo tickets is gone. Remove
# /etc/sudoers.d/fedora-init to restore stock behavior.
dropin='Defaults timestamp_type=global'
if [[ "$(sudo cat /etc/sudoers.d/fedora-init 2>/dev/null)" != "$dropin" ]]; then
    tmpf="$(mktemp)"
    printf '%s\n' "$dropin" > "$tmpf"
    visudo -cf "$tmpf"  # never install an unparseable sudoers file
    sudo install -m 0440 -o root -g root "$tmpf" /etc/sudoers.d/fedora-init
    rm -f "$tmpf"
    # The auth above wrote a tty-scoped record; the type just changed, so
    # authenticate once more to write the global one (first run only).
    sudo -k
    echo "sudo timestamp policy installed — authenticate once more:"
    sudo -v
fi

# Keep the record fresh: first-run dnf downloads can outlast sudo's 5-minute
# cache, and a lapsed record would make become's non-interactive sudo fail.
( while sleep 60; do sudo -n -v || exit; done ) &
SUDO_KEEPALIVE=$!
trap 'kill "$SUDO_KEEPALIVE" 2>/dev/null' EXIT

# Toolchain bootstrap, guarded so converged re-runs stay offline. All stock
# Fedora packages: ansible-core + the two collections the roles use,
# python3-libdnf5 (dnf5 module backend), python3-psutil (community.general
# dconf locates the session bus with it), rsync (synchronize).
pkgs=(ansible-core ansible-collection-community-general
      ansible-collection-ansible-posix python3-libdnf5 python3-psutil rsync)
if ! rpm -q "${pkgs[@]}" >/dev/null 2>&1; then
    # A real mutation even under --check: the toolchain has to exist before
    # ansible can dry-run anything. Say so instead of pretending otherwise.
    [[ " $* " == *" --check "* ]] \
        && echo "note: --check still bootstraps the ansible toolchain itself (${#pkgs[@]} rpms)"
    sudo dnf install -y "${pkgs[@]}"
fi

# Map bare arguments onto role tags by substring (tags are role dir names
# with underscores as hyphens; see site.yml). Dash arguments pass through —
# including the separate value word of options that take one.
tags=() passthru=() expect_value=0
for arg in "$@"; do
    if ((expect_value)); then
        passthru+=("$arg")
        expect_value=0
        continue
    fi
    if [[ $arg == -* ]]; then
        passthru+=("$arg")
        # ansible-playbook options whose value is a separate argument (the
        # --opt=value form needs no entry here). Without this, `--tags foo`
        # would substring-match "foo" against role names below.
        case $arg in
            -t|--tags|--skip-tags|-l|--limit|-e|--extra-vars|--start-at-task|-i|--inventory)
                expect_value=1 ;;
        esac
        continue
    fi
    matched=0
    for d in roles/*/; do
        role="$(basename "$d")"
        [[ $role == common ]] && continue
        tag="${role//_/-}"
        [[ $tag == *"$arg"* ]] && { tags+=("$tag"); matched=1; }
    done
    ((matched)) || { echo "No role matches: $arg" >&2; exit 1; }
done

args=(--diff)
((${#tags[@]})) && args+=(--tags "$(IFS=,; echo "${tags[*]}")")

# No become password anywhere: each per-task sudo authenticates against the
# global timestamp primed above. Become's default -n flag means a lapsed
# record fails fast and loud instead of hanging in PAM.
ansible-playbook site.yml "${args[@]}" "${passthru[@]}"

echo
echo "Done. Log out and back in so GNOME picks up the extensions."
