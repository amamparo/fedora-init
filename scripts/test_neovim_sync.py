#!/usr/bin/env python3
# Tests for how roles/common/files/neovim_sync.lua turns Mason results into an
# exit status. The user-visible bug: a failed Mason UPGRADE of an installed
# package (kotlin-lsp's vendor URL 404ing) exited 1 and aborted the whole
# ./install.sh from the first role. The rule now: a failed upgrade whose old
# build is intact (receipt still names the old version) is a one-line WARNING
# and exit 0; a failed first install, or an upgrade that lost the old build,
# stays fatal (exit 1, no sentinel).
#
# The script is a top-level program that mutates the real Neovim install, so
# it cannot be imported: this extracts four marked blocks from the real file
# (helpers, finish, install, job-result) and runs them in `nvim -l` against
# mock packages, with XDG_STATE_HOME in a scratch dir so the sentinel the
# script writes lands there. Skipped without nvim. Run by `just check`:
#   python3 -m unittest discover -s scripts -p 'test_*.py'

import os
import re
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPT = os.path.join(REPO, "roles", "common", "files", "neovim_sync.lua")
NVIM = shutil.which("nvim")


def block(name):
    with open(SCRIPT) as f:
        text = f.read()
    m = re.search(r"-- BEGIN %s\n(.*?)-- END %s\n" % (name, name), text, re.S)
    assert m, "marker block %r missing from neovim_sync.lua" % name
    return m.group(1)


PRELUDE = """
local mode = "update"
local failures, warnings, skipped, changed = {}, {}, {}, false
%(helpers)s
%(finish)s
local toolchains = {}
%(install)s
local function fake(name, o)
  return {
    name = name,
    spec = { source = { id = "pkg:generic/" .. name .. "@1" } },
    is_installing = function() return false end,
    is_installed = function() return o.installed end,
    get_installed_version = function() return o.version end,
    install = function(_, _, cb) cb(o.ok, o.err) end,
  }
end
%(scenario)s
%(loop)s
finish()
"""

KOTLIN_404 = (
    'install(fake("kotlin-lsp", {installed = true, version = "v1", ok = false,'
    ' err = "spawn: wget failed with exit code 8.\\nFailed to download file x"}),'
    ' "upgrading (v1 -> v2)", "v1")\n'
)
PRETTIER_OK = (
    'install(fake("prettier", {installed = true, version = "v2", ok = true}),'
    ' "upgrading (v1 -> v2)", "v1")\n'
)


def run_scenario(scenario):
    """Run the extracted blocks; return (returncode, stdout, sentinel_exists)."""
    src = PRELUDE % {
        "helpers": block("helpers"),
        "finish": block("finish"),
        "install": block("install"),
        "loop": block("job-result"),
        "scenario": scenario,
    }
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "harness.lua")
        with open(path, "w") as f:
            f.write(src)
        env = dict(os.environ, XDG_STATE_HOME=os.path.join(d, "state"))
        r = subprocess.run(
            [NVIM, "--headless", "-u", "NONE", "-l", path],
            capture_output=True, text=True, timeout=60, env=env,
        )
        sentinel = os.path.exists(os.path.join(d, "state", "nvim", "fedora-init-bootstrap"))
    return r.returncode, r.stdout, sentinel


