-- fedora-init — repo-owned (roles/dev/neovim/files/nvim): edit it there, not in ~.
--
-- Overrides for LazyVim's ai.claudecode extra (coder/claudecode.nvim). The
-- plugin speaks the same WebSocket/MCP protocol as Claude Code's VS Code
-- extension: Claude sees the current buffer/selection and proposes edits as
-- native diffs (accept with :w or <leader>aa, reject with :q or <leader>ad).
return {
  {
    "coder/claudecode.nvim",
    -- The extra only defines `keys`, so the plugin — and with it the
    -- WebSocket server and the ~/.claude/ide/<port>.lock that `/ide` looks
    -- for — wouldn't exist until a <leader>a key was pressed, and a Claude
    -- Code running in a separate herdr pane would find no IDE to connect
    -- to. VeryLazy loads it right after startup in every UI session, and
    -- never fires headless, so the playbook's sync runs leave no lockfile.
    -- The default snacks terminal provider stays: <leader>ac still opens
    -- Claude in a split for in-editor use.
    event = "VeryLazy",
    keys = {
      -- The extra lists only NvimTree/neo-tree/oil; add the explorers this
      -- config actually has (snacks explorer, mini.files, netrw), as
      -- claudecode.nvim's own README spec does. lazy.nvim keys a mapping by
      -- lhs + ft, so the extra's narrower entry is removed explicitly
      -- rather than left to double-map the same key.
      { "<leader>as", false, ft = { "NvimTree", "neo-tree", "oil" } },
      {
        "<leader>as",
        "<cmd>ClaudeCodeTreeAdd<cr>",
        desc = "Add file",
        ft = { "NvimTree", "neo-tree", "oil", "minifiles", "netrw", "snacks_picker_list" },
      },
    },
  },
}
