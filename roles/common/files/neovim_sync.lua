-- fedora-init — headless Neovim sync, run by roles/common/tasks/neovim_sync.yml
-- (from roles/dev/neovim on first install / config change, and from
-- roles/system/updates) as:
--
--   nvim --headless -u ~/.config/nvim/init.lua -l neovim_sync.lua <mode>
--
-- Modes:
--   plugins  `:Lazy sync` (install + clean + update every plugin), then
--            verify every plugin is on disk. Its own process on purpose: the
--            pass below must read the UPDATED LazyVim specs, and a process
--            that has already loaded LazyVim keeps the old ones.
--   install  finish what LazyVim otherwise does lazily on the first
--            interactive start: treesitter parsers, blink.cmp's prebuilt
--            fuzzy library, and every Mason tool + language server the
--            config asks for — waiting for all of it.
--   update   `install`, plus upgrade outdated parsers and Mason packages.
--
-- Why a script and not `nvim --headless "+Lazy! sync" +qa`: that installs
-- plugins only. Parser and Mason installs are async and die with the
-- process; mason-lspconfig deliberately skips ensure_installed when headless;
-- nvim-treesitter's install():wait() boolean is not trustworthy (a language
-- already compiling is waited on for at most 60 s, and a failed one can read
-- as success); and lazy.nvim exits 0 on failure. So everything here is
-- polled against what is actually on disk, and any failure exits 1 — `-l`
-- turns a Lua error or os.exit(1) into the process status Ansible sees. The
-- one exception is a failed UPGRADE of a Mason package that is already
-- installed: Mason downloads and unpacks into a staging dir and only then
-- replaces the old tree, so a failed download leaves the old build in place,
-- and one broken vendor URL must not abort a whole ./install.sh (this runs
-- from the first role). That case prints a WARNING line and exits 0 — the
-- Ansible side reports it — but only while the old receipt still names the
-- old version; a failed first INSTALL, or an upgrade that lost the old build,
-- stays fatal, so the sentinel is never written over a half-set-up editor.
-- Other downloads (registry refresh, blink's library, parsers) stay fatal.
-- The marked blocks below are what scripts/test_neovim_sync.py runs.
--
-- By the time this runs, init.lua has already done lazy.nvim's startup
-- install-missing (synchronous) — that part has no timeout of its own, which
-- is why the Ansible task wraps the whole process in coreutils `timeout`.
--
-- stdout lines are prefixed "fedora-init:"; the task's changed_when looks for
-- "fedora-init: changed".

local mode = _G.arg[1]
if mode ~= "plugins" and mode ~= "install" and mode ~= "update" then
  io.stderr:write("usage: nvim --headless -u init.lua -l neovim_sync.lua plugins|install|update\n")
  os.exit(2)
end

local failures, warnings, skipped, changed = {}, {}, {}, false

-- BEGIN helpers
local function say(msg)
  io.stdout:write("fedora-init: " .. msg .. "\n")
  io.stdout:flush()
end

local function fail(msg)
  failures[#failures + 1] = msg
  say("FAILED " .. msg)
end

local function warn(msg)
  warnings[#warnings + 1] = msg
  say("WARNING " .. msg)
end

--- Mason errors can span lines; keep each "fedora-init:" record on one line
--- so the Ansible side can pick it out by prefix.
local function oneline(s)
  return (tostring(s):gsub("%s*\n%s*", " "))
end
-- END helpers

local function read(path)
  local f = io.open(path, "rb")
  if not f then
    return nil
  end
  local data = f:read("*a")
  f:close()
  return data
end

--- vim.wait with the event loop running (async installs progress meanwhile).
local function wait(seconds, cond)
  return vim.wait(seconds * 1000, cond, 250)
end

-- BEGIN finish
local function finish()
  if #skipped > 0 then
    say(
      "skipped (toolchain missing — installs on the next interactive start once present): "
        .. table.concat(skipped, ", ")
    )
  end
  if #warnings > 0 then
    say(#warnings .. " upgrade(s) failed, installed versions kept (see WARNING above)")
  end
  if #failures > 0 then
    say(#failures .. " failure(s): " .. table.concat(failures, "; "))
    os.exit(1)
  end
  if mode ~= "plugins" then
    -- The role's first-install gate (`stat`). Written only on a clean run,
    -- so a half-finished first install is retried by the next ./install.sh.
    local state = vim.fn.stdpath("state")
    vim.fn.mkdir(state, "p")
    local sentinel = state .. "/fedora-init-bootstrap"
    if vim.fn.filereadable(sentinel) == 0 then
      vim.fn.writefile({ "written by fedora-init's neovim_sync.lua" }, sentinel)
      changed = true
    end
  end
  if changed then
    say("changed")
  end
  os.exit(0)
end
-- END finish

local Config = require("lazy.core.config")

-- Plugins ------------------------------------------------------------------

if mode == "plugins" then
  local before = read(Config.options.lockfile)
  require("lazy").sync({ wait = true, show = false })
  if read(Config.options.lockfile) ~= before then
    changed = true
    say("plugins updated")
  end
end

for name, plugin in pairs(Config.plugins) do
  if not plugin._.installed then
    fail("plugin " .. name .. " is not installed")
  end
end

if mode == "plugins" or #failures > 0 then
  finish()
end

-- What's on disk BEFORE anything below loads: loading LazyVim's treesitter
-- and mason configs starts their own async installs, which can finish
-- before this script would otherwise look — snapshotting first is what keeps
-- "changed" honest. Read straight from the filesystem (both plugins default
-- to these dirs; LazyVim overrides neither), since neither is loaded yet.

--- name -> "size:mtime" of each entry in dir (or of dir/<name>/<file>).
local function fs_state(dir, file)
  local ret = {}
  if vim.uv.fs_stat(dir) then
    for name in vim.fs.dir(dir) do
      local st = vim.uv.fs_stat(dir .. "/" .. name .. (file and ("/" .. file) or ""))
      ret[name] = st and (st.size .. ":" .. st.mtime.sec .. "." .. st.mtime.nsec) or "?"
    end
  end
  return ret
end

local parser_dir = vim.fn.stdpath("data") .. "/site/parser"
local mason_dir = vim.fn.stdpath("data") .. "/mason/packages"
local parsers_before = fs_state(parser_dir)
local mason_before = fs_state(mason_dir, "mason-receipt.json")

-- blink.cmp fuzzy library ----------------------------------------------------
-- A prebuilt Rust .so fetched from blink's GitHub release (checksum-verified
-- by blink) when blink.cmp's setup runs, and again after each blink version
-- bump. Without it blink falls back to its slower Lua matcher. setup() hands
-- the outcome to fuzzy.set_implementation() from a scheduled callback —
-- 'rust', or 'lua' after a failed download — and implementation_type
-- DEFAULTS to 'lua', so it can't be polled directly: the setter is wrapped
-- right after loading, before the event loop can run that callback.

local blink_lib = Config.plugins["blink.cmp"].dir .. "/target/release/libblink_cmp_fuzzy.so"
local blink_lib_before = read(blink_lib)
require("lazy").load({ plugins = { "blink.cmp" } })
local fuzzy = require("blink.cmp.fuzzy")
local blink_impl ---@type string?
local set_implementation = fuzzy.set_implementation
fuzzy.set_implementation = function(impl)
  blink_impl = impl
  return set_implementation(impl)
end

-- Load what LazyVim otherwise loads on its first file open / `:Mason`.
-- nvim-lspconfig's config is what calls mason-lspconfig.setup() with the
-- filtered server list read below.
require("lazy").load({
  plugins = { "mason.nvim", "nvim-lspconfig", "mason-lspconfig.nvim", "nvim-treesitter" },
})

if not wait(300, function()
  return blink_impl ~= nil
end) or blink_impl ~= "rust" then
  fail("blink.cmp fuzzy library (implementation: " .. tostring(blink_impl) .. ")")
elseif read(blink_lib) ~= blink_lib_before then
  changed = true
  say("blink.cmp fuzzy library downloaded")
end

-- Treesitter parsers --------------------------------------------------------

local TS = require("nvim-treesitter")
local TSConfig = require("nvim-treesitter.config")
local want = LazyVim.dedup(LazyVim.opts("nvim-treesitter").ensure_installed or {})

--- Languages still missing a parser OR its queries. install() treats a
--- language as installed when EITHER exists (an interrupted compile can
--- leave queries without a parser), so both are checked, and a half-install
--- is forced.
local function missing_langs()
  local parsers, queries = TSConfig.get_installed("parsers"), TSConfig.get_installed("queries")
  return vim.tbl_filter(function(lang)
    return not (vim.list_contains(parsers, lang) and vim.list_contains(queries, lang))
  end, want)
end

local missing = missing_langs()
if #missing > 0 then
  say("installing parsers: " .. table.concat(missing, ", "))
  TS.install(missing, { force = true }):wait(20 * 60 * 1000)
end
if mode == "update" then
  TS.update():wait(20 * 60 * 1000)
end
-- LazyVim's own config (loaded above) may still be compiling some of these.
wait(20 * 60, function()
  return #missing_langs() == 0
end)
for _, lang in ipairs(missing_langs()) do
  fail("treesitter parser " .. lang)
end
if not vim.deep_equal(parsers_before, fs_state(parser_dir)) then
  changed = true
end

-- Mason tools + language servers --------------------------------------------

local registry = require("mason-registry")

-- Fetch the current registry (update() forces it; refresh() would trust a
-- cache up to a day old, and "latest version" below must be current).
local reg_done, reg_ok = false, false
registry.update(function(ok)
  reg_done, reg_ok = true, ok
end)
if not wait(300, function()
  return reg_done
end) or not reg_ok then
  fail("mason registry update")
  finish()
end

-- The wanted set is exactly what interactive LazyVim would install: the
-- mason.nvim ensure_installed list (tools) plus the server list LazyVim
-- handed mason-lspconfig.setup() (already filtered for enabled/mason=false
-- servers), mapped from lspconfig names to Mason package names.
local map = require("mason-lspconfig.mappings").get_mason_map().lspconfig_to_package
local wanted = {}
for _, tool in ipairs(LazyVim.opts("mason.nvim").ensure_installed or {}) do
  wanted[#wanted + 1] = tool
end
for _, server in ipairs(require("mason-lspconfig.settings").current.ensure_installed or {}) do
  local pkg = map[server]
  if pkg then
    wanted[#wanted + 1] = pkg
  else
    fail("no Mason package for language server " .. server)
  end
end
wanted = LazyVim.dedup(wanted)

-- Source type -> host toolchain Mason shells out to for it.
local toolchains = {
  npm = "npm",
  golang = "go",
  pypi = "python3",
  cargo = "cargo",
  gem = "gem",
  luarocks = "luarocks",
  nuget = "dotnet",
  composer = "composer",
  opam = "opam",
}

-- BEGIN install
local jobs = {}
--- `from` is the installed version when this is an upgrade, nil for a fresh
--- install; it is what lets the result loop below tell the two apart.
local function install(pkg, why, from)
  local kind = pkg.spec.source.id:match("^pkg:(%w+)/")
  local tool = toolchains[kind]
  if tool and vim.fn.executable(tool) == 0 then
    skipped[#skipped + 1] = pkg.name .. " (needs " .. tool .. ")"
    return
  end
  if pkg:is_installing() then
    -- LazyVim's mason config (loaded above) already started it: just wait.
    jobs[#jobs + 1] = { pkg = pkg, done = true }
    return
  end
  say(why .. " " .. pkg.name)
  local job = { pkg = pkg, done = false, from = from }
  jobs[#jobs + 1] = job
  pkg:install({}, function(ok, err)
    job.done, job.ok, job.err = true, ok, err
  end)
end
-- END install

for _, name in ipairs(wanted) do
  local ok, pkg = pcall(registry.get_package, name)
  if not ok then
    fail("unknown Mason package " .. name)
  elseif not pkg:is_installed() then
    install(pkg, "installing")
  elseif mode == "update" and pkg:get_installed_version() ~= pkg:get_latest_version() then
    install(
      pkg,
      "upgrading (" .. tostring(pkg:get_installed_version()) .. " -> " .. pkg:get_latest_version() .. ")",
      pkg:get_installed_version()
    )
  end
end

wait(30 * 60, function()
  for _, job in ipairs(jobs) do
    if not job.done or job.pkg:is_installing() then
      return false
    end
  end
  return true
end)

-- BEGIN job-result
--- True while the package's receipt still names `version`: the strongest
--- "the old build is intact" signal Mason gives (is_installed() alone is only
--- a directory stat, and the receipt is written last and removed with the tree).
local function still_on(pkg, version)
  local ok, current = pcall(pkg.get_installed_version, pkg)
  return ok and current == version
end

for _, job in ipairs(jobs) do
  if job.ok == false then
    if job.from ~= nil and job.pkg:is_installed() and still_on(job.pkg, job.from) then
      warn("Mason " .. job.pkg.name .. " upgrade failed, kept " .. tostring(job.from) .. ": " .. oneline(job.err))
    else
      fail("Mason " .. job.pkg.name .. ": " .. oneline(job.err))
    end
  elseif not job.pkg:is_installed() then
    fail("Mason " .. job.pkg.name .. " did not install")
  end
end
-- END job-result

if not vim.deep_equal(mason_before, fs_state(mason_dir, "mason-receipt.json")) then
  changed = true
end

finish()
