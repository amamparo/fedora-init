# Shared logic for the dotfiles round trip (`just save` <-> the roles that
# apply the saved files). PURE STDLIB, no ansible imports, on purpose: the
# check-mode-aware module library/dotfile_sync.py reaches it as
# ansible.module_utils.dotfiles_common (ansible.cfg's module_utils path),
# while scripts/dotfiles.py and the unit tests import this very file by path
# — so what `save` captures and what the roles restore cannot drift apart.
#
# The model, in one paragraph: dotfiles/manifest.yml is an ALLOWLIST of items
# (one live path each, a repo path under the role that owns the concern, a
# kind, an apply policy). save renders live -> repo (drop keys, HOME ->
# token); apply renders repo -> live and never overwrites what a person has
# edited since (seed = only when absent, 3-way guard against a last-applied
# hash for role-owned files). Nothing here prints or logs file CONTENT of a
# secret item — results carry names, hashes and counts only.

import base64
import fnmatch
import gzip
import hashlib
import json
import os
import re
import subprocess
import tempfile

HOME_TOKEN = "__FEDORA_INIT_HOME__"
STATE_REL = ".local/state/fedora-init/dotfiles.json"

# The Bitwarden note field is capped at 10,000 characters AFTER encryption,
# which inflates by roughly a third; stay well under so a note never fails
# at the server after the scan already passed.
NOTE_MAX_CHARS = 7000

ITEM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

# kind -> the apply policies it supports.
KINDS = {
    "file": {"role", "seed"},
    "dir": {"role", "seed"},
    "json-keys": {"seed-keys"},
    "lines": {"add-lines"},
    "secret-file": {"vault-seed"},
    "vscode-extensions": {"seed"},
    "dconf-keys": {"role"},
    "packages": {"role"},
}
# Kinds with a single live path on disk (the rest are computed from tools).
LIVE_PATH_KINDS = {"file", "dir", "json-keys", "lines", "secret-file"}

# ---------------------------------------------------------------- denylist
# The allowlist (the manifest) is the primary control; this is the backstop
# that makes a mistaken manifest edit FAIL instead of publishing a
# credential. Paths are ~-relative prefixes; DENY_EXCEPT carves out the
# exact files/dirs inside a denied tree that are legitimately savable.
DENY_PREFIXES = [
    "~/.ssh", "~/.gnupg", "~/.pki", "~/.android", "~/.aws", "~/.docker",
    "~/.kube", "~/.netrc", "~/.npmrc", "~/.pgpass", "~/.git-credentials",
    "~/.cargo/credentials", "~/.cargo/credentials.toml",
    "~/.config/gh", "~/.config/Bitwarden CLI", "~/.config/rbw",
    "~/.config/pulse", "~/.config/goa-1.0", "~/.config/evolution",
    "~/.config/monitors.xml", "~/.config/Claude",
    "~/.config/BraveSoftware", "~/.config/google-chrome", "~/.config/chromium",
    "~/.config/microsoft-edge", "~/.config/opera", "~/.config/vivaldi",
    "~/.config/mozilla", "~/.mozilla", "~/.var", "~/.steam", "~/.BitwigStudio",
    "~/.local/share/keyrings", "~/.local/share/pki", "~/.local/share/Steam",
    # reaper.ini is app-written and role-seeded; it also carries csurf_*
    # (the web-remote listener) — never a save target.
    "~/.config/REAPER/reaper.ini",
    "~/.claude", "~/.claude.json", "~/.config/Code",
]
DENY_EXCEPT = [
    "~/.claude/settings.json", "~/.claude/keybindings.json",
    "~/.claude/CLAUDE.md", "~/.claude/agents", "~/.claude/commands",
    "~/.claude/hooks", "~/.claude/output-styles",
    "~/.config/Code/User/settings.json", "~/.config/Code/User/snippets",
]
DENY_BASENAMES = [
    "*_history", ".viminfo", "*.pre-fedora-init", "id_rsa*", "id_ed25519*",
    "id_ecdsa*", "id_dsa*", "*.pem", "*.p12", "*.pfx", "*.kdbx", "*.key",
    ".env", ".env.*", "known_hosts*", "credentials", "credentials.json",
    ".credentials.json", "*.keystore", "secrets.*",
]


