-- fedora-init — repo-owned (roles/dev/neovim/files/nvim): edit it there, not in ~.
--
-- Language-server and tooling overrides on top of the extras imported in
-- lua/config/lazy.lua — only where an extra's default is wrong for these
-- projects or this repo.
return {
  {
    "neovim/nvim-lspconfig",
    opts = {
      servers = {
        -- basedpyright defaults to its "recommended" mode, far stricter than
        -- pyright's "standard" — noisy on projects already type-checked by
        -- mypy. Standard keeps pyright-level diagnostics plus basedpyright's
        -- inlay hints and semantic tokens.
        basedpyright = {
          settings = {
            basedpyright = {
              analysis = { typeCheckingMode = "standard" },
            },
          },
        },
        -- Kotlin: lang.kotlin still wires fwcd's kotlin-language-server,
        -- which its own README calls deprecated, embeds Kotlin 2.1 and
        -- fails to resolve Gradle dependencies (LazyVim #6988). JetBrains'
        -- official kotlin-lsp replaces it: alpha, experimental Android
        -- Gradle support, a ~370 MB Mason download with its own bundled
        -- JRE, and 2.5-4.5 GB of RAM while a Kotlin project is open. Its
        -- builds are time-limited and expire ("This build of intellij-server
        -- has expired") — `./install.sh updates` upgrades Mason packages,
        -- which is what keeps it alive.
        kotlin_language_server = { enabled = false },
        kotlin_lsp = {
          -- Left to itself, intellij-server puts its caches and indexes in a
          -- fresh random /tmp/idea-system<N> per launch (verified), so every
          -- nvim start re-imports Gradle and re-indexes the whole project —
          -- minutes of CPU on battery. A stable per-project --system-path
          -- (a documented `intellij-server --help` option) keeps the index
          -- across sessions.
          cmd = function(dispatchers, config)
            local root = config.root_dir or vim.fn.getcwd()
            local system_path = vim.fn.stdpath("cache") .. "/kotlin-lsp/" .. vim.fn.sha256(root):sub(1, 16)
            return vim.lsp.rpc.start({ "intellij-server", "--stdio", "--system-path=" .. system_path }, dispatchers)
          end,
        },
        -- just: no LazyVim extra; just-lsp (Mason) + the treesitter parser
        -- below.
        just = {},
      },
    },
  },
  {
    "nvim-treesitter/nvim-treesitter",
    opts = { ensure_installed = { "just" } },
  },
  {
    "mfussenegger/nvim-ansible",
    keys = {
      -- lang.ansible maps <leader>ta to "run this playbook/role", which runs
      -- `ansible-playbook` / `ansible localhost -m import_role` against THIS
      -- machine (with -K when the file mentions become) — fedora-init's
      -- AGENTS.md forbids running the playbook to test a change, and with
      -- its roles/<group>/<name>/ layout the role regex would even import a
      -- whole group. Removed. (The key id includes its ft, hence ft here.)
      { "<leader>ta", false, ft = "yaml.ansible" },
    },
  },
  {
    "stevearc/conform.nvim",
    optional = true,
    opts = function(_, opts)
      -- lang.markdown runs `markdownlint-cli2 --fix` on save whenever the
      -- buffer has any markdownlint diagnostic — so saving any doc with a
      -- long line (fedora-init's AGENTS.md/README.md are nothing but)
      -- rewrites it. Keep markdownlint as a linter (diagnostics), drop it as
      -- a formatter.
      for _, ft in ipairs({ "markdown", "markdown.mdx" }) do
        opts.formatters_by_ft[ft] = vim.tbl_filter(function(f)
          return f ~= "markdownlint-cli2"
        end, opts.formatters_by_ft[ft] or {})
      end
    end,
  },
}
