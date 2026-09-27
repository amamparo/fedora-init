-- fedora-init — repo-owned (roles/dev/neovim/files/nvim): edit it there, not in ~.
--
-- Loaded by LazyVim before its own defaults (lazyvim.config.init) and —
-- load-bearing for the two vim.g switches below — BEFORE the extras in
-- lua/config/lazy.lua are imported: lang.python reads lazyvim_python_lsp at
-- module load. Everything not set here is LazyVim's default
-- (lazyvim/config/options.lua: relativenumber, clipboard=unnamedplus,
-- autoformat on save, ...), deliberately — the distro's defaults ARE the
-- community-consensus settings this config opts into.

-- Python: basedpyright rather than LazyVim's default pyright. It installs
-- from PyPI (bundles its own node, so no npm), adds the Pylance-only
-- features pyright lacks (inlay hints, semantic highlighting), and picks up
-- a project-root .venv (poetry's in-project venv) with no venv-selector
-- step. Its type-checking strictness is lowered in lua/plugins/lsp.lua.
vim.g.lazyvim_python_lsp = "basedpyright"

-- Prettier only where a project has a prettier config. Without this,
-- formatting.prettier runs on every js/ts/json/yaml/markdown save — after
-- biome in biome repos, and over the YAML/Markdown of repos that never
-- chose prettier (this one included).
vim.g.lazyvim_prettier_needs_config = true

-- No remote-plugin providers: nothing here uses them, and Fedora's
-- sysinit.vim points python3_host_prog at /usr/bin/python3 while pynvim is
-- only a Suggests of the rpm, so :checkhealth would warn about all four.
vim.g.loaded_python3_provider = 0
vim.g.loaded_node_provider = 0
vim.g.loaded_perl_provider = 0
vim.g.loaded_ruby_provider = 0

-- PATH fallbacks for an nvim that didn't start from zsh (a GNOME launcher
-- gets gnome-shell's bare /usr/local/bin:/usr/bin). ~/.local/bin holds
-- `claude` (claudecode.nvim's terminal) and lazygit; mise's shims hold `go`,
-- which Mason's gopls/goimports/gofumpt installs and gopls itself need.
-- APPENDED, never prepended: inside a mise-activated shell the real paths
-- already come first, and a shim ahead of /usr/bin would shadow the system
-- npm with mise's npm shim, which errors outside a node-pinned project
-- ("No version is set for shim: npm") and would break every Mason npm
-- install. Mason prepends its own bin dir on top of all of this.
for _, dir in ipairs({ vim.env.HOME .. "/.local/bin", vim.env.HOME .. "/.local/share/mise/shims" }) do
  if not vim.tbl_contains(vim.split(vim.env.PATH or "", ":", { plain = true }), dir) then
    vim.env.PATH = (vim.env.PATH or "") .. ":" .. dir
  end
end

-- fedora-init's own playbook: nvim-ansible's ftdetect catches role
-- tasks/handlers but not a top-level site.yml, which would otherwise be
-- plain yaml (yamlls, no ansiblels).
vim.filetype.add({ filename = { ["site.yml"] = "yaml.ansible" } })
