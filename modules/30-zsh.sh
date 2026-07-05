#!/usr/bin/env bash
#
# zsh: make zsh the login shell, install oh-my-zsh (git plugin only).
#
set -euo pipefail
REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

sudo dnf install -y zsh git

# oh-my-zsh via shallow clone — that's all the official installer does under
# --unattended --keep-zshrc, and unlike curl|sh a failed clone aborts loudly
# instead of leaving a shell that sources a missing oh-my-zsh.sh forever.
# Guard on the payload file, not the directory: a dir without oh-my-zsh.sh is
# a broken half-install, so move it aside and reclone.
if [[ ! -f "$HOME/.oh-my-zsh/oh-my-zsh.sh" ]]; then
    if [[ -d "$HOME/.oh-my-zsh" ]]; then
        rm -rf "$HOME/.oh-my-zsh.broken"
        mv "$HOME/.oh-my-zsh" "$HOME/.oh-my-zsh.broken"
        echo "warning: broken ~/.oh-my-zsh moved aside to ~/.oh-my-zsh.broken"
    fi
    git clone --depth=1 https://github.com/ohmyzsh/ohmyzsh.git "$HOME/.oh-my-zsh"
fi

# Our .zshrc: robbyrussell theme, plugins=(git) and nothing else.
# One-shot backup if an existing .zshrc would be overwritten with changes.
if [[ -f "$HOME/.zshrc" ]] && ! cmp -s "$HOME/.zshrc" "$REPO_ROOT/files/zsh/zshrc"; then
    cp -a "$HOME/.zshrc" "$HOME/.zshrc.pre-fedora-init"
    echo "existing ~/.zshrc backed up to ~/.zshrc.pre-fedora-init"
fi
install -m 0644 "$REPO_ROOT/files/zsh/zshrc" "$HOME/.zshrc"

# Default shell
sudo usermod --shell "$(command -v zsh)" "$USER"

echo "zsh is now the default shell (takes effect on next login)."
