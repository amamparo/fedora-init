#!/usr/bin/env python3
# fedora-init — the dotfiles CLI behind `just save`.
#
#   dotfiles.py save [--dry-run] [--yes] [--no-vault] [--no-push] [--no-commit]
#                    [--only ID]... [--force-live ID]... [-m MSG]
#   dotfiles.py lint                  validate dotfiles/manifest.yml
#   dotfiles.py pending-secrets       role tags whose vault-seeded file is absent
#   dotfiles.py vault-needed          exit 0 when `save` must open a vault session
#
# `save` copies the live configs the manifest allowlists into the roles that
# own them, then commits and pushes. The repo is PUBLIC, so nothing reaches
# the working tree until a secret/PII scan of the rendered files has passed,
# and nothing is committed until a person has seen the diff (--yes skips the
# question for automation). Secret FILES (licence keys) never touch git: they
# go to Bitwarden secure notes. Credentials (ssh keys, tokens) are not
# savable at all — dotfiles_common's denylist rejects them at manifest load.
#
# The restore side is library/dotfile_sync.py, driven from the owning roles
# through roles/common/tasks/dotfiles.yml; both import
# module_utils/dotfiles_common.py, so the two directions cannot drift.
#
# Output carries paths, rule ids and counts — never file content of a secret
# item and never a scanner match.

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "module_utils"))
import dotfiles_common as dc  # noqa: E402

PII_FILE_REL = ".config/fedora-init/pii-patterns.txt"
PII_FILE_HEADER = """\
# Private PII literals for `just save`'s staging scan — NEVER in the repo.
# One regular expression per line (Go/RE2 syntax, as gitleaks reads it); blank
# lines and # comments are ignored. A match in a file about to be saved aborts
# the save before anything is written. Put here the values that are private
# but not obviously secret: a self-hosted git domain, a tailnet name, the
# street you live on. Values already public in the repo (your commit email)
# belong nowhere near this file — they would fail every save.
"""

# Leaks exit code for gitleaks: distinct from 1, which it also uses for errors.
GITLEAKS_LEAK_RC = 99


def root():
    return os.environ.get("FEDORA_INIT_ROOT") or ROOT


# ------------------------------------------------------------------ helpers
def say(msg=""):
    print(msg, flush=True)


def load_items():
    path = os.path.join(root(), "dotfiles", "manifest.yml")
    manifest = dc.load_manifest(path)
    errors = dc.validate_manifest(manifest, root())
    if errors:
        raise dc.DotfilesError("dotfiles/manifest.yml is invalid:\n  - " + "\n  - ".join(errors))
    for item in manifest["items"]:
        item["src"] = os.path.join(root(), item["repo"]) if item.get("repo") else ""
    return manifest, manifest["items"]


def git(*args, check=True, input=None):
    p = subprocess.run(["git", "-C", root(), *args], capture_output=True, text=True, input=input)
    if check and p.returncode != 0:
        raise dc.DotfilesError("git %s failed: %s" % (args[0], (p.stderr or p.stdout).strip()[:300]))
    return p


def repo_read(rel):
    path = os.path.join(root(), rel)
    return dc.read_bytes(path) if os.path.isfile(path) else None


# ------------------------------------------------------------- collection
class Collected:
    """What one manifest item contributes to a save."""

    def __init__(self):
        self.files = {}      # repo-relative path -> new bytes
        self.deletes = []    # repo-relative paths to remove
        self.notes = []      # human-readable skips and warnings
        self.record = None   # sha to remember as "last saved" (role/dconf items)
        self.mtimes = {}     # repo-relative path -> live mtime (dir items; see cmd_save)
        self.dconf_live = {}  # dconf-keys items: path -> live non-default value (HOME tokenised)


