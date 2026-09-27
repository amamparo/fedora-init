-- fedora-init — installed to ~/.config/nvim/init.lua (roles/dev/neovim).
-- The whole ~/.config/nvim tree is a repo-owned MIRROR: edit
-- roles/dev/neovim/files/nvim/ in the repo, never ~/.config/nvim — a re-run
-- overwrites changes there and deletes files the repo doesn't have (only
-- lazy-lock.json and lazyvim.json, which lazy.nvim/LazyVim write at runtime,
-- are left alone).
require("config.lazy")
