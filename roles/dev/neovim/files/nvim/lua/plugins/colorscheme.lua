-- fedora-init — repo-owned (roles/dev/neovim/files/nvim): edit it there, not in ~.
--
-- Moonfly, matching the terminal: roles/desktop/ghostty sets `theme =
-- Moonfly`, and this is the same palette's Neovim port (bluz71). It styles
-- treesitter, LSP semantic tokens, diagnostics, blink.cmp, snacks, mason,
-- gitsigns, noice and lualine (whose theme it autoloads).
return {
  {
    "bluz71/vim-moonfly-colors",
    name = "moonfly",
    lazy = false,
    priority = 1000,
  },
  {
    "LazyVim/LazyVim",
    opts = { colorscheme = "moonfly" },
  },
}