def collect_item(item, manifest, state, force_live=()):
    kind = item["kind"]
    out = Collected()
    fn = {
        "file": _collect_file, "json-keys": _collect_file, "lines": _collect_file,
        "dir": _collect_dir, "vscode-extensions": _collect_vscode,
        "dconf-keys": _collect_dconf, "packages": _collect_packages,
    }[kind]
    fn(item, manifest, out)
    if item["id"] not in force_live:
        if kind in ("file", "dir") and item["apply"] == "role":
            _three_way(item, state, out)
        elif kind == "dconf-keys":
            _three_way_dconf(item, state, out)
    return out


def _three_way(item, state, out):
    """`save` is a SNAPSHOT of live, which is wrong whenever the repo moved and
    the machine did not (a pull, or an agent editing roles/…/files/ per
    AGENTS.md, then `just save` before `./install.sh`): it would silently
    revert that work. Use the same record the roles' guard keeps.
      live == repo                 nothing to do
      no record                    cannot tell who moved: live wins, loudly
      repo == record (live moved)  the ordinary save: live wins
      live == record (repo moved)  skip the item, the repo is ahead
      all three differ             conflict: refuse, never pick a side"""
    if not out.files and not out.deletes:
        return
    live_sha = out.record
    if item["kind"] == "dir":
        repo_files, _ = dc.collect_tree(item["src"], item.get("excludes", []))
        repo_sha = dc.tree_hash(repo_files) if repo_files else None
    else:
        data = repo_read(item["repo"])
        repo_sha = dc.sha256_bytes(dc.render_for_live(item, data)) if data is not None else None
    rec = state["applied"].get(item["id"])
    if repo_sha is None or live_sha == repo_sha:
        return
    if rec is None:
        out.notes.append(
            "%s: LOOK AT THIS DIFF — live and the repo copy differ and there is no record of "
            "which one moved (never installed or saved on this machine); live wins" % item["id"])
    elif repo_sha == rec:
        return
    elif live_sha == rec:
        out.notes.append(
            "%s: the repo is AHEAD of this machine (live is unchanged since the last install/save) — "
            "kept the repo copy; run ./install.sh %s to take it" % (item["id"], item["role"].replace("_", "-")))
        out.files, out.deletes, out.mtimes, out.record = {}, [], {}, None
    else:
        raise dc.DotfilesError(
            "%s: CONFLICT — the live file was edited AND the repo copy changed since the last "
            "install/save, so neither side can be picked for you. Merge by hand, then either "
            "`just save --force-live %s` (take live) or revert the live file and run "
            "./install.sh %s (take the repo)." % (item["id"], item["id"], item["role"].replace("_", "-")))


def _three_way_dconf(item, state, out):
    """The same rule per saved dconf key; see _three_way. A key saved in the
    repo but never applied on this machine (no record) is never dropped just
    because live shows the default — that would erase a pulled addition."""
    try:
        import yaml
        existing = (yaml.safe_load(dc.read_bytes(item["src"]).decode()) or {}) if os.path.isfile(item["src"]) else {}
    except Exception as e:  # noqa: BLE001
        raise dc.DotfilesError("%s: cannot read the saved dconf values: %s" % (item["id"], e))
    saved = {e["key"]: e["value"] for e in (existing.get("saved_dconf") or [])}
    final, keep_live = {}, []
    for path, _schema, _key in dc.dconf_entries(item):
        rec = state["applied"].get("dconf:" + path)
        raw = dc.dconf_read(path)
        raw_h = dc.sha256_bytes(raw.encode()) if raw else None
        mine, theirs = out.dconf_live.get(path), saved.get(path)
        if mine is not None:
            if theirs is None or theirs == mine:
                final[path] = mine
                keep_live.append(path)
            elif rec is None:
                out.notes.append("%s: LOOK AT THIS — %s differs from the repo copy and there is no record of "
                                 "which moved; live wins" % (item["id"], path))
                final[path] = mine
                keep_live.append(path)
            elif dc.sha256_bytes(theirs.replace(dc.HOME_TOKEN, dc.home()).encode()) == rec:
                final[path] = mine     # only live moved: the ordinary save
                keep_live.append(path)
            elif raw_h == rec:
                out.notes.append("%s: the repo is AHEAD for %s — kept the repo value (run ./install.sh gnome-prefs)"
                                 % (item["id"], path))
                final[path] = theirs
            else:
                raise dc.DotfilesError(
                    "%s: CONFLICT on %s — changed live AND in the repo since the last install/save. "
                    "Settle it by hand, then `just save --force-live %s`." % (item["id"], path, item["id"]))
        elif theirs is not None and rec is None:
            final[path] = theirs
    out.files[item["repo"]] = render_dconf(final)
    out.record = "dconf:" + json.dumps(sorted(keep_live))


