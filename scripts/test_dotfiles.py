#!/usr/bin/env python3
# Unit + CLI tests for the dotfiles round trip (module_utils/dotfiles_common.py,
# scripts/dotfiles.py). Everything runs in a scratch HOME and a scratch git
# repo with shim `bw`/`dconf`/`gsettings`/`code`/`dnf5` binaries on PATH — it
# never touches the real home, the real vault or the real repo. Run by
# `just check`:   python3 -m unittest discover -s scripts -p 'test_*.py'
#
# The scan tests need the gitleaks binary (PATH, or GITLEAKS_BIN); without it
# they are skipped and say so — `just save` itself refuses to run unscanned.

import contextlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "module_utils"))
import dotfiles_common as dc  # noqa: E402

_spec = importlib.util.spec_from_file_location("dotfiles_cli", os.path.join(HERE, "dotfiles.py"))
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)

def rd(path, mode="r"):
    with open(path, mode) as f:
        return f.read()


def wr(path, data, mode="w"):
    with open(path, mode) as f:
        f.write(data)


GITLEAKS = os.environ.get("GITLEAKS_BIN") or shutil.which("gitleaks")
# Shaped like a real GitHub PAT (ghp_ + 36 alphanumerics) so the default
# ruleset flags it, but obviously fake.
FAKE_TOKEN = "ghp_" + "aB3dE5gH7jK9mN1pQ3rS5tU7vW9xY1zA3bC5"
SECRET_BYTES = b"\x00\x01LICENCE-KEY-DO-NOT-LEAK\xff\xfe" + bytes(range(256))