class DotfilesError(Exception):
    pass


def home():
    return os.environ.get("HOME") or os.path.expanduser("~")


def expand(path):
    """~-prefixed or absolute path -> absolute. Anything else is a manifest bug."""
    if path == "~" or path.startswith("~/"):
        return home() + path[1:]
    if os.path.isabs(path):
        return path
    raise DotfilesError("live path must start with ~/ or be absolute: %r" % path)


def _under(path, prefix):
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")


def deny_reason(path):
    """Why `path` may never be saved or vaulted, or None. Checks the path as
    written AND its realpath, so a symlink cannot smuggle a denied file in."""
    for candidate in {os.path.abspath(path), os.path.realpath(path)}:
        excepted = any(_under(candidate, expand(e)) for e in DENY_EXCEPT)
        if not excepted:
            for prefix in DENY_PREFIXES:
                if _under(candidate, expand(prefix)):
                    return "%s is on the never-save list (%s)" % (candidate, prefix)
        base = os.path.basename(candidate)
        for pat in DENY_BASENAMES:
            if fnmatch.fnmatch(base, pat):
                return "%s matches the never-save filename pattern %s" % (candidate, pat)
    return None


# ---------------------------------------------------------------- manifest
def load_manifest(path):
    import yaml  # PyYAML is an ansible-core dependency, so always present

    with open(path, "r", encoding="utf-8") as f:
        manifest = yaml.safe_load(f)
    if not isinstance(manifest, dict):
        raise DotfilesError("%s: top level must be a mapping" % path)
    return manifest


def declared_dconf_keys(repo_root):
    """dconf keys some role already declares in a task (`key: /org/...`) —
    those are repo-owned values (edit the task), so the saved allowlist may
    never list them."""
    keys = set()
    pat = re.compile(r"\bkey:\s*[\"']?(/[A-Za-z0-9_/.-]+)")
    roles = os.path.join(repo_root, "roles")
    for dirpath, _dirs, files in os.walk(roles):
        for name in files:
            if not name.endswith((".yml", ".yaml")) or "saved" in dirpath.split(os.sep):
                continue
            try:
                with open(os.path.join(dirpath, name), encoding="utf-8") as f:
                    for line in f:
                        keys.update(pat.findall(line))
            except (OSError, UnicodeDecodeError):
                continue
    return keys


def dconf_entries(item):
    """Normalise an item's `keys` to [(dconf_path, schema, key)]."""
    out = []
    for entry in item.get("keys", []):
        if isinstance(entry, str):
            entry = {"key": entry}
        path = entry["key"]
        parts = path.strip("/").split("/")
        schema = entry.get("schema") or ".".join(parts[:-1])
        out.append((path, schema, parts[-1]))
    return out


