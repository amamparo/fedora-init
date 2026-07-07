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

# One password prompt for the whole run. Ansible re-supplies it to every
# per-task sudo (privileged tasks opt in with become), which replaces the old
# background `sudo -n -v` keepalive loop — sudo's tty-keyed timestamps
# wouldn't cover Ansible's tty-less sudo invocations anyway.
IFS= read -rs -p "[sudo] password for $USER (Enter if passwordless): " SUDO_PW < /dev/tty
echo

# Ansible strips leading/trailing whitespace when reading the become
# password file, so such a password would validate here and then fail every
# become task mid-play — reject it up front with a real explanation.
if [[ $SUDO_PW =~ ^[[:space:]] || $SUDO_PW =~ [[:space:]]$ ]]; then
    echo "error: a sudo password with leading/trailing whitespace cannot be" >&2
    echo "passed to ansible (it strips password-file edges) — change it first" >&2
    exit 1
fi

# Validate now, in the SAME PAM context ansible's become will use: setsid +
# fully detached stdio means no terminal anywhere, so pam_fprintd (which
# runs before pam_unix on fingerprint-enrolled Fedora) bails out instead of
# engaging — a fingerprint touch can satisfy an interactive `sudo -v` at
# the terminal, but it can never satisfy ansible's background sudo calls,
# so validating at the terminal would accept setups the play then fails on.
if [[ -z $SUDO_PW ]]; then
    setsid sudo -n -v </dev/null >/dev/null 2>&1 || {
        echo "sudo needs a password on this machine: a fingerprint satisfies" >&2
        echo "interactive sudo but cannot authorize ansible's background sudo" >&2
        echo "calls — re-run and type your password." >&2
        exit 1
    }
else
    printf '%s\n' "$SUDO_PW" | setsid sudo -S -p '' -v >/dev/null 2>&1 \
        || { echo "sudo: authentication failed" >&2; exit 1; }
fi

# Toolchain bootstrap, guarded so converged re-runs stay offline. All stock
# Fedora packages: ansible-core + the two collections the roles use,
# python3-libdnf5 (dnf5 module backend), python3-psutil (community.general
# dconf locates the session bus with it), rsync (synchronize). This plain
# sudo runs at the terminal, so on a fingerprint-enrolled machine the first
# run may show "Place your finger on the fingerprint reader" here — either
# touch it or wait for the password fallback.
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

# The become password goes to ansible in a 0600 file on /tmp (tmpfs on
# Fedora Workstation, so memory-backed), removed on exit — never argv or
# env. Not a /dev/fd process substitution: ansible canonicalizes the path
# (unfrackpath) before opening it, which resolves an fd symlink to the
# pseudo-name "pipe:[inode]" and dies with "password file not found". An
# empty password (passwordless sudo) omits the flag entirely: ansible
# refuses an empty password file.
if [[ -n $SUDO_PW ]]; then
    pwfile="$(mktemp)"  # mktemp creates 0600
    trap 'rm -f "$pwfile"' EXIT
    printf '%s\n' "$SUDO_PW" > "$pwfile"
    ansible-playbook site.yml "${args[@]}" "${passthru[@]}" \
        --become-password-file "$pwfile"
else
    ansible-playbook site.yml "${args[@]}" "${passthru[@]}"
fi

echo
echo "Done. Log out and back in so GNOME picks up the extensions."
