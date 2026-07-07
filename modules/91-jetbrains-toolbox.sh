#!/usr/bin/env bash
#
# JetBrains Toolbox from the official tarball (JetBrains ships no rpm). The
# launcher's undocumented --install flag does a headless self-install into
# ~/.local/share/JetBrains/Toolbox — the same layout a first GUI launch
# creates: app bundle, desktop entries, the jetbrains:// URI handler, and an
# autostart entry, so the Toolbox window opens (minimized) at the next login
# for terms/sign-in. Entirely user-level: no sudo, and no FUSE — that was
# the pre-2.6.2 AppImage era; the 3.x bundle needs only glibc.
#
# Toolbox self-updates in place, so the installed launcher's presence is the
# terminal state: re-runs make no network calls, and no version pinning.
# Never run the installer under sudo — it derives the target home from
# getpwuid(), ignoring $HOME, and would install into root's home.
#
set -euo pipefail

TOOLBOX_BIN="$HOME/.local/share/JetBrains/Toolbox/bin/jetbrains-toolbox"

if [[ ! -x "$TOOLBOX_BIN" ]]; then
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT

    # Latest-tarball URL via the download endpoint — a plain 302, no JSON.
    url="$(curl -fsS -o /dev/null -w '%{redirect_url}' 'https://data.services.jetbrains.com/products/download?code=TBA&platform=linux')"
    [[ -n "$url" ]] || { echo "error: JetBrains download endpoint returned no redirect" >&2; exit 1; }

    # Keep the original filename: the .sha256 file references it by name.
    fname="${url##*/}"
    curl -fsSL -o "$tmp/$fname" "$url"
    curl -fsSL -o "$tmp/$fname.sha256" "$url.sha256"
    (cd "$tmp" && sha256sum -c --quiet "$fname.sha256")

    mkdir "$tmp/toolbox"
    tar -xzf "$tmp/$fname" -C "$tmp/toolbox" --strip-components=1

    # Only the bundle copy into Toolbox/bin is synchronous; desktop entries
    # and the daemon are finished by a background child moments after this
    # returns (deleting $tmp under it is fine — Linux keeps deleted-but-open
    # files alive). So assert the binary landed, nothing more.
    "$tmp/toolbox/bin/jetbrains-toolbox" --install
    [[ -x "$TOOLBOX_BIN" ]] || {
        echo "error: --install produced no $TOOLBOX_BIN — the undocumented flag may have changed" >&2
        exit 1
    }
fi

echo "JetBrains Toolbox installed — it opens at next login to sign in and install IDEs."