def validate_manifest(manifest, repo_root):
    """-> list of error strings (empty = valid)."""
    errors = []
    if manifest.get("version") != 1:
        errors.append("manifest version must be 1")
    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        return errors + ["manifest needs a non-empty items list"]
    declared = declared_dconf_keys(repo_root)
    seen = set()
    for n, item in enumerate(items):
        where = "item #%d" % n
        if not isinstance(item, dict):
            errors.append("%s: not a mapping" % where)
            continue
        iid = item.get("id", "")
        where = "item %r" % (iid or n)
        if not ITEM_ID_RE.match(str(iid)):
            errors.append("%s: id must match %s" % (where, ITEM_ID_RE.pattern))
        if iid in seen:
            errors.append("%s: duplicate id" % where)
        seen.add(iid)
        kind, apply_ = item.get("kind"), item.get("apply")
        if kind not in KINDS:
            errors.append("%s: kind must be one of %s" % (where, sorted(KINDS)))
            continue
        if apply_ not in KINDS[kind]:
            errors.append("%s: kind %s supports apply %s" % (where, kind, sorted(KINDS[kind])))
        role, repo = item.get("role"), item.get("repo")
        if not role or (kind != "secret-file" and not isinstance(repo, str)):
            errors.append("%s: role%s is required" % (where, "" if kind == "secret-file" else " and repo"))
            continue
        if isinstance(repo, str):
            if kind == "secret-file":
                errors.append("%s: secret files have no repo copy — drop the repo field" % where)
                continue
            norm = os.path.normpath(repo)
            parts = norm.split("/")
            if os.path.isabs(repo) or norm.startswith("..") or len(parts) < 4 or parts[0] != "roles":
                errors.append("%s: repo must be a relative path under roles/<group>/<role>/" % where)
            elif parts[2] != role:
                errors.append("%s: repo %s is not inside role %r's directory" % (where, repo, role))
            elif not os.path.isdir(os.path.join(repo_root, "roles", parts[1], role)):
                errors.append("%s: role directory roles/%s/%s does not exist" % (where, parts[1], role))
        if kind in LIVE_PATH_KINDS:
            live = item.get("live")
            if not isinstance(live, str):
                errors.append("%s: live is required for kind %s" % (where, kind))
            else:
                try:
                    reason = deny_reason(expand(live))
                except DotfilesError as e:
                    reason = str(e)
                if reason:
                    errors.append("%s: %s" % (where, reason))
        if item.get("drop_keys") is not None:
            if kind != "file" or apply_ != "seed":
                errors.append("%s: drop_keys only works on seed-once files" % where)
            elif not all(isinstance(k, str) and k for k in item["drop_keys"]):
                errors.append("%s: drop_keys must be a list of key paths" % where)
        if kind == "json-keys" and not (item.get("keys") and all(isinstance(k, str) for k in item["keys"])):
            errors.append("%s: json-keys needs a keys list" % where)
        if kind == "lines":
            for rx in item.get("drop_regex", []):
                try:
                    re.compile(rx)
                except re.error as e:
                    errors.append("%s: bad drop_regex %r: %s" % (where, rx, e))
        if kind == "secret-file":
            vault = item.get("vault", "")
            if not str(vault).startswith("fedora-init/"):
                errors.append("%s: vault note names must start with fedora-init/" % where)
            if not re.match(r"^0[0-7]{3}$", str(item.get("mode", "0600"))):
                errors.append("%s: mode must look like 0600" % where)
        if kind == "dconf-keys":
            if not item.get("keys"):
                errors.append("%s: dconf-keys needs a keys allowlist" % where)
            for path, _schema, _key in dconf_entries(item):
                if not path.startswith("/") or path.endswith("/"):
                    errors.append("%s: dconf allowlist entries are single KEYS, never paths: %s" % (where, path))
                if path in declared:
                    errors.append("%s: %s is declared by a role task — edit the task, not the saved list" % (where, path))
    return errors


# ----------------------------------------------------------------- hashing
def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def is_excluded(rel, patterns):
    """rsync-flavoured: '/x' is anchored at the tree root, 'x' matches any
    path component; both also exclude everything beneath a matched dir."""
    for pat in patterns or []:
        if pat.startswith("/"):
            anchored = pat[1:].rstrip("/")
            if rel == anchored or rel.startswith(anchored + "/") or fnmatch.fnmatch(rel, anchored):
                return True
        else:
            if any(fnmatch.fnmatch(part, pat) for part in rel.split("/")):
                return True
    return False