def _collect_file(item, _manifest, out):
    live = dc.expand(item["live"])
    reason = dc.deny_reason(live)
    if reason:
        raise dc.DotfilesError("%s: %s" % (item["id"], reason))
    if not os.path.isfile(live):
        out.notes.append("%s: %s is absent — skipped" % (item["id"], item["live"]))
        return
    raw = dc.read_bytes(live)
    out.files[item["repo"]] = dc.render_for_repo(item, raw)
    if item["apply"] == "role":
        out.record = dc.sha256_bytes(raw)


def _collect_dir(item, _manifest, out):
    live = dc.expand(item["live"])
    reason = dc.deny_reason(live)
    if reason:
        raise dc.DotfilesError("%s: %s" % (item["id"], reason))
    ex = item.get("excludes", [])
    if not os.path.isdir(live):
        out.notes.append("%s: %s is absent — skipped" % (item["id"], item["live"]))
        return
    live_files, links = dc.collect_tree(live, ex)
    for rel in links:
        out.notes.append("%s: symlink %s not captured (links are never followed)" % (item["id"], rel))
    for rel, data in live_files.items():
        out.files[item["repo"] + "/" + rel] = dc.render_for_repo(item, data) if item.get("home_template") else data
        out.mtimes[item["repo"] + "/" + rel] = os.stat(os.path.join(live, rel)).st_mtime_ns
    repo_files, _ = dc.collect_tree(os.path.join(root(), item["repo"]), ex)
    out.deletes = [item["repo"] + "/" + rel for rel in repo_files if rel not in live_files]
    if item["apply"] == "role":
        out.record = dc.tree_hash(live_files)


def _collect_vscode(item, _manifest, out):
    rc, text, err = dc.run_cmd(["code", "--list-extensions"], 120)
    if rc == 127:
        out.notes.append("%s: `code` is not installed — extension list skipped" % item["id"])
        return
    if rc != 0:
        raise dc.DotfilesError("%s: `code --list-extensions` failed: %s" % (item["id"], err.strip()[:200]))
    ids = sorted({ln.strip() for ln in text.splitlines() if ln.strip()}, key=str.lower)
    out.files[item["repo"]] = ("\n".join(ids) + "\n").encode() if ids else b""


def schema_default(schema, key):
    env = dict(os.environ, GSETTINGS_BACKEND="memory")
    rc, text, _ = dc.run_cmd(["gsettings", "get", schema, key], 10, env)
    return text.strip() if rc == 0 else None


DCONF_HEADER = [
    "# Written by `just save` (scripts/dotfiles.py) — do not hand-edit: change the",
    "# live setting, then save again. The role applies these through the dconf",
    "# module, behind a guard that keeps a live value changed since the last save.",
    "# Values are GVariant text; the home directory is a placeholder.",
]


def render_dconf(final):
    lines = list(DCONF_HEADER)
    lines.append("saved_dconf:" + ("" if final else " []"))
    for path, value in final.items():
        lines.append("  - key: %s" % path)
        lines.append("    value: %s" % json.dumps(value, ensure_ascii=False))
    return ("\n".join(lines) + "\n").encode()