@unittest.skipUnless(NVIM, "nvim not installed — neovim_sync.lua tests skipped")
class MasonJobResults(unittest.TestCase):
    def test_failed_upgrade_with_intact_old_build_warns_and_exits_zero(self):
        rc, out, sentinel = run_scenario(KOTLIN_404 + PRETTIER_OK)
        self.assertEqual(rc, 0, out)
        warn = [x for x in out.splitlines() if x.startswith("fedora-init: WARNING ")]
        self.assertEqual(len(warn), 1, out)
        self.assertIn("kotlin-lsp upgrade failed, kept v1", warn[0])
        # one record per line: the multi-line Mason error is folded into it
        self.assertIn("Failed to download file x", warn[0])
        self.assertIn("1 upgrade(s) failed, installed versions kept", out)
        self.assertNotIn("FAILED", out)
        # a clean-enough run still writes the first-install sentinel
        self.assertTrue(sentinel)

    def test_failed_first_install_is_fatal_and_writes_no_sentinel(self):
        rc, out, sentinel = run_scenario(
            'install(fake("stylua", {installed = false, ok = false, err = "boom"}), "installing")\n'
        )
        self.assertEqual(rc, 1, out)
        self.assertIn("FAILED Mason stylua: boom", out)
        self.assertFalse(sentinel)

    def test_failed_install_is_fatal_even_if_something_is_on_disk(self):
        # no `from`: this was a fresh install, not an upgrade. The second case
        # (directory present, no receipt) is the one a missing `from ~= nil`
        # guard would wave through, because nil == nil.
        for version in ('"v1"', "nil"):
            with self.subTest(version=version):
                rc, out, sentinel = run_scenario(
                    'install(fake("ruff", {installed = true, version = %s, ok = false, err = "half"}),'
                    ' "installing")\n' % version
                )
                self.assertEqual(rc, 1, out)
                self.assertNotIn("WARNING", out)
                self.assertFalse(sentinel)

    def test_failed_upgrade_that_lost_the_old_build_is_fatal(self):
        for case in (
            '{installed = false, version = nil, ok = false, err = "gone"}',  # tree removed
            '{installed = true, version = nil, ok = false, err = "no receipt"}',  # receipt lost
            '{installed = true, version = "v2", ok = false, err = "promoted"}',  # different build
        ):
            with self.subTest(case=case):
                rc, out, sentinel = run_scenario(
                    'install(fake("biome", %s), "upgrading (v1 -> v2)", "v1")\n' % case
                )
                self.assertEqual(rc, 1, out)
                self.assertNotIn("WARNING", out)
                self.assertFalse(sentinel)

    def test_reported_success_with_nothing_installed_is_fatal(self):
        rc, out, _ = run_scenario(
            'install(fake("just-lsp", {installed = false, ok = true}), "installing")\n'
        )
        self.assertEqual(rc, 1, out)
        self.assertIn("did not install", out)

    def test_one_real_failure_still_fails_the_run_and_keeps_the_warning_visible(self):
        rc, out, sentinel = run_scenario(
            KOTLIN_404
            + 'install(fake("stylua", {installed = false, ok = false, err = "boom"}), "installing")\n'
        )
        self.assertEqual(rc, 1, out)
        self.assertIn("WARNING Mason kotlin-lsp", out)
        self.assertIn("FAILED Mason stylua", out)
        self.assertFalse(sentinel)

    def test_clean_run_is_silent_about_warnings(self):
        rc, out, _ = run_scenario(PRETTIER_OK)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("WARNING", out)


@unittest.skipUnless(NVIM, "nvim not installed — neovim_sync.lua tests skipped")
class ScriptSanity(unittest.TestCase):
    def test_script_compiles(self):
        # loadfile only parses, so the real script (which mutates the live
        # Neovim install) is never run. A runner script, because a failing
        # `-c` command does not change nvim's exit status.
        with tempfile.TemporaryDirectory() as d:
            runner = os.path.join(d, "compile.lua")
            with open(runner, "w") as f:
                f.write(
                    "local f, e = loadfile(%r)\n"
                    "if not f then io.stderr:write(e .. '\\n') os.exit(1) end\n" % SCRIPT
                )
            r = subprocess.run(
                [NVIM, "--headless", "-u", "NONE", "-l", runner],
                capture_output=True, text=True, timeout=60,
            )
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_broken_script_would_be_caught(self):
        with tempfile.TemporaryDirectory() as d:
            runner = os.path.join(d, "compile.lua")
            broken = os.path.join(d, "broken.lua")
            with open(broken, "w") as f:
                f.write("local x = = 1\n")
            with open(runner, "w") as f:
                f.write(
                    "local f, e = loadfile(%r)\n"
                    "if not f then io.stderr:write(e .. '\\n') os.exit(1) end\n" % broken
                )
            r = subprocess.run(
                [NVIM, "--headless", "-u", "NONE", "-l", runner],
                capture_output=True, text=True, timeout=60,
            )
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