def collect_tree(root, excludes=(), max_file=1_000_000, max_total=5_000_000):
    """-> ({relpath: bytes}, [skipped symlink relpaths]). Symlinks are never
    followed or captured (a link can point anywhere, including at a secret)."""
    files, links, total = {}, [], 0
    if not os.path.isdir(root):
        return files, links
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        reldir = os.path.relpath(dirpath, root)
        reldir = "" if reldir == "." else reldir
        dirnames[:] = sorted(
            d for d in dirnames
            if not is_excluded((reldir + "/" + d).lstrip("/"), excludes))
        for name in sorted(filenames):
            rel = (reldir + "/" + name).lstrip("/")
            if is_excluded(rel, excludes):
                continue
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                links.append(rel)
                continue
            data = read_bytes(full)
            if len(data) > max_file:
                raise DotfilesError("%s is %d bytes — over the %d-byte per-file cap" % (full, len(data), max_file))
            total += len(data)
            if total > max_total:
                raise DotfilesError("%s exceeds the %d-byte per-item cap" % (root, max_total))
            files[rel] = data
        # symlinked dirs show up in dirnames; treat them as links too
        for d in list(dirnames):
            if os.path.islink(os.path.join(dirpath, d)):
                links.append((reldir + "/" + d).lstrip("/"))
                dirnames.remove(d)
    return files, links


def tree_hash(files):
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(rel.encode() + b"\0" + sha256_bytes(files[rel]).encode() + b"\n")
    return h.hexdigest()


# ---------------------------------------------------------- HOME tokenising
def tokenise_home(data, item_id="?"):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise DotfilesError("%s: home_template needs a UTF-8 text file" % item_id)
    if HOME_TOKEN in text:
        raise DotfilesError("%s: the file already contains the %s placeholder" % (item_id, HOME_TOKEN))
    return text.replace(home(), HOME_TOKEN).encode("utf-8")


def detokenise_home(data):
    return data.replace(HOME_TOKEN.encode(), home().encode())