def _collect_dconf(item, _manifest, out):
    live = {}
    for path, schema, key in dc.dconf_entries(item):
        val = dc.dconf_read(path)
        if not val:
            continue
        default = schema_default(schema, key)
        if default is None:
            out.notes.append("%s: no schema for %s — skipped" % (item["id"], path))
        elif val != default:  # equal to the schema default: nothing to carry to a new machine
            live[path] = val.replace(dc.home(), dc.HOME_TOKEN)
    out.dconf_live = live
    out.files[item["repo"]] = render_dconf(live)
    out.record = "dconf:" + json.dumps(sorted(live))  # sentinel: handled in _record


def _reference_blob():
    """Everything that can install a package, as one string."""
    parts = []
    for base in ("install.sh", "site.yml"):
        p = os.path.join(root(), base)
        if os.path.isfile(p):
            parts.append(open(p, encoding="utf-8", errors="replace").read())
    for dirpath, _d, files in os.walk(os.path.join(root(), "roles")):
        if "saved" in dirpath.split(os.sep):
            continue
        for name in files:
            if name.endswith((".yml", ".yaml", ".j2", ".sh", ".repo")):
                parts.append(open(os.path.join(dirpath, name), encoding="utf-8", errors="replace").read())
    return "\n".join(parts)


def _referenced(name, blob):
    return re.search(r"(?<![A-Za-z0-9._+-])" + re.escape(name) + r"(?![A-Za-z0-9._+-])", blob) is not None


def discover_rpms(blob):
    """Packages a person installed by hand: Install/User actions from dnf
    transactions that are neither the image build (ids 1-2) nor ansible's."""
    rc, text, _ = dc.run_cmd(["dnf5", "history", "list"], 60)
    if rc != 0:
        return None
    found = set()
    for ln in text.splitlines():
        m = re.match(r"^\s*(\d+)\s+(.*?)\s+\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\s+\d+\s*$", ln)
        if not m or int(m.group(1)) <= 2 or m.group(2).startswith("ansible dnf5"):
            continue
        rc, info, _ = dc.run_cmd(["dnf5", "history", "info", m.group(1)], 60)
        if rc != 0:
            continue
        for row in info.splitlines():
            r = re.match(r"^\s*Install\s+(\S+)\s+User\b", row)
            if r:
                nevra = r.group(1).rsplit(".", 1)[0]
                nm = re.match(r"^(.+)-\d+:[^-]+-[^-]+$", nevra)
                if nm:
                    found.add(nm.group(1))
    return {n for n in found
            if dc.run_cmd(["rpm", "-q", n], 10)[0] == 0 and not _referenced(n, blob)}


def discover_flatpaks(blob):
    rc, text, _ = dc.run_cmd(["flatpak", "list", "--app", "--columns=application,origin"], 30)
    if rc != 0:
        return None
    found = set()
    for ln in text.splitlines():
        cols = ln.split()
        if len(cols) >= 2 and cols[1] == "flathub" and not _referenced(cols[0], blob):
            found.add(cols[0])
    return found


