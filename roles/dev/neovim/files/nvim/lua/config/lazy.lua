-- fedora-init — repo-owned (roles/dev/neovim/files/nvim): edit it there, not in ~.
--
-- LazyVim's starter lua/config/lazy.lua with three deliberate deltas:
--   1. the lazy.nvim bootstrap fails fast when headless (the starter waits
--      for a keypress, which would hang an Ansible task forever on a
--      network failure);
--   2. every LazyVim extra is declared HERE, in code (see below);
--   3. the update checker is off — `./install.sh updates` owns updates
--      (roles/common/files/neovim_sync.lua, update mode).

local lazypath = vim.fn.stdpath("data") .. "/lazy/lazy.nvim"
if not (vim.uv or vim.loop).fs_stat(lazypath) then
  local lazyrepo = "https://github.com/folke/lazy.nvim.git"
  local out = vim.fn.system({ "git", "clone", "--filter=blob:none", "--branch=stable", lazyrepo, lazypath })
  if vim.v.shell_error ~= 0 then
    vim.api.nvim_echo({
      { "Failed to clone lazy.nvim:\n", "ErrorMsg" },
      { out, "WarningMsg" },
    }, true, {})
    -- No UI attached = headless (the playbook's sync run): exit non-zero
    -- right away instead of blocking on getchar() with no terminal.
    if #vim.api.nvim_list_uis() > 0 then
      vim.api.nvim_echo({ { "\nPress any key to exit..." } }, true, {})
      vim.fn.getchar()
    end
    os.exit(1)
  end
end
vim.opt.rtp:prepend(lazypath)

require("lazy").setup({
  spec = {
    { "LazyVim/LazyVim", import = "lazyvim.plugins" },
    -- Extras are declared in code, not through :LazyExtras, so the
    -- selection lives in the repo: :LazyExtras persists to lazyvim.json,
    -- which LazyVim itself rewrites at runtime (news state, migrations) and
    -- the role therefore leaves unmanaged. They go HERE, between the
    -- LazyVim import and `plugins` — LazyVim warns on any other order, and
    -- an extras import inside lua/plugins/*.lua always trips that warning
    -- (don't "fix" this toward the docs' examples page, which does that).
    --
    -- Code-declared extras skip :LazyExtras' priority sort
    -- (lazyvim/plugins/xtras.lua), so they are written in its order:
    -- lang.typescript (prio 5), formatting.prettier (10), then the rest
    -- (default 50) alphabetically.
    { import = "lazyvim.plugins.extras.lang.typescript" }, -- vtsls
    { import = "lazyvim.plugins.extras.formatting.prettier" }, -- gated by lazyvim_prettier_needs_config
    { import = "lazyvim.plugins.extras.ai.claudecode" }, -- coder/claudecode.nvim (lua/plugins/claudecode.lua)
    { import = "lazyvim.plugins.extras.lang.ansible" }, -- ansiblels + ansible-lint
    { import = "lazyvim.plugins.extras.lang.docker" }, -- dockerls, compose, hadolint
    { import = "lazyvim.plugins.extras.lang.git" }, -- gitcommit/gitrebase/gitignore parsers
    { import = "lazyvim.plugins.extras.lang.go" }, -- gopls, goimports, gofumpt, golangci-lint
    { import = "lazyvim.plugins.extras.lang.json" }, -- jsonls + SchemaStore
    { import = "lazyvim.plugins.extras.lang.kotlin" }, -- server swapped to kotlin_lsp (lua/plugins/lsp.lua)
    { import = "lazyvim.plugins.extras.lang.markdown" }, -- marksman, markdownlint, render-markdown
    { import = "lazyvim.plugins.extras.lang.python" }, -- basedpyright (options.lua) + ruff
    { import = "lazyvim.plugins.extras.lang.rust" }, -- rustaceanvim; rust-analyzer comes from the toolchain
    { import = "lazyvim.plugins.extras.lang.toml" }, -- taplo
    { import = "lazyvim.plugins.extras.lang.typescript.biome" }, -- attaches only where biome.json exists
    { import = "lazyvim.plugins.extras.lang.yaml" }, -- yamlls + SchemaStore
    { import = "lazyvim.plugins.extras.linting.eslint" }, -- attaches only where an eslint config exists
    { import = "lazyvim.plugins.extras.util.dot" }, -- bashls + shellcheck
    { import = "plugins" },
  },
  defaults = {
    -- The starter's defaults: only LazyVim's own plugins lazy-load, and
    -- plugins track their latest commit rather than (often stale) tags.
    lazy = false,
    version = false,
  },
  install = { colorscheme = { "moonfly", "habamax" } },
  -- The starter polls every plugin's git remote hourly; updates are
  -- `./install.sh updates`' job here (a headless lazy sync + parser and
  -- Mason upgrades), so the in-editor checker stays off.
  checker = { enabled = false },
  -- lua/ is a repo mirror — edits arrive from ./install.sh, where a reload
  -- prompt mid-session is noise.
  change_detection = { notify = false },
  -- Nothing in this config needs luarocks; off also keeps hererocks from
  -- being bootstrapped and :checkhealth lazy from warning about it.
  rocks = { enabled = false },
  -- Headless runs are the playbook's sync, whose output lands in Ansible:
  -- keep git output and errors, drop the per-task start/finish lines (two
  -- per plugin per step) and the ANSI colors.
  headless = { task = false, colors = false },
  performance = {
    rtp = {
      disabled_plugins = {
        "gzip",
        "tarPlugin",
        "tohtml",
        "tutor",
        "zipPlugin",
      },
    },
  },
})