# ------------------------------------------------------------------- JSONC
def strip_jsonc(text):
    """Comments and trailing commas out, string-aware. VS Code settings are
    JSONC; json.loads is not."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif c == "/" and text[i:i + 2] == "//":
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif c == "/" and text[i:i + 2] == "/*":
            end = text.find("*/", i + 2)
            if end < 0:
                raise DotfilesError("unterminated /* comment")
            i = end + 2
            continue
        else:
            out.append(c)
        i += 1
    cleaned = "".join(out)
    # trailing commas: a comma followed (past whitespace) by } or ]
    res, in_str, i, n = [], False, 0, len(cleaned)
    while i < n:
        c = cleaned[i]
        if in_str:
            res.append(c)
            if c == "\\" and i + 1 < n:
                res.append(cleaned[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            res.append(c)
        elif c == ",":
            j = i + 1
            while j < n and cleaned[j] in " \t\r\n":
                j += 1
            if j < n and cleaned[j] in "}]":
                i += 1
                continue
            res.append(c)
        else:
            res.append(c)
        i += 1
    return "".join(res)


def parse_json(data, what="file"):
    text = data.decode("utf-8") if isinstance(data, bytes) else data
    text = text.lstrip("﻿")
    try:
        return json.loads(strip_jsonc(text))
    except (ValueError, DotfilesError) as e:
        # fail CLOSED: an unparseable structured file is never copied raw,
        # because the whole point of parsing it is to remove keys first
        raise DotfilesError("%s is not valid JSON/JSONC (%s) — refusing to handle it" % (what, e))


def dump_json(obj, indent):
    return (json.dumps(obj, indent=indent, ensure_ascii=False) + "\n").encode("utf-8")


def split_path(path):
    parts = path.split("/")
    if not all(parts):
        raise DotfilesError("bad key path %r" % path)
    return parts


def has_path(obj, path):
    for part in split_path(path):
        if not isinstance(obj, dict) or part not in obj:
            return False
        obj = obj[part]
    return True


def get_path(obj, path):
    for part in split_path(path):
        obj = obj[part]
    return obj


def set_path(obj, path, value):
    parts = split_path(path)
    for part in parts[:-1]:
        nxt = obj.get(part)
        if not isinstance(nxt, dict):
            nxt = obj[part] = {}
        obj = nxt
    obj[parts[-1]] = value


def del_path(obj, path):
    parts = split_path(path)
    for part in parts[:-1]:
        obj = obj.get(part) if isinstance(obj, dict) else None
        if not isinstance(obj, dict):
            return
    if isinstance(obj, dict):
        obj.pop(parts[-1], None)


# -------------------------------------------------- live <-> repo rendering
def render_for_repo(item, live_bytes):
    """save direction: live bytes -> the bytes committed to the repo."""
    kind = item["kind"]
    out = live_bytes
    if kind == "file" and item.get("drop_keys"):
        obj = parse_json(live_bytes, item["id"])
        for p in item["drop_keys"]:
            del_path(obj, p)
        out = dump_json(obj, item.get("indent", 4))
    elif kind == "json-keys":
        obj = parse_json(live_bytes, item["id"])
        keep = {}
        for p in item["keys"]:
            if has_path(obj, p):
                set_path(keep, p, get_path(obj, p))
        out = dump_json(keep, 2)
    elif kind == "lines":
        text = live_bytes.decode("utf-8")
        drops = [re.compile(r) for r in item.get("drop_regex", [])]
        kept = [ln.rstrip() for ln in text.splitlines()
                if ln.strip() and not any(d.search(ln) for d in drops)]
        out = ("\n".join(kept) + "\n").encode("utf-8") if kept else b""
    if item.get("home_template"):
        out = tokenise_home(out, item["id"])
    return out


def render_for_live(item, repo_bytes):
    """apply direction: repo bytes -> what lands on disk."""
    return detokenise_home(repo_bytes) if item.get("home_template") else repo_bytes


# ------------------------------------------------------- secret note format
def pack_secret(raw, mode="0600"):
    """-> the text stored in the Bitwarden note: a small header (so a vault
    copy can be compared by hash without writing it anywhere) and the file
    as base64(gzip) — text-safe for binary files, and small enough that a
    7 KB licence/registration file fits the note limit."""
    body = base64.b64encode(gzip.compress(raw, mtime=0)).decode("ascii")
    wrapped = "\n".join(body[i:i + 76] for i in range(0, len(body), 76))
    text = "fedora-init-secret v1\nsha256=%s\nmode=%s\n\n%s\n" % (sha256_bytes(raw), mode, wrapped)
    if len(text) > NOTE_MAX_CHARS:
        raise DotfilesError(
            "secret file compresses to %d note characters, over the %d cap — "
            "Bitwarden attachments (Premium) would be needed" % (len(text), NOTE_MAX_CHARS))
    return text


def unpack_secret(text):
    """-> (raw bytes, mode str, sha256). Verifies the embedded hash."""
    head, _, body = text.partition("\n\n")
    lines = head.splitlines()
    if not lines or lines[0].strip() != "fedora-init-secret v1":
        raise DotfilesError("vault note is not a fedora-init secret (missing header)")
    meta = dict(ln.split("=", 1) for ln in lines[1:] if "=" in ln)
    try:
        raw = gzip.decompress(base64.b64decode("".join(body.split())))
    except (ValueError, OSError, EOFError) as e:
        raise DotfilesError("vault note payload is corrupt (%s)" % e)
    if sha256_bytes(raw) != meta.get("sha256"):
        raise DotfilesError("vault note payload does not match its recorded sha256")
    return raw, meta.get("mode", "0600"), meta["sha256"]


def note_sha(text):
    """sha256 recorded in a note header, without decoding the payload."""
    for ln in text.splitlines()[:4]:
        if ln.startswith("sha256="):
            return ln.split("=", 1)[1].strip()
    return None


# ------------------------------------------------------------------- state
def state_path():
    return os.path.join(home(), STATE_REL)


def load_state():
    try:
        with open(state_path(), encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    st.setdefault("applied", {})
    st.setdefault("vaulted", {})
    return st


def save_state(st):
    path = state_path()
    write_atomic(path, (json.dumps(st, indent=2, sort_keys=True) + "\n").encode(), 0o600, dir_mode=0o700)


def write_atomic(path, data, mode, dir_mode=0o755):
    d = os.path.dirname(path)
    os.makedirs(d, mode=dir_mode, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".fedora-init-")
    try:
        os.fchmod(fd, mode)  # mkstemp starts 0600; widen only as asked
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def drift(live_sha, repo_sha, record_sha):
    """The 3-way rule. Drift ONLY when a record exists AND live differs from
    both the record (what we last wrote/saved) and the repo copy. No record
    (first run, or a machine that predates this feature) is never drift, so
    an already-used machine's first install is not blocked."""
    if live_sha is None or record_sha is None:
        return False
    return live_sha != repo_sha and live_sha != record_sha


