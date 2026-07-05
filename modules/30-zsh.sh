#!/usr/bin/env bash
#
# zsh: make zsh the login shell, install oh-my-zsh (git plugin only).
#
set -euo pipefail

sudo dnf install -y zsh git curl

# oh-my-zsh, unattended (no shell switch, no zsh exec)
if [[ ! -d "$HOME/.oh-my-zsh" ]]; then
    sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" \
        "" --unattended --keep-zshrc
fi

# Our .zshrc: robbyrussell theme, plugins=(git) and nothing else
install -m 0644 "$REPO_ROOT/files/zsh/zshrc" "$HOME/.zshrc"

# Default shell
sudo usermod --shell "$(command -v zsh)" "$USER"

echo "zsh is now the default shell (takes effect on next login)."