def _collect_packages(item, manifest, out):
    try:
        import yaml
        existing = (yaml.safe_load(dc.read_bytes(item["src"]).decode()) or {}) if os.path.isfile(item["src"]) else {}
    except Exception as e:  # noqa: BLE001 — a bad saved file must not silently reset the list
        raise dc.DotfilesError("%s: cannot read the saved package list: %s" % (item["id"], e))
    ignore = set(item.get("ignore", []))
    blob = _reference_blob()
    rpms, flatpaks = set(existing.get("rpms") or []), set(existing.get("flatpaks") or [])
    new_rpms, new_flat = discover_rpms(blob), discover_flatpaks(blob)
    if new_rpms is None:
        out.notes.append("%s: dnf5 history unavailable — rpm list kept as is" % item["id"])
    else:
        for n in sorted(new_rpms - rpms - ignore):
            out.notes.append("%s: found hand-installed rpm %s" % (item["id"], n))
        rpms |= new_rpms - ignore
    if new_flat is None:
        out.notes.append("%s: flatpak unavailable — flatpak list kept as is" % item["id"])
    else:
        for n in sorted(new_flat - flatpaks - ignore):
            out.notes.append("%s: found hand-installed flatpak %s" % (item["id"], n))
        flatpaks |= new_flat - ignore
    lines = [
        "# Written by `just save` (scripts/dotfiles.py): packages no other role installs,",
        "# found from dnf history (hand-run installs) and flathub flatpaks. Additive —",
        "# save never removes an entry; delete a line here to stop installing it. The",
        "# extra_packages role installs what is listed and never uninstalls anything.",
        "rpms:" + ("" if rpms else " []"),
    ]
    lines += ["  - " + n for n in sorted(rpms)]
    lines.append("flatpaks:" + ("" if flatpaks else " []"))
    lines += ["  - " + n for n in sorted(flatpaks)]
    out.files[item["repo"]] = ("\n".join(lines) + "\n").encode()


# ------------------------------------------------------------------ vault
class Vault:
    """Bitwarden secure notes via the bw CLI (BW_SESSION from the caller's
    environment — scripts/save.sh opened it). Secrets travel on stdin only."""

    def _bw(self, args, stdin=None):
        p = subprocess.run(["bw", *args], capture_output=True, text=True, input=stdin)
        if p.returncode != 0:
            # stderr is bw's own message; stdout (which may hold vault data) stays unprinted
            raise dc.DotfilesError("bw %s failed: %s" % (args[0], p.stderr.strip()[:200]))
        return p.stdout

    def find(self, name):
        items = json.loads(self._bw(["list", "items", "--search", name]) or "[]")
        same = [i for i in items if i.get("name") == name]
        if len(same) > 1:
            raise dc.DotfilesError("several vault items are named %s — rename one and retry" % name)
        if same and same[0].get("type") != 2:
            raise dc.DotfilesError("the vault item named %s is not a secure note — rename it and retry" % name)
        return same[0] if same else None

    def upsert(self, name, payload):
        item = self.find(name)
        if item is None:
            item = json.loads(self._bw(["get", "template", "item"]))
            item.update(type=2, name=name, secureNote={"type": 0}, fields=[])
            # a note carries no login/card/identity; a null `login` key also
            # trips community.general.bitwarden's get_field, which tests
            # `field in match["login"]` whenever the key exists
            for key in ("login", "card", "identity"):
                item.pop(key, None)
        item["notes"] = payload
        encoded = self._bw(["encode"], json.dumps(item))
        if item.get("id"):
            self._bw(["edit", "item", item["id"]], encoded)
            return "updated"
        self._bw(["create", "item"], encoded)
        return "created"


def secret_items(items, only=None):
    return [i for i in items if i["kind"] == "secret-file" and (not only or i["id"] in only)]


def vault_pending(items, state, only=None):
    """Secret files whose live copy differs from what we last vaulted."""
    out = []
    for item in secret_items(items, only):
        live = dc.expand(item["live"])
        if os.path.isfile(live) and state["vaulted"].get(item["id"]) != dc.sha256_bytes(dc.read_bytes(live)):
            out.append(item)
    return out


# ---------------------------------------------------------------- scanning
def pii_literals(create=True):
    """Private regexes from the untracked per-user file. The first real save
    creates it (with a header explaining what belongs there); a dry run only
    reads — it must not write anything under $HOME."""
    path = os.path.join(dc.home(), PII_FILE_REL)
    if not os.path.exists(path):
        if create:
            dc.write_atomic(path, PII_FILE_HEADER.encode(), 0o600, 0o700)
        return []
    pats = []
    for ln in open(path, encoding="utf-8"):
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            re.compile(ln)  # fail loudly on a typo rather than scan with a hole in it
            pats.append(ln)
    return pats