class Sandbox(unittest.TestCase):
    """Scratch HOME + bin dir with shims; restores the environment after."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dotfiles-test-")
        self.home = os.path.join(self.tmp, "home")
        self.bin = os.path.join(self.tmp, "bin")
        self.fake = os.path.join(self.tmp, "fake")
        for d in (self.home, self.bin, self.fake):
            os.makedirs(d)
        self._env = dict(os.environ)
        os.environ["HOME"] = self.home
        os.environ["FAKE"] = self.fake
        os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        extra = [self.bin]
        if GITLEAKS:
            extra.append(os.path.dirname(GITLEAKS))
        os.environ["PATH"] = os.pathsep.join(extra + [self._env.get("PATH", "")])
        self.addCleanup(self._restore)

    def _restore(self):
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def shim(self, name, body, python=False):
        path = os.path.join(self.bin, name)
        head = "#!/usr/bin/env python3\n" if python else "#!/usr/bin/env bash\n"
        with open(path, "w") as f:
            f.write(head + textwrap.dedent(body))
        os.chmod(path, 0o755)

    def live(self, rel, data=b"", mode=0o644):
        path = os.path.join(self.home, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode())
        os.chmod(path, mode)
        return path


# ======================================================================
class PureHelpers(Sandbox):
    def test_jsonc_comments_trailing_commas_and_strings(self):
        text = '{\n // line\n "a": "x // not a comment", /* block */\n "b": [1, 2,],\n}\n'
        self.assertEqual(dc.parse_json(text), {"a": "x // not a comment", "b": [1, 2]})

    def test_unparseable_json_fails_closed(self):
        with self.assertRaises(dc.DotfilesError):
            dc.parse_json("{not json", "settings")

    def test_drop_keys_keeps_flat_dotted_keys_and_removes_listed(self):
        item = {"id": "x", "kind": "file", "drop_keys": ["chat.instructions", "a/b"]}
        raw = b'{"editor.fontSize": 14, "chat.instructions": {"/tmp/x": true}, "a": {"b": 1, "c": 2}}'
        out = json.loads(dc.render_for_repo(item, raw))
        self.assertEqual(out, {"editor.fontSize": 14, "a": {"c": 2}})

    def test_home_token_round_trip_and_collision(self):
        item = {"id": "x", "kind": "file", "home_template": True}
        raw = ('{"p": "%s/proj"}' % self.home).encode()
        saved = dc.render_for_repo(item, raw)
        self.assertNotIn(self.home.encode(), saved)
        self.assertIn(dc.HOME_TOKEN.encode(), saved)
        self.assertEqual(dc.render_for_live(item, saved), raw)
        with self.assertRaises(dc.DotfilesError):
            dc.render_for_repo(item, dc.HOME_TOKEN.encode())

    def test_json_keys_extracts_only_allowlisted_paths(self):
        item = {"id": "x", "kind": "json-keys", "keys": ["model", "telemetry/enabled"]}
        raw = b'{"model": "m", "autoMode": {"environment": ["a private sentence"]}, "telemetry": {"enabled": false, "decidedAt": 5}}'
        out = json.loads(dc.render_for_repo(item, raw))
        self.assertEqual(out, {"model": "m", "telemetry": {"enabled": False}})
        self.assertNotIn(b"private", dc.render_for_repo(item, raw))

    def test_secret_round_trip_binary_and_integrity(self):
        text = dc.pack_secret(SECRET_BYTES, "0600")
        raw, mode, sha = dc.unpack_secret(text)
        self.assertEqual(raw, SECRET_BYTES)
        self.assertEqual(mode, "0600")
        self.assertEqual(dc.note_sha(text), dc.sha256_bytes(SECRET_BYTES))
        tampered = text.replace("sha256=", "sha256=00", 1)
        with self.assertRaises(dc.DotfilesError):
            dc.unpack_secret(tampered)
        with self.assertRaises(dc.DotfilesError):
            dc.pack_secret(os.urandom(20000))  # incompressible: over the note cap

    def test_drift_matrix(self):
        self.assertFalse(dc.drift(None, "r", "rec"))      # live absent
        self.assertFalse(dc.drift("l", "r", None))        # no record: never drift
        self.assertFalse(dc.drift("r", "r", "rec"))       # in sync with the repo
        self.assertFalse(dc.drift("rec", "r", "rec"))     # repo moved, live untouched
        self.assertTrue(dc.drift("edited", "r", "rec"))   # edited after the last write

    def test_tree_helpers_exclude_anchored_and_skip_symlinks(self):
        root = os.path.join(self.home, "tree")
        for rel, data in {"init.lua": "a", "lazy-lock.json": "x", "lua/p/x.lua": "b", "lua/lazy-lock.json": "y"}.items():
            self.live("tree/" + rel, data)
        os.symlink("/etc/passwd", os.path.join(root, "link"))
        files, links = dc.collect_tree(root, ["/lazy-lock.json"])
        self.assertEqual(sorted(files), ["init.lua", "lua/lazy-lock.json", "lua/p/x.lua"])  # nested one kept
        self.assertEqual(links, ["link"])

    def test_denylist(self):
        h = self.home
        for bad in ("~/.ssh/id_ed25519", "~/.aws/credentials", "~/.config/Bitwarden CLI/data.json",
                    "~/.local/share/keyrings/login.keyring", "~/.claude/.credentials.json", "~/.claude.json",
                    "~/.config/BraveSoftware/Brave-Browser/Default/Cookies", "~/.zsh_history",
                    "~/.config/REAPER/reaper.ini", "~/.config/monitors.xml", "~/.config/Code/User/globalStorage/x"):
            self.assertIsNotNone(dc.deny_reason(dc.expand(bad)), bad)
        for ok in ("~/.claude/settings.json", "~/.config/Code/User/settings.json", "~/.zshrc",
                   "~/.config/REAPER/reaper-license.rk", "~/.config/herdr/config.toml"):
            self.assertIsNone(dc.deny_reason(dc.expand(ok)), ok)
        # a symlink cannot smuggle a denied file into an allowed name
        os.makedirs(os.path.join(h, ".ssh"))
        key = self.live(".ssh/id_ed25519", "PRIVATE")
        os.makedirs(os.path.join(h, ".config/herdr"))
        link = os.path.join(h, ".config/herdr/config.toml")
        os.symlink(key, link)
        self.assertIsNotNone(dc.deny_reason(link))

    def test_real_manifest_is_valid(self):
        manifest = dc.load_manifest(os.path.join(REPO, "dotfiles", "manifest.yml"))
        self.assertEqual(dc.validate_manifest(manifest, REPO), [])

    def test_manifest_validation_catches_mistakes(self):
        def errs(*items):
            return dc.validate_manifest({"version": 1, "items": list(items)}, REPO)
        base = {"id": "x", "role": "herdr", "kind": "file", "apply": "seed",
                "live": "~/.config/herdr/config.toml", "repo": "roles/ai/herdr/files/saved/c.toml"}
        self.assertEqual(errs(base), [])
        self.assertTrue(errs(dict(base, live="~/.ssh/id_ed25519")))                        # denylist
        self.assertTrue(errs(dict(base, repo="roles/ai/claude_code/files/saved/c.toml")))  # wrong role dir
        self.assertTrue(errs(dict(base, repo="../etc/passwd")))                            # escape
        self.assertTrue(errs(dict(base, kind="json-keys")))                                # apply not allowed for kind
        self.assertTrue(errs(base, base))                                                  # duplicate id
        self.assertTrue(errs(dict(base, apply="role", drop_keys=["a"])))                   # drop_keys needs seed
        self.assertTrue(errs({"id": "d", "role": "gnome_prefs", "kind": "dconf-keys", "apply": "role",
                              "repo": "roles/desktop/gnome_prefs/files/saved/d.yml",
                              "keys": ["/org/gnome/desktop/interface/color-scheme"]}))     # a role declares it
        self.assertTrue(errs({"id": "d", "role": "gnome_prefs", "kind": "dconf-keys", "apply": "role",
                              "repo": "roles/desktop/gnome_prefs/files/saved/d.yml",
                              "keys": ["/org/gnome/desktop/"]}))                           # a path, not a key
        self.assertTrue(errs({"id": "s", "role": "reaper", "kind": "secret-file", "apply": "vault-seed",
                              "live": "~/.config/REAPER/reaper-license.rk", "vault": "no-prefix"}))


# ======================================================================
class ApplyOps(Sandbox):
    def ctx(self, **kw):
        return dc.Ctx(**kw)

    def repo_file(self, name, data):
        path = os.path.join(self.tmp, "repo-src", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode())
        return path

    def test_file_seed_writes_once_and_never_overwrites(self):
        src = self.repo_file("c.toml", "a = 1\n")
        item = {"id": "h", "kind": "file", "apply": "seed", "live": "~/.config/herdr/config.toml", "src": src}
        r = dc.apply_item(item, self.ctx(check_mode=True))
        self.assertTrue(r["changed"])
        self.assertFalse(os.path.exists(os.path.join(self.home, ".config/herdr/config.toml")))  # check mode writes nothing
        self.assertTrue(dc.apply_item(item, self.ctx())["changed"])
        live = os.path.join(self.home, ".config/herdr/config.toml")
        self.assertEqual(rd(live), "a = 1\n")
        with open(live, "w") as f:
            f.write("user edit\n")
        self.assertFalse(dc.apply_item(item, self.ctx())["changed"])
        self.assertEqual(rd(live), "user edit\n")

    def test_missing_repo_copy_is_not_a_failure(self):
        item = {"id": "h", "kind": "file", "apply": "seed", "live": "~/x", "src": os.path.join(self.tmp, "nope")}
        r = dc.apply_item(item, self.ctx())
        self.assertFalse(r["changed"])
        self.assertIn("just save", r["msg"])

    def test_seed_keys_adds_only_missing(self):
        src = self.repo_file("k.json", json.dumps({"model": "opusplan", "theme": "dark", "telemetry": {"enabled": False}}))
        item = {"id": "c", "kind": "json-keys", "apply": "seed-keys", "live": "~/.claude/settings.json",
                "src": src, "keys": ["model", "theme", "telemetry/enabled"]}
        live = self.live(".claude/settings.json", json.dumps({"model": "sonnet", "ultracode": True, "autoMode": {"x": 1}}))
        r = dc.apply_item(item, self.ctx(check_mode=True))
        self.assertTrue(r["changed"])
        self.assertEqual(json.loads(rd(live))["model"], "sonnet")
        r = dc.apply_item(item, self.ctx())
        got = json.loads(rd(live))
        self.assertEqual(got["model"], "sonnet")              # existing value wins
        self.assertEqual(got["theme"], "dark")                # missing key added
        self.assertEqual(got["telemetry"], {"enabled": False})
        self.assertTrue(got["ultracode"] and got["autoMode"] == {"x": 1})  # untouched
        self.assertFalse(dc.apply_item(item, self.ctx())["changed"])       # converged
        self.assertNotIn("sonnet", json.dumps(r.get("diff", {})))          # diff shows only what was added

    def test_seed_keys_creates_file_0600_and_rejects_non_object(self):
        src = self.repo_file("k.json", '{"model": "m"}')
        item = {"id": "c", "kind": "json-keys", "apply": "seed-keys", "live": "~/.claude/settings.json", "src": src, "keys": ["model"]}
        dc.apply_item(item, self.ctx())
        live = os.path.join(self.home, ".claude/settings.json")
        self.assertEqual(stat.S_IMODE(os.stat(live).st_mode), 0o600)
        with open(live, "w") as f:
            f.write("[1, 2]")
        with self.assertRaises(dc.DotfilesError):
            dc.apply_item(dict(item, keys=["model"]), self.ctx())

    def test_add_lines_appends_missing_and_keeps_existing(self):
        src = self.repo_file("ignore", "**/.claude/settings.local.json\n*.swp\n")
        item = {"id": "g", "kind": "lines", "apply": "add-lines", "live": "~/.config/git/ignore", "src": src}
        live = self.live(".config/git/ignore", "*.swp")  # no trailing newline, one line present
        self.assertTrue(dc.apply_item(item, self.ctx())["changed"])
        self.assertEqual(rd(live), "*.swp\n**/.claude/settings.local.json\n")
        self.assertFalse(dc.apply_item(item, self.ctx())["changed"])

    def test_secret_restore_bytes_mode_and_no_leak(self):
        payload = dc.pack_secret(SECRET_BYTES, "0600")
        item = {"id": "lic", "kind": "secret-file", "apply": "vault-seed", "live": "~/.config/REAPER/reaper-license.rk",
                "vault": "fedora-init/x", "mode": "0600", "src": ""}
        r = dc.apply_item(item, self.ctx(check_mode=True, secret_payload=payload))
        self.assertTrue(r["changed"])
        self.assertFalse(os.path.exists(os.path.join(self.home, ".config/REAPER/reaper-license.rk")))
        r = dc.apply_item(item, self.ctx(secret_payload=payload))
        live = os.path.join(self.home, ".config/REAPER/reaper-license.rk")
        self.assertEqual(rd(live, "rb"), SECRET_BYTES)
        self.assertEqual(stat.S_IMODE(os.stat(live).st_mode), 0o600)
        blob = json.dumps(r)
        self.assertNotIn("LICENCE-KEY", blob)
        self.assertNotIn("diff", r)  # a secret never gets a diff
        # existing target is never touched; no payload is a skip, not a failure
        self.assertFalse(dc.apply_item(item, self.ctx(secret_payload=dc.pack_secret(b"other")))["changed"])
        self.assertEqual(rd(live, "rb"), SECRET_BYTES)
        os.unlink(live)
        r = dc.apply_item(item, self.ctx(secret_payload=""))
        self.assertFalse(r["changed"])
        self.assertTrue(r.get("skipped"))

    def test_dir_seed_adds_only_absent_files(self):
        srcdir = os.path.join(self.tmp, "repo-src", "snips")
        os.makedirs(srcdir)
        for n, d in {"a.json": "A", "b.json": "B"}.items():
            wr(os.path.join(srcdir, n), d)
        item = {"id": "s", "kind": "dir", "apply": "seed", "live": "~/.config/Code/User/snippets", "src": srcdir}
        self.live(".config/Code/User/snippets/a.json", "mine")
        self.assertTrue(dc.apply_item(item, self.ctx())["changed"])
        base = os.path.join(self.home, ".config/Code/User/snippets")
        self.assertEqual(rd(os.path.join(base, "a.json")), "mine")
        self.assertEqual(rd(os.path.join(base, "b.json")), "B")

    def test_vscode_extensions_seed_only_a_machine_with_none(self):
        self.shim("code", """
            if [ "$1" = "--list-extensions" ]; then cat "$FAKE/exts" 2>/dev/null; exit 0; fi
            if [ "$1" = "--install-extension" ]; then echo "$2" >> "$FAKE/exts"; exit 0; fi
            exit 2
        """)
        wr(os.path.join(self.fake, "exts"), "ms-python.python\n")
        src = self.repo_file("ext.txt", "ms-python.python\nCharlieMarsh.Ruff\n")
        item = {"id": "v", "kind": "vscode-extensions", "apply": "seed", "src": src}
        # a machine that already has extensions owns its set: nothing is reinstalled
        r = dc.apply_item(item, self.ctx())
        self.assertFalse(r["changed"])
        self.assertIn("seed-once", r["msg"])
        self.assertEqual(rd(os.path.join(self.fake, "exts")), "ms-python.python\n")
        # a fresh VS Code (none installed) is seeded with the whole list
        wr(os.path.join(self.fake, "exts"), "")
        self.assertTrue(dc.apply_item(item, self.ctx(check_mode=True))["changed"])
        self.assertEqual(rd(os.path.join(self.fake, "exts")), "")
        self.assertTrue(dc.apply_item(item, self.ctx())["changed"])
        self.assertEqual(rd(os.path.join(self.fake, "exts")).split(), ["ms-python.python", "CharlieMarsh.Ruff"])
        self.assertFalse(dc.apply_item(item, self.ctx())["changed"])
        wr(os.path.join(self.fake, "exts"), "ms-python.python\n")  # uninstalled one on purpose: stays gone
        self.assertFalse(dc.apply_item(item, self.ctx())["changed"])
        os.environ["PATH"] = os.path.join(self.tmp, "empty")  # no `code`, even if the host has VS Code
        self.assertIn("not installed", dc.apply_item(item, self.ctx())["msg"])

    def test_guard_and_record_flow_for_a_role_applied_file(self):
        src = self.repo_file("zshrc", "repo v1\n")
        item = {"id": "zsh-zshrc", "kind": "file", "apply": "role", "live": "~/.zshrc", "src": src}
        ctx = self.ctx()
        live = self.live(".zshrc", "old pre-existing\n")
        self.assertFalse(dc.guard_item(item, ctx)["drift"], "no record yet: never drift")
        wr(live, "repo v1\n")                       # the role wrote it
        self.assertTrue(dc.record_item(item, ctx)["changed"])
        self.assertFalse(dc.guard_item(item, ctx)["drift"])
        wr(live, "hand edit\n")                     # the person edited it
        r = dc.guard_item(item, ctx)
        self.assertTrue(r["drift"])
        self.assertTrue(any("just save" in w for w in ctx.warnings))
        wr(src, "repo v2\n")                        # repo moves too -> still drift
        self.assertTrue(dc.guard_item(item, self.ctx())["drift"])
        wr(live, "repo v1\n")                       # edit reverted: live == record
        self.assertFalse(dc.guard_item(item, self.ctx())["drift"])
        # check mode records nothing
        wr(live, "repo v2\n")
        dc.record_item(item, self.ctx(check_mode=True))
        self.assertEqual(dc.load_state()["applied"]["zsh-zshrc"], dc.sha256_bytes(b"repo v1\n"))

    def test_dir_guard_uses_tree_hash_with_excludes(self):
        srcdir = os.path.join(self.tmp, "repo-src", "nvim")
        os.makedirs(srcdir)
        wr(os.path.join(srcdir, "init.lua"), "a")
        item = {"id": "nv", "kind": "dir", "apply": "role", "live": "~/.config/nvim", "src": srcdir,
                "excludes": ["/lazy-lock.json"]}
        self.live(".config/nvim/init.lua", "a")
        self.live(".config/nvim/lazy-lock.json", "runtime")
        dc.record_item(item, self.ctx())
        self.live(".config/nvim/lazy-lock.json", "changed runtime state")  # excluded: not drift
        self.assertFalse(dc.guard_item(item, self.ctx())["drift"])
        self.live(".config/nvim/lua/plugins/new.lua", "return {}")         # new unsaved spec
        self.assertTrue(dc.guard_item(item, self.ctx())["drift"])

    def test_dconf_guard_record(self):
        self.shim("dconf", '''
            [ "$1" = read ] || exit 1
            f="$FAKE/dconf/$(echo "$2" | tr / _)"; [ -f "$f" ] && cat "$f"; exit 0
        ''')
        os.makedirs(os.path.join(self.fake, "dconf"))
        def setv(key, val):
            wr(os.path.join(self.fake, "dconf", key.replace("/", "_")), val + "\n")
        k = "/org/gnome/system/location/enabled"
        entries = [{"key": k, "value": "true"}]
        ctx = self.ctx()
        self.assertEqual(dc.guard_dconf(entries, ctx)["safe"], [k])       # absent: writable
        setv(k, "true")
        dc.record_dconf([k], ctx)
        setv(k, "false")                                                   # changed in Settings
        r = dc.guard_dconf(entries, self.ctx())
        self.assertEqual((r["safe"], r["drifted"]), ([], [k]))


# ======================================================================
class CliBase(Sandbox):
    """Scratch git repo + shim bw for driving `dotfiles.py` (real gitleaks when present)."""

    MANIFEST = textwrap.dedent("""\
        version: 1
        items:
          - id: herdr-config
            role: herdr
            kind: file
            apply: seed
            live: ~/.config/herdr/config.toml
            repo: roles/ai/herdr/files/saved/config.toml
          - id: claude-settings
            role: claude_code
            kind: json-keys
            apply: seed-keys
            live: ~/.claude/settings.json
            repo: roles/ai/claude_code/files/saved/settings-keys.json
            keys: [model, theme]
          - id: zsh-zshrc
            role: zsh
            kind: file
            apply: role
            live: ~/.zshrc
            repo: roles/dev/zsh/files/zshrc
          - id: reaper-license
            role: reaper
            kind: secret-file
            apply: vault-seed
            live: ~/.config/REAPER/reaper-license.rk
            vault: fedora-init/reaper-license.rk
            mode: "0600"
    """)

    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.tmp, "repo")
        for d in ("roles/ai/herdr", "roles/ai/claude_code", "roles/dev/zsh", "roles/audio/reaper", "dotfiles", ".githooks"):
            os.makedirs(os.path.join(self.root, d))
        wr(os.path.join(self.root, "dotfiles/manifest.yml"), self.MANIFEST)
        for f in (".gitleaks.toml", ".gitleaks-pii.toml"):
            shutil.copy(os.path.join(REPO, f), os.path.join(self.root, f))
        shutil.copy(os.path.join(REPO, ".githooks/pre-commit"), os.path.join(self.root, ".githooks/pre-commit"))
        os.environ["FEDORA_INIT_ROOT"] = self.root
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.git("config", "commit.gpgsign", "false")
        wr(os.path.join(self.root, "README"), "x\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "init")
        # shim vault: a JSON file of items
        self.vault_db = os.path.join(self.fake, "vault.json")
        wr(self.vault_db, "[]")
        os.environ["FAKE_BW_DB"] = self.vault_db
        self.shim("bw", '''
            import json, os, sys
            db = os.environ["FAKE_BW_DB"]; items = json.load(open(db)); a = sys.argv[1:]
            def save(): json.dump(items, open(db, "w"))
            if a[:2] == ["list", "items"]:
                print(json.dumps([i for i in items if a[3].lower() in i["name"].lower()]))
            elif a[:3] == ["get", "template", "item"]:
                print(json.dumps({"type": 1, "name": "", "notes": None, "login": {}, "fields": [], "secureNote": None}))
            elif a[0] == "encode":
                print(sys.stdin.read().strip())
            elif a[:2] == ["create", "item"]:
                it = json.loads(sys.stdin.read()); it["id"] = "id%d" % (len(items) + 1); items.append(it); save()
            elif a[:2] == ["edit", "item"]:
                it = json.loads(sys.stdin.read())
                items[:] = [it if i["id"] == a[2] else i for i in items]; save()
            else:
                sys.exit(1)
        ''', python=True)

    def git(self, *a):
        return subprocess.run(["git", "-C", self.root, *a], capture_output=True, text=True, check=True).stdout

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                cli.main(list(argv))
            except SystemExit as e:
                code = e.code or 0
        return code, out.getvalue(), err.getvalue()

    def head_files(self):
        return set(self.git("ls-tree", "-r", "--name-only", "HEAD").split())


class CliSave(CliBase):
    # ---- behaviour that needs no scanner
    def test_lint_and_pending_secrets_and_vault_needed(self):
        self.assertEqual(self.run_cli("lint")[0], 0)
        code, out, _ = self.run_cli("pending-secrets")
        self.assertEqual(out.split(), ["reaper"])           # licence absent -> reaper needs a vault session
        self.live(".config/REAPER/reaper-license.rk", SECRET_BYTES)
        self.assertEqual(self.run_cli("pending-secrets")[1].split(), [])
        with self.assertRaises(SystemExit) as cm:
            cli.main(["vault-needed"])
        self.assertEqual(cm.exception.code, 0)               # a file never vaulted -> needed

    def test_dry_run_writes_nothing_anywhere(self):
        self.live(".config/herdr/config.toml", "onboarding = false\n")
        before = self.git("status", "--porcelain")
        code, out, err = self.run_cli("save", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("config.toml (new file)", out)
        self.assertIn("Dry run", out)
        self.assertEqual(self.git("status", "--porcelain"), before)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".config/fedora-init")))  # not even the PII file
        self.assertFalse(os.path.exists(dc.state_path()))

    def test_unknown_only_id_and_not_on_main_are_refused(self):
        self.assertEqual(self.run_cli("save", "--dry-run", "--only", "nope")[0], 1)
        self.git("checkout", "-qb", "feature")
        self.live(".config/herdr/config.toml", "a = 1\n")
        code, _out, err = self.run_cli("save", "--yes", "--no-vault", "--no-push")
        self.assertEqual(code, 1)
        self.assertIn("not on main", err)

    # ---- behaviour that needs gitleaks
    @unittest.skipUnless(GITLEAKS, "gitleaks not installed (set GITLEAKS_BIN) — scan tests skipped")
    def test_save_commits_filtered_files_and_vaults_secrets(self):
        self.live(".config/herdr/config.toml", "onboarding = false\n")
        self.live(".claude/settings.json", json.dumps(
            {"model": "opusplan", "theme": "dark",
             "autoMode": {"environment": ["a sentence naming a private repo and a person"]}}))
        self.live(".zshrc", "export EDITOR=nvim\n")
        self.live(".config/REAPER/reaper-license.rk", SECRET_BYTES)
        code, out, err = self.run_cli("save", "--yes", "--no-push")
        self.assertEqual(code, 0, err + out)
        files = self.head_files()
        self.assertEqual(files, {"README", ".githooks/pre-commit", ".gitleaks.toml", ".gitleaks-pii.toml",
                                 "dotfiles/manifest.yml", "roles/ai/herdr/files/saved/config.toml",
                                 "roles/ai/claude_code/files/saved/settings-keys.json", "roles/dev/zsh/files/zshrc"})
        saved = json.loads(self.git("show", "HEAD:roles/ai/claude_code/files/saved/settings-keys.json"))
        self.assertEqual(saved, {"model": "opusplan", "theme": "dark"})
        everything = self.git("log", "-p", "--all")
        self.assertNotIn("private repo", everything)
        self.assertNotIn("LICENCE-KEY", everything)
        # the secret went to the vault, as a note, and only there
        items = json.loads(rd(self.vault_db))
        self.assertEqual([i["name"] for i in items], ["fedora-init/reaper-license.rk"])
        self.assertEqual(items[0]["type"], 2)
        self.assertEqual(dc.unpack_secret(items[0]["notes"])[0], SECRET_BYTES)
        self.assertNotIn("LICENCE-KEY", out + err)
        # state: role item recorded, secret vaulted
        st = dc.load_state()
        self.assertIn("zsh-zshrc", st["applied"])
        self.assertEqual(st["vaulted"]["reaper-license"], dc.sha256_bytes(SECRET_BYTES))
        self.assertEqual(self.git("config", "--local", "core.hooksPath").strip(), ".githooks")
        # converged: nothing to do, no new commit, no vault session needed
        n = len(self.git("rev-list", "HEAD").split())
        code, out, _ = self.run_cli("save", "--yes", "--no-push")
        self.assertEqual(code, 0)
        self.assertIn("Nothing to save", out)
        self.assertEqual(len(self.git("rev-list", "HEAD").split()), n)
        with self.assertRaises(SystemExit) as cm:
            cli.main(["vault-needed"])
        self.assertEqual(cm.exception.code, 1)

    @unittest.skipUnless(GITLEAKS, "gitleaks not installed (set GITLEAKS_BIN) — scan tests skipped")
    def test_scan_blocks_a_planted_secret_before_anything_is_written(self):
        self.live(".config/herdr/config.toml", 'token = "%s"\n' % FAKE_TOKEN)
        head = self.git("rev-parse", "HEAD")
        code, out, err = self.run_cli("save", "--yes", "--no-vault", "--no-push")
        self.assertEqual(code, 1)
        self.assertIn("github-pat", err)
        self.assertNotIn(FAKE_TOKEN, out + err)               # findings name the rule, never the value
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertFalse(os.path.exists(os.path.join(self.root, "roles/ai/herdr/files/saved")))

    @unittest.skipUnless(GITLEAKS, "gitleaks not installed (set GITLEAKS_BIN) — scan tests skipped")
    def test_pii_rules_block_home_paths_emails_and_private_literals(self):
        for content, rule in (('p = "/home/someoneelse/x"\n', "home-directory"),
                              ('mail = "a.person@private-domain.org"\n', "email-address"),
                              ('h = "box.tail9f3e.ts.net"\n', "tailnet-hostname")):
            self.live(".config/herdr/config.toml", content)
            code, _o, err = self.run_cli("save", "--yes", "--no-vault", "--no-push")
            self.assertEqual(code, 1, content)
            self.assertIn(rule, err)
        # a private literal from the untracked per-user file
        os.makedirs(os.path.join(self.home, ".config/fedora-init"), exist_ok=True)
        wr(os.path.join(self.home, ".config/fedora-init/pii-patterns.txt"), "# mine\nmy-private-git\\.example\n")
        self.live(".config/herdr/config.toml", 'remote = "git@my-private-git.example:x.git"\n')
        code, _o, err = self.run_cli("save", "--yes", "--no-vault", "--no-push")
        self.assertEqual(code, 1)
        self.assertIn("private-literal-1", err)
        # GNOME extension ids look like emails and must pass
        self.live(".config/herdr/config.toml", 'ext = ["appindicatorsupport@rgcjonas.gmail.com", "rectangle@amamparo"]\n')
        self.assertEqual(self.run_cli("save", "--yes", "--no-vault", "--no-push")[0], 0)

    @unittest.skipUnless(GITLEAKS, "gitleaks not installed (set GITLEAKS_BIN) — scan tests skipped")
    def test_dirty_target_aborts_and_unrelated_dirty_files_are_not_committed(self):
        self.live(".config/herdr/config.toml", "a = 1\n")
        self.assertEqual(self.run_cli("save", "--yes", "--no-vault", "--no-push")[0], 0)
        target = os.path.join(self.root, "roles/ai/herdr/files/saved/config.toml")
        self.live(".config/herdr/config.toml", "a = 2\n")
        wr(target, "# hand edit, uncommitted\n", "a")
        code, _o, err = self.run_cli("save", "--yes", "--no-vault", "--no-push")
        self.assertEqual(code, 1)
        self.assertIn("uncommitted changes", err)
        self.git("checkout", "--", "roles")
        wr(os.path.join(self.root, "README"), "unrelated work in progress\n", "a")
        self.assertEqual(self.run_cli("save", "--yes", "--no-vault", "--no-push")[0], 0)
        self.assertEqual(self.git("status", "--porcelain").strip(), "M README")   # left alone, uncommitted
        self.assertNotIn("README", self.git("show", "--name-only", "--format=", "HEAD"))

    @unittest.skipUnless(GITLEAKS, "gitleaks not installed (set GITLEAKS_BIN) — scan tests skipped")
    def test_save_fails_closed_without_gitleaks(self):
        self.live(".config/herdr/config.toml", "a = 1\n")
        only_git = os.path.join(self.tmp, "only-git")
        os.makedirs(only_git)
        os.symlink(shutil.which("git"), os.path.join(only_git, "git"))
        os.environ["PATH"] = only_git  # git yes, gitleaks no
        code, _o, err = self.run_cli("save", "--yes", "--no-vault", "--no-push")
        self.assertEqual(code, 1)
        self.assertIn("gitleaks is not installed", err)
        self.assertFalse(os.path.exists(os.path.join(self.root, "roles/ai/herdr/files/saved")))

    @unittest.skipUnless(GITLEAKS, "gitleaks not installed (set GITLEAKS_BIN) — scan tests skipped")
    def test_deleted_live_dir_file_is_mirrored_out_of_the_repo(self):
        manifest = self.MANIFEST + textwrap.indent(textwrap.dedent("""\
            - id: neovim-config
              role: neovim
              kind: dir
              apply: role
              live: ~/.config/nvim
              repo: roles/dev/neovim/files/nvim
              excludes: [/lazy-lock.json]
        """), "  ")
        os.makedirs(os.path.join(self.root, "roles/dev/neovim"))
        wr(os.path.join(self.root, "dotfiles/manifest.yml"), manifest)
        self.git("add", "-A")
        self.git("commit", "-qm", "manifest")
        self.live(".config/nvim/init.lua", "a\n")
        self.live(".config/nvim/lua/plugins/x.lua", "return {}\n")
        self.live(".config/nvim/lazy-lock.json", "{}")
        self.assertEqual(self.run_cli("save", "--yes", "--no-vault", "--no-push", "--only", "neovim-config")[0], 0)
        files = self.head_files()
        self.assertIn("roles/dev/neovim/files/nvim/lua/plugins/x.lua", files)
        self.assertNotIn("roles/dev/neovim/files/nvim/lazy-lock.json", files)
        os.unlink(os.path.join(self.home, ".config/nvim/lua/plugins/x.lua"))
        self.assertEqual(self.run_cli("save", "--yes", "--no-vault", "--no-push", "--only", "neovim-config")[0], 0)
        self.assertNotIn("roles/dev/neovim/files/nvim/lua/plugins/x.lua", self.head_files())


class SaveThreeWay(CliBase):
    """`save` must not mistake 'the repo moved' for 'live changed': a pull, or an
    agent editing roles/…/files/ as AGENTS.md says to, followed by `just save`
    before `./install.sh`, would otherwise silently revert that work."""

    ZSHRC = "roles/dev/zsh/files/zshrc"

    def save(self, *extra):
        return self.run_cli("save", "--yes", "--no-vault", "--no-push", "--only", "zsh-zshrc", *extra)

    def commit_repo_file(self, rel, data):
        os.makedirs(os.path.dirname(os.path.join(self.root, rel)), exist_ok=True)
        wr(os.path.join(self.root, rel), data)
        self.git("add", "-A")
        self.git("commit", "-qm", "repo edit " + data.strip())

    def repo_head(self):
        return self.git("show", "HEAD:" + self.ZSHRC)

    @unittest.skipUnless(GITLEAKS, "gitleaks not installed (set GITLEAKS_BIN) — scan tests skipped")
    def test_repo_ahead_is_kept_and_a_conflict_is_refused(self):
        self.live(".zshrc", "A\n")
        self.assertEqual(self.save()[0], 0)
        self.assertEqual(self.repo_head(), "A\n")
        self.commit_repo_file(self.ZSHRC, "B\n")             # the repo moved; the machine did not
        code, out, err = self.save()
        self.assertEqual(code, 0, err)
        self.assertIn("AHEAD", out)
        self.assertEqual(self.repo_head(), "B\n")              # NOT reverted to A
        self.assertEqual(rd(os.path.join(self.home, ".zshrc")), "A\n")
        self.live(".zshrc", "C\n")                             # now live moved too: neither side can win
        head = self.git("rev-parse", "HEAD")
        code, _o, err = self.save()
        self.assertEqual(code, 1)
        self.assertIn("CONFLICT", err)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.repo_head(), "B\n")
        self.assertEqual(self.save("--force-live", "zsh-zshrc")[0], 0)   # the user settles it
        self.assertEqual(self.repo_head(), "C\n")

    @unittest.skipUnless(GITLEAKS, "gitleaks not installed (set GITLEAKS_BIN) — scan tests skipped")
    def test_an_ordinary_live_edit_after_a_save_is_captured(self):
        self.live(".zshrc", "A\n")
        self.assertEqual(self.save()[0], 0)
        self.live(".zshrc", "D\n")                             # repo == record, only live moved
        code, out, err = self.save()
        self.assertEqual(code, 0, err)
        self.assertNotIn("AHEAD", out)
        self.assertEqual(self.repo_head(), "D\n")

    @unittest.skipUnless(GITLEAKS, "gitleaks not installed (set GITLEAKS_BIN) — scan tests skipped")
    def test_no_record_and_a_difference_lets_live_win_but_says_so(self):
        self.commit_repo_file(self.ZSHRC, "R\n")
        self.live(".zshrc", "L\n")
        code, out, err = self.save()
        self.assertEqual(code, 0, err)
        self.assertIn("LOOK AT THIS DIFF", out)
        self.assertEqual(self.repo_head(), "L\n")

    def test_a_converged_dry_run_writes_no_state_file(self):
        self.live(".config/herdr/config.toml", "a = 1\n")
        self.commit_repo_file("roles/ai/herdr/files/saved/config.toml", "a = 1\n")
        self.assertFalse(os.path.exists(dc.state_path()))
        code, out, err = self.run_cli("save", "--dry-run", "--only", "herdr-config")
        self.assertEqual(code, 0, err)
        self.assertIn("Nothing to save", out)
        self.assertFalse(os.path.exists(dc.state_path()))        # a dry run writes NOTHING

    def test_dconf_keys_follow_the_same_rule(self):
        self.shim("dconf", '''
            [ "$1" = read ] || exit 1
            f="$FAKE/dconf/$(echo "$2" | tr / _)"; [ -f "$f" ] && cat "$f"; exit 0
        ''')
        self.shim("gsettings", '''
            [ "$1" = get ] || exit 1
            echo "false"
        ''')
        os.makedirs(os.path.join(self.fake, "dconf"))
        key = "/org/gnome/system/location/enabled"
        item = {"id": "gnome-dconf", "role": "gnome_prefs", "kind": "dconf-keys", "apply": "role",
                "repo": "roles/desktop/gnome_prefs/files/saved/dconf.yml", "keys": [key]}
        item["src"] = os.path.join(self.root, item["repo"])
        os.makedirs(os.path.dirname(item["src"]))
        wr(item["src"], cli.render_dconf({key: "true"}).decode())

        def run(live, rec):
            wr(os.path.join(self.fake, "dconf", key.replace("/", "_")), live + "\n")
            st = {"applied": {} if rec is None else {"dconf:" + key: dc.sha256_bytes(rec.encode())}, "vaulted": {}}
            got = cli.Collected()
            cli._collect_dconf(item, {}, got)
            cli._three_way_dconf(item, st, got)
            return got

        # saved value "true"; the repo is ahead when live still equals the record and differs from it
        wr(item["src"], cli.render_dconf({key: "'repo-says-this'"}).decode())
        got = run("true", "true")
        self.assertIn("repo-says-this", got.files[item["repo"]].decode())      # kept, not reverted
        self.assertTrue(any("AHEAD" in n for n in got.notes))
        with self.assertRaises(dc.DotfilesError):
            run("true", "something-else")                                     # all three differ: conflict
        got = run("true", "'repo-says-this'")                                 # only live moved: ordinary save
        self.assertIn("true", got.files[item["repo"]].decode())
        self.assertNotIn("repo-says-this", got.files[item["repo"]].decode())
        got = run("true", None)                                               # no record: live wins, loudly
        self.assertTrue(any("LOOK AT THIS" in n for n in got.notes))
        # a key the repo has but this machine never applied is not dropped just because live shows the default
        wr(os.path.join(self.fake, "dconf", key.replace("/", "_")), "false\n")
        got = cli.Collected()
        cli._collect_dconf(item, {}, got)
        cli._three_way_dconf(item, {"applied": {}, "vaulted": {}}, got)
        self.assertIn("repo-says-this", got.files[item["repo"]].decode())


class PackageDiscovery(Sandbox):
    def test_hand_installed_rpms_and_flathub_flatpaks_minus_what_roles_name(self):
        self.shim("dnf5", '''
            if [ "$1 $2" = "history list" ]; then
              echo "ID Command line Date and time Action(s) Altered"
              echo "64 ansible dnf5 module                   2026-09-27 06:00:26                12"
              echo "58 dnf install easytag                   2026-09-23 03:48:44                 4"
              echo "57 dnf install gcc                       2026-09-22 03:48:44                 4"
              echo " 2 dnf5 --config /kiwi_dnf5.config -y   2026-04-22 14:00:43              1721"
              exit 0; fi
            if [ "$1 $2" = "history info" ]; then
              case "$3" in
                58) echo "  Install easytag-0:2.5-1.fc44.x86_64   User   fedora"; echo "  Install libid3tag-0:0.15-1.fc44.x86_64 Dependency fedora";;
                57) echo "  Install gcc-0:15.1-1.fc44.x86_64      User   updates";;
              esac; exit 0; fi
            exit 1
        ''')
        self.shim("rpm", "exit 0")
        self.shim("flatpak", '''
            printf 'com.bitwig.BitwigStudio\\tflathub\\ncom.bitwig.BitwigStudio\\tbitwigstudio-origin\\nio.podman_desktop.PodmanDesktop\\tflathub\\n'
        ''')
        blob = "- name: gcc\n  something: io.podman_desktop.PodmanDesktop\n"
        self.assertEqual(cli.discover_rpms(blob), {"easytag"})
        self.assertEqual(cli.discover_flatpaks(blob), {"com.bitwig.BitwigStudio"})


if __name__ == "__main__":
    unittest.main()