# --------------------------------------------------------------- execution
def run_cmd(argv, timeout=60, env=None):
    """-> (rc, stdout, stderr); a missing binary is rc 127, not an exception."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
    except FileNotFoundError:
        return 127, "", "%s: command not found" % argv[0]
    except subprocess.TimeoutExpired:
        return 124, "", "%s: timed out after %ss" % (argv[0], timeout)
    return p.returncode, p.stdout, p.stderr


def dconf_read(key):
    rc, out, _ = run_cmd(["dconf", "read", key])
    return out.strip() if rc == 0 else ""


def make_diff(before, after, name):
    """ansible --diff payload; callers must never pass secret content."""
    return {"before": before, "after": after,
            "before_header": name + " (before)", "after_header": name + " (after)"}


def _text(data, limit=20000):
    try:
        s = data.decode("utf-8")
    except UnicodeDecodeError:
        return "(binary, %d bytes)" % len(data)
    return s if len(s) <= limit else s[:limit] + "\n… (truncated)"


# ---------------------------------------------------------- apply (module)
class Ctx:
    """Execution context: check mode, the vault payload, a command runner
    (swapped in tests). `warnings` collects drift/skip notices for the
    caller to surface."""

    def __init__(self, check_mode=False, secret_payload=None, run=None):
        self.check_mode = check_mode
        self.secret_payload = secret_payload
        self.run = run or run_cmd
        self.warnings = []


def _res(changed=False, msg="", diff=None, **extra):
    r = {"changed": changed, "msg": msg}
    if diff is not None:
        r["diff"] = diff
    r.update(extra)
    return r


def _src_bytes(item):
    src = item.get("src")
    if not src or not os.path.exists(src):
        return None
    return read_bytes(src)


def apply_item(item, ctx):
    kind = item["kind"]
    fn = {
        "file": _apply_file_seed,
        "dir": _apply_dir_seed,
        "json-keys": _apply_json_keys,
        "lines": _apply_lines,
        "secret-file": _apply_secret,
        "vscode-extensions": _apply_vscode,
    }.get(kind)
    if fn is None or item.get("apply") == "role":
        raise DotfilesError("%s: kind %s / apply %s is not applied by the module" % (item["id"], kind, item.get("apply")))
    return fn(item, ctx)


def _apply_file_seed(item, ctx):
    live = expand(item["live"])
    if os.path.lexists(live):
        return _res(False, "%s exists — left alone (seed-once)" % item["live"])
    data = _src_bytes(item)
    if data is None:
        return _res(False, "%s: nothing saved in the repo yet (run `just save`)" % item["id"])
    out = render_for_live(item, data)
    if not ctx.check_mode:
        write_atomic(live, out, int(str(item.get("mode", "0644")), 8))
    return _res(True, "seeded %s" % item["live"], make_diff("", _text(out), live))


def _apply_dir_seed(item, ctx):
    live_root = expand(item["live"])
    repo_files, _ = collect_tree(item.get("src") or "/nonexistent", item.get("excludes", []))
    if not repo_files:
        return _res(False, "%s: nothing saved in the repo yet (run `just save`)" % item["id"])
    added = []
    for rel, data in sorted(repo_files.items()):
        target = os.path.join(live_root, rel)
        if os.path.lexists(target):
            continue
        if not ctx.check_mode:
            write_atomic(target, render_for_live(item, data), 0o644)
        added.append(rel)
    if not added:
        return _res(False, "%s: every saved file already exists" % item["live"])
    return _res(True, "seeded %d file(s) into %s" % (len(added), item["live"]),
                make_diff("", "\n".join(added) + "\n", item["live"]))


def _apply_json_keys(item, ctx):
    live = expand(item["live"])
    data = _src_bytes(item)
    if data is None:
        return _res(False, "%s: nothing saved in the repo yet (run `just save`)" % item["id"])
    want = parse_json(render_for_live(item, data), item["id"] + " (repo copy)")
    if os.path.exists(live):
        cur = parse_json(read_bytes(live), live)
        if not isinstance(cur, dict):
            raise DotfilesError("%s is not a JSON object" % live)
    else:
        cur = {}
    added = {}
    for p in item["keys"]:
        if has_path(want, p) and not has_path(cur, p):
            set_path(cur, p, get_path(want, p))
            set_path(added, p, get_path(want, p))
    if not added:
        return _res(False, "%s already has every saved key (seed-keys never overwrites)" % item["live"])
    if not ctx.check_mode:
        mode = _mode_of(live) if os.path.exists(live) else 0o600
        write_atomic(live, dump_json(cur, item.get("indent", 2)), mode)
    return _res(True, "added %d missing key(s) to %s" % (len(_leaf_paths(added)), item["live"]),
                make_diff("", _text(dump_json(added, 2)), live))


def _mode_of(path):
    try:
        return os.stat(path).st_mode & 0o777
    except OSError:
        return 0o644


def _leaf_paths(obj, prefix=""):
    out = []
    for k, v in obj.items():
        p = prefix + "/" + k if prefix else k
        out += _leaf_paths(v, p) if isinstance(v, dict) and v else [p]
    return out


def _apply_lines(item, ctx):
    live = expand(item["live"])
    data = _src_bytes(item)
    if data is None:
        return _res(False, "%s: nothing saved in the repo yet (run `just save`)" % item["id"])
    want = [ln for ln in render_for_live(item, data).decode("utf-8").splitlines() if ln.strip()]
    have_text = read_bytes(live).decode("utf-8") if os.path.exists(live) else ""
    have = have_text.splitlines()
    missing = [ln for ln in want if ln not in have]
    if not missing:
        return _res(False, "%s already has every saved line" % item["live"])
    if have_text and not have_text.endswith("\n"):
        have_text += "\n"
    new = have_text + "\n".join(missing) + "\n"
    if not ctx.check_mode:
        write_atomic(live, new.encode("utf-8"), _mode_of(live) if os.path.exists(live) else 0o644)
    return _res(True, "added %d line(s) to %s" % (len(missing), item["live"]),
                make_diff(have_text, new, live))


def _apply_secret(item, ctx):
    live = expand(item["live"])
    if os.path.lexists(live):
        return _res(False, "%s exists — left alone (seed-once)" % item["live"])
    if not ctx.secret_payload:
        return _res(False, "%s: no vault note available — nothing restored" % item["id"], skipped=True)
    raw, mode, _sha = unpack_secret(ctx.secret_payload)
    if not ctx.check_mode:
        write_atomic(live, raw, int(item.get("mode", mode), 8) & 0o777, dir_mode=0o755)
    # no diff, ever: the content is the secret
    return _res(True, "restored %s from the vault (mode %s)" % (item["live"], item.get("mode", mode)))


def _apply_vscode(item, ctx):
    data = _src_bytes(item)
    if data is None:
        return _res(False, "%s: nothing saved in the repo yet (run `just save`)" % item["id"])
    want = [ln.strip() for ln in data.decode("utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
    rc, out, err = ctx.run(["code", "--list-extensions"], 120)
    if rc == 127:
        return _res(False, "VS Code (`code`) is not installed — extensions skipped")
    if rc != 0:
        raise DotfilesError("`code --list-extensions` failed: %s" % err.strip()[:200])
    have = {ln.strip().lower() for ln in out.splitlines() if ln.strip()}
    if have:
        # Seed-once, like the settings beside it: VS Code (Settings Sync, the
        # Extensions view) owns the set once it has any. Reinstalling "missing"
        # extensions every run would resurrect whatever was uninstalled on
        # purpose — the very fight the seed policies exist to avoid.
        return _res(False, "VS Code already has %d extension(s) — left alone (seed-once)" % len(have))
    missing = list(want)
    if not missing:
        return _res(False, "no saved VS Code extensions")
    if ctx.check_mode:
        return _res(True, "would install %d VS Code extension(s)" % len(missing),
                    make_diff("", "\n".join(missing) + "\n", "vscode extensions"))
    installed, failed = [], []
    for ext in missing:
        rc, _o, e = ctx.run(["code", "--install-extension", ext], 300)
        (installed if rc == 0 else failed).append(ext)
        if rc != 0:
            ctx.warnings.append("VS Code extension %s did not install: %s" % (ext, e.strip()[:120]))
    return _res(bool(installed), "installed %d of %d VS Code extension(s)" % (len(installed), len(missing)),
                make_diff("", "\n".join(installed) + "\n", "vscode extensions") if installed else None,
                failed=failed)


# --------------------------------------------------- guard / record (3-way)
def _live_and_repo_sha(item):
    """-> (live_sha or None, repo-equivalent sha or None)."""
    live = expand(item["live"])
    if item["kind"] == "dir":
        ex = item.get("excludes", [])
        live_files, _ = collect_tree(live, ex)
        repo_files, _ = collect_tree(item.get("src") or "/nonexistent", ex)
        repo_files = {k: render_for_live(item, v) for k, v in repo_files.items()}
        return (tree_hash(live_files) if live_files else None,
                tree_hash(repo_files) if repo_files else None)
    live_sha = sha256_bytes(read_bytes(live)) if os.path.isfile(live) else None
    data = _src_bytes(item)
    return live_sha, (sha256_bytes(render_for_live(item, data)) if data is not None else None)


def guard_item(item, ctx):
    """For role-applied items, BEFORE the role writes. -> result with
    drift: bool; the role skips its write when true."""
    live_sha, repo_sha = _live_and_repo_sha(item)
    rec = load_state()["applied"].get(item["id"])
    if drift(live_sha, repo_sha, rec):
        ctx.warnings.append(
            "%s: %s has edits that were never saved — keeping them and skipping the repo copy. "
            "Run `just save` to capture them (or delete/revert the live file to take the repo version)."
            % (item["id"], item["live"]))
        return _res(False, "unsaved live edit", drift=True)
    return _res(False, "no unsaved edits", drift=False)


def record_item(item, ctx):
    """For role-applied items, AFTER the role wrote: remember what is live."""
    live_sha, _ = _live_and_repo_sha(item)
    if live_sha is None:
        return _res(False, "%s absent — nothing to record" % item["id"])
    st = load_state()
    if st["applied"].get(item["id"]) == live_sha:
        return _res(False, "record already current")
    if not ctx.check_mode:
        st["applied"][item["id"]] = live_sha
        save_state(st)
    return _res(True, "recorded %s" % item["id"])


def guard_dconf(entries, ctx):
    """entries: [{key, value}] (value = GVariant text, HOME-token allowed).
    -> result with `safe` (keys the role may write) and `drifted`."""
    st, safe, drifted = load_state(), [], []
    for e in entries:
        want = e["value"].replace(HOME_TOKEN, home())
        live = dconf_read(e["key"])
        rec = st["applied"].get("dconf:" + e["key"])
        live_h = sha256_bytes(live.encode()) if live else None
        if drift(live_h, sha256_bytes(want.encode()), rec):
            drifted.append(e["key"])
            ctx.warnings.append(
                "dconf %s was changed outside the repo — keeping the live value. "
                "Run `just save` to capture it." % e["key"])
        else:
            safe.append(e["key"])
    return _res(False, "%d key(s) safe to write, %d drifted" % (len(safe), len(drifted)),
                safe=safe, drifted=drifted)


def record_dconf(keys, ctx):
    st, n = load_state(), 0
    for key in keys:
        live = dconf_read(key)
        if not live:
            continue
        h = sha256_bytes(live.encode())
        if st["applied"].get("dconf:" + key) != h:
            st["applied"]["dconf:" + key] = h
            n += 1
    if n and not ctx.check_mode:
        save_state(st)
    return _res(bool(n), "recorded %d dconf key(s)" % n)