def build_scan_config(literals, tmp):
    """The PII scan's config: the committed generic rules plus the private
    literals. Extends by ABSOLUTE path (a relative one resolves against
    gitleaks' working directory)."""
    lines = ['title = "fedora-init save scan"', "", "[extend]",
             'path = "%s"' % os.path.join(root(), ".gitleaks-pii.toml"), ""]
    for n, rx in enumerate(literals, 1):
        lines += ["[[rules]]", 'id = "private-literal-%d"' % n,
                  'description = "a value from ~/%s"' % PII_FILE_REL,
                  "regex = '''%s'''" % rx, ""]
    path = os.path.join(tmp, "gitleaks-pii.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def _gitleaks_dir(gl, stage, cfg, report):
    return subprocess.run(
        [gl, "dir", "--no-banner", "--redact", "--exit-code", str(GITLEAKS_LEAK_RC),
         "--report-format", "json", "--report-path", report, "-c", cfg, stage],
        capture_output=True, text=True)


def scan_stage(stage, tmp, create_pii_file=True):
    """Fail CLOSED: no gitleaks, or any finding, aborts the save. Two passes —
    secrets (the default ruleset) and PII (generic + private literals) — kept
    separate because the default ruleset's global allowlist suppresses some
    PII matches (see .gitleaks-pii.toml)."""
    gl = shutil.which("gitleaks")
    if not gl:
        raise dc.DotfilesError("gitleaks is not installed — `just save` refuses to publish unscanned "
                               "(sudo dnf install gitleaks, or ./install.sh cli-tools)")
    passes = [("secrets", os.path.join(root(), ".gitleaks.toml")),
              ("pii", build_scan_config(pii_literals(create_pii_file), tmp))]
    found = []
    for name, cfg in passes:
        report = os.path.join(tmp, "findings-%s.json" % name)
        p = _gitleaks_dir(gl, stage, cfg, report)
        if p.returncode == 0:
            continue
        if p.returncode != GITLEAKS_LEAK_RC:
            raise dc.DotfilesError("gitleaks (%s pass) failed (rc %d): %s" % (name, p.returncode, p.stderr.strip()[-300:]))
        found += json.load(open(report))
    if not found:
        return
    lines = ["the scan found %d potential secret/PII value(s) — nothing was written:" % len(found)]
    for f in found:
        rel = os.path.relpath(f.get("File", "?"), stage)
        lines.append("  %s:%s  [%s]" % (rel, f.get("StartLine", "?"), f.get("RuleID", "?")))
    lines.append("Remove the value (drop_keys / a narrower keys list in dotfiles/manifest.yml) or, "
                 "for a false positive, add its fingerprint to .gitleaksignore.")
    raise dc.DotfilesError("\n".join(lines))


# --------------------------------------------------------------------- save
def show_diff(rel, old, new):
    if old is None:
        label = "new file"
    elif new is None:
        label = "deleted"
    else:
        label = "modified"
    say("--- %s (%s)" % (rel, label))
    try:
        a = [] if old is None else old.decode("utf-8").splitlines(keepends=True)
        b = [] if new is None else new.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        say("    (binary, %d -> %d bytes)" % (len(old or b""), len(new or b"")))
        return
    diff = list(difflib.unified_diff(a, b, "a/" + rel, "b/" + rel))
    for ln in diff[:200]:
        sys.stdout.write(ln if ln.endswith("\n") else ln + "\n")
    if len(diff) > 200:
        say("    … %d more diff line(s)" % (len(diff) - 200))


def cmd_save(args):
    _manifest, items = load_items()
    only = set(args.only or [])
    unknown = only - {i["id"] for i in items}
    if unknown:
        raise dc.DotfilesError("unknown --only id(s): %s" % ", ".join(sorted(unknown)))
    selected = [i for i in items if not only or i["id"] in only]
    state = dc.load_state()

    if not args.dry_run:
        for tool in ("git",):
            if not shutil.which(tool):
                raise dc.DotfilesError("%s is required" % tool)
        if not args.no_commit and git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() != "main":
            raise dc.DotfilesError("not on main — this repo commits straight to main (AGENTS.md); switch branches or pass --no-commit")

    files, deletes, notes, records, mtimes = {}, [], [], {}, {}
    for item in selected:
        if item["kind"] == "secret-file":
            continue
        got = collect_item(item, _manifest, state, set(args.force_live or []))
        notes += got.notes
        files.update(got.files)
        deletes += got.deletes
        mtimes.update(got.mtimes)
        if got.record is not None:
            records[item["id"]] = got.record

    # what actually differs from the working tree
    write_set = {rel: new for rel, new in files.items() if repo_read(rel) != new}
    delete_set = [rel for rel in deletes if os.path.isfile(os.path.join(root(), rel))]
    secrets = [] if args.no_vault else vault_pending(items, state, only)

    for n in notes:
        say("note: " + n)
    if not write_set and not delete_set and not secrets:
        say("Nothing to save — the repo already matches the live configs.")
        if not args.dry_run:  # a dry run writes nothing, the state file included
            _record(records, state, args)
        return

    # nothing is staged in the tree before the scan passes
    tmp = tempfile.mkdtemp(prefix="fedora-init-save-")
    try:
        stage = os.path.join(tmp, "stage")
        for rel, data in write_set.items():
            dest = os.path.join(stage, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)
        os.makedirs(stage, exist_ok=True)
        if write_set:
            if shutil.which("gitleaks") or not args.dry_run:
                scan_stage(stage, tmp, not args.dry_run)
                say("scan: clean (%d file(s))" % len(write_set))
            else:
                say("warning: gitleaks is not installed — dry run skipped the secret scan "
                    "(a real save refuses to run without it)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for rel in sorted(write_set):
        show_diff(rel, repo_read(rel), write_set[rel])
    for rel in sorted(delete_set):
        show_diff(rel, repo_read(rel), None)
    for item in secrets:
        say("--- vault: %s -> Bitwarden note %s (content never shown)" % (item["live"], item["vault"]))

    if args.dry_run:
        say("\nDry run — nothing written, committed, or sent to the vault.")
        return

    # refuse to mix with uncommitted hand edits to the same paths
    touched = sorted(set(write_set) | set(delete_set))
    if touched:
        dirty = git("status", "--porcelain", "--untracked-files=all", "--", *touched).stdout.strip()
        if dirty:
            raise dc.DotfilesError("uncommitted changes in files save would write — commit or stash them first:\n" + dirty)

    if not args.yes:
        if not sys.stdin.isatty():
            raise dc.DotfilesError("not a terminal — re-run with --yes to commit and push without asking")
        what = "commit and push" if not args.no_commit and not args.no_push else "write"
        if input("\n%s these changes? [y/N] " % what.capitalize()).strip().lower() not in ("y", "yes"):
            say("Aborted — nothing written.")
            return

    # vault first: a failure here leaves the working tree untouched
    vaulted = {}
    for item in secrets:
        live = dc.expand(item["live"])
        raw = dc.read_bytes(live)
        payload = dc.pack_secret(raw, item.get("mode", "0600"))
        action = Vault().upsert(item["vault"], payload)
        vaulted[item["id"]] = dc.sha256_bytes(raw)
        say("vault: %s %s" % (action, item["vault"]))

    for rel, data in write_set.items():
        dest = os.path.join(root(), rel)
        dc.write_atomic(dest, data, 0o644)
        if rel in mtimes:
            # A mirrored tree (the nvim config) is applied with rsync --checksum,
            # which still rewrites a file whose mtime differs and reports the
            # whole mirror "changed" — here, a headless plugin sync that
            # installs nothing new. Giving the repo copy the live file's mtime
            # makes the next apply a true no-op.
            os.utime(dest, ns=(mtimes[rel], mtimes[rel]))
    for rel in delete_set:
        os.unlink(os.path.join(root(), rel))
        d = os.path.dirname(os.path.join(root(), rel))
        while d != root() and os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
            d = os.path.dirname(d)
    state["vaulted"].update(vaulted)
    _record(records, state, args)

    if touched and not args.no_commit:
        ensure_hooks()
        git("add", "-A", "--", *touched)
        ids = sorted({i["id"] for i in selected if i["kind"] != "secret-file"
                      and any(r == i["repo"] or r.startswith(i["repo"] + "/") for r in touched)})
        msg = args.message or "chore(dotfiles): save %s" % (", ".join(ids) or "configs")
        git("commit", "-m", msg, "--", *touched)
        say("committed: " + msg)
        if not args.no_push:
            git("push", "origin", "main")
            say("pushed to origin/main")
    elif touched:
        git("add", "-A", "--", *touched, check=False)
        say("written and staged — not committed (--no-commit)")


def _record(records, state, _args):
    """Remember what was saved so the roles' guard can tell a later live edit
    from the repo having moved."""
    for iid, rec in records.items():
        if rec.startswith("dconf:["):
            for key in json.loads(rec[len("dconf:"):]):
                live = dc.dconf_read(key)
                if live:
                    state["applied"]["dconf:" + key] = dc.sha256_bytes(live.encode())
        else:
            state["applied"][iid] = rec
    dc.save_state(state)


def ensure_hooks():
    """Make this checkout's commits run the pre-commit secret scan."""
    hook = os.path.join(root(), ".githooks", "pre-commit")
    if os.path.isfile(hook) and git("config", "--local", "core.hooksPath", check=False).stdout.strip() != ".githooks":
        git("config", "--local", "core.hooksPath", ".githooks")


# ------------------------------------------------------------- small cmds
def cmd_lint(_args):
    load_items()
    say("dotfiles/manifest.yml is valid")


def cmd_pending_secrets(_args):
    _m, items = load_items()
    tags = {i["role"].replace("_", "-") for i in secret_items(items)
            if not os.path.lexists(dc.expand(i["live"]))}
    for t in sorted(tags):
        say(t)


def cmd_vault_needed(args):
    _m, items = load_items()
    sys.exit(0 if vault_pending(items, dc.load_state(), set(args.only or [])) else 1)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dotfiles.py", description=__doc__.splitlines()[1] if __doc__ else None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("save", help="capture live configs into the repo, commit, push")
    sv.add_argument("--dry-run", action="store_true", help="show what would change; write nothing")
    sv.add_argument("--yes", action="store_true", help="do not ask before committing and pushing")
    sv.add_argument("--no-vault", action="store_true", help="skip Bitwarden secret files")
    sv.add_argument("--no-push", action="store_true")
    sv.add_argument("--no-commit", action="store_true", help="write and stage only")
    sv.add_argument("--only", action="append", metavar="ID", help="only this manifest item (repeatable)")
    sv.add_argument("--force-live", action="append", metavar="ID",
                    help="take the live copy of this role-applied item even if the repo moved (resolves a conflict)")
    sv.add_argument("-m", "--message", help="commit message")
    sv.set_defaults(fn=cmd_save)
    sub.add_parser("lint", help="validate the manifest").set_defaults(fn=cmd_lint)
    sub.add_parser("pending-secrets", help="role tags whose vault-seeded file is absent").set_defaults(fn=cmd_pending_secrets)
    vn = sub.add_parser("vault-needed", help="exit 0 when save must open a vault session")
    vn.add_argument("--only", action="append", metavar="ID")
    vn.set_defaults(fn=cmd_vault_needed)
    args = ap.parse_args(argv)
    try:
        args.fn(args)
    except dc.DotfilesError as e:
        print("error: %s" % e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
