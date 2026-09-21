#!/usr/bin/python3
# Seed Alpaca (com.jeffser.Alpaca, the GNOME client for Ollama) with one
# instance row pointing at an Ollama daemon, and select it, so its first
# launch opens on a working chat instead of the onboarding Guide — whose
# "Create Ollama Instance" button downloads a SECOND ollama into the
# sandbox. Alpaca keeps its instances in SQLite (its data dir, alpaca.db,
# table `instance`: id/pinned/type/properties-as-JSON) and the active one in
# the GSettings key com.jeffser.Alpaca selected-instance — which, inside the
# flatpak sandbox (no dconf access in the manifest), GLib backs with the
# keyfile at <config dir>/glib-2.0/settings/keyfile. Verified against
# Alpaca 9.2.5's src/sql_manager.py (CREATE TABLE IF NOT EXISTS per table at
# startup, so a db holding only this one row is completed by the app) and
# src/window.py (the Guide replaces the chat view only when
# `len(SQL.get_instances()) == 0`; the row whose id equals selected-instance
# is selected at startup).
#
# The gate is Alpaca's own Guide trigger: ANY instance row — of any type, in
# the current `instance` table or the pre-9 `instances` one Alpaca migrates
# — means the user has configured Alpaca themselves, and nothing is written
# (the bookmark rule; delete every instance and the next run seeds again,
# exactly when Alpaca would otherwise show the Guide). On a zero-row db an
# existing selected-instance value can only name a deleted row (Alpaca never
# clears the key on removal), so the seed always (re)writes the selection.
# Row and selection land together: the keyfile is written before the
# INSERT commits, so a failed keyfile write leaves no orphan row. Runs as
# the desktop user (a $HOME write), never under become.

DOCUMENTATION = r"""
module: alpaca_instance
short_description: Seed an Alpaca instance row (and select it) when Alpaca has no instances
description:
  - Inserts one row into Alpaca's SQLite instance table, creating the
    database and table when absent, unless any instance row already exists
    (then nothing changes) — the same condition under which Alpaca itself
    would show its onboarding Guide.
  - When it seeds the row, sets (or replaces) the GSettings keyfile key
    selected-instance to the new id.
  - Check-mode aware; must run as the desktop user, never under become.
author:
  - fedora-init
options:
  instance_id:
    description: Primary key of the row (any string without control characters; Alpaca itself uses uuids).
    type: str
    required: true
  instance_type:
    description: Alpaca instance type, e.g. C(ollama) for "Ollama (External)".
    type: str
    default: ollama
  properties:
    description:
      - The row's properties JSON. Keys Alpaca knows but finds missing fall
        back to its class defaults in memory.
    type: dict
    required: true
  data_dir:
    description: Alpaca's XDG data dir (holds alpaca.db).
    type: path
    default: ~/.var/app/com.jeffser.Alpaca/data
  config_dir:
    description: Alpaca's XDG config dir (holds the GSettings keyfile).
    type: path
    default: ~/.var/app/com.jeffser.Alpaca/config
"""

import json
import os
import pathlib
import sqlite3
import tempfile

from ansible.module_utils.basic import AnsibleModule

INSTANCE_DDL = ("CREATE TABLE IF NOT EXISTS instance (id TEXT NOT NULL PRIMARY KEY,"
                " pinned INTEGER NOT NULL, type TEXT NOT NULL, properties TEXT NOT NULL)")
KEYFILE_GROUP = "[com/jeffser/Alpaca]"
KEYFILE_KEY = "selected-instance"


def existing_instance(db):
    """id of any existing row, in the current or the legacy table, else None.

    Read-only open: never creates the db. No lock handling on purpose —
    Alpaca's SQLiteConnection is a per-operation with-block, so a running
    Alpaca never holds the db past sqlite's default 5 s busy timeout.
    """
    if not os.path.exists(db):
        return None
    con = sqlite3.connect(pathlib.Path(db).as_uri() + "?mode=ro", uri=True)
    try:
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("instance", "instances"):
            if table in tables:
                row = con.execute(f"SELECT id FROM {table} LIMIT 1").fetchone()
                if row:
                    return row[0]
    finally:
        con.close()
    return None


def gvariant_string(value):
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def write_selection(path, instance_id):
    """Set selected-instance in the keyfile: replace it inside Alpaca's group, insert
    it after the group header, or append the group. Returns True when replaced."""
    entry = f"{KEYFILE_KEY}={gvariant_string(instance_id)}"
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = []
    replaced = False
    header = None
    in_group = False
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("["):
            in_group = s == KEYFILE_GROUP
            if in_group and header is None:
                header = i
        elif in_group and s.split("=", 1)[0].strip() == KEYFILE_KEY:
            lines[i] = entry
            replaced = True
    if not replaced:
        if header is not None:
            lines.insert(header + 1, entry)
        else:
            if lines and lines[-1].strip():
                lines.append("")
            lines += [KEYFILE_GROUP, entry]
    # Modes as flatpak (config: 0755) and GLib's keyfile backend (glib-2.0/,
    # settings/: 0700; the file: 0600) create them; atomic replace.
    config_dir = os.path.dirname(os.path.dirname(os.path.dirname(path)))
    os.makedirs(config_dir, mode=0o755, exist_ok=True)
    for d in (os.path.dirname(os.path.dirname(path)), os.path.dirname(path)):
        os.makedirs(d, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".keyfile.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
    return replaced


def main():
    module = AnsibleModule(
        argument_spec={
            "instance_id": {"type": "str", "required": True},
            "instance_type": {"type": "str", "default": "ollama"},
            "properties": {"type": "dict", "required": True},
            "data_dir": {"type": "path", "default": "~/.var/app/com.jeffser.Alpaca/data"},
            "config_dir": {"type": "path", "default": "~/.var/app/com.jeffser.Alpaca/config"},
        },
        supports_check_mode=True,
    )
    p = module.params
    db = os.path.join(p["data_dir"], "alpaca.db")
    keyfile = os.path.join(p["config_dir"], "glib-2.0", "settings", "keyfile")
    if any(ord(c) < 32 for c in p["instance_id"]):
        module.fail_json(msg="instance_id must not contain control characters (it is written"
                             " as one keyfile line)")

    try:
        found = existing_instance(db)
    except sqlite3.Error as e:
        module.fail_json(msg=f"Could not read {db}: {e} — not a usable SQLite database, so"
                             " Alpaca itself will not start against it either; move it aside"
                             " (Alpaca recreates it on launch) and re-run. Nothing was modified.")
    if found is not None:
        module.exit_json(changed=False, seeded=False, instance_id=found,
                         msg=f"Alpaca already has an instance ({found}); left alone.")

    if module.check_mode:
        module.exit_json(changed=True, seeded=True, instance_id=p["instance_id"],
                         msg=f"Would seed Alpaca instance {p['instance_id']!r} and select it.")

    os.makedirs(p["data_dir"], mode=0o755, exist_ok=True)
    con = sqlite3.connect(db)
    try:
        con.execute(INSTANCE_DDL)
        # pinned: Alpaca's own column; 0 is what the UI writes for a user-created
        # row. Inert in 9.2.5 — create_instance_row() never passes it to
        # InstanceRow, so the Remove button shows regardless, and an edit-save
        # writes 0 back over whatever was stored.
        con.execute("INSERT INTO instance (id, pinned, type, properties) VALUES (?, 0, ?, ?)",
                    (p["instance_id"], p["instance_type"], json.dumps(p["properties"])))
        write_selection(keyfile, p["instance_id"])
        con.commit()
    except sqlite3.IntegrityError as e:
        module.fail_json(msg=f"Could not insert into {db}: {e} — Alpaca's instance table has"
                             " an unexpected shape; add the instance in Alpaca's Instance"
                             " Manager (Ctrl+I) instead. The row was not committed.")
    except sqlite3.OperationalError as e:
        busy = getattr(e, "sqlite_errorname", "") == "SQLITE_BUSY"
        module.fail_json(msg=f"Could not write {db}: {e}"
                             + (" — close Alpaca and re-run." if busy else ". The row was not committed."))
    except sqlite3.Error as e:
        module.fail_json(msg=f"Could not write {db}: {e}. The row was not committed.")
    except OSError as e:
        module.fail_json(msg=f"Could not write {keyfile}: {e}. The instance row was not"
                             " committed; nothing was modified.")
    finally:
        con.close()

    module.exit_json(changed=True, seeded=True, instance_id=p["instance_id"],
                     msg=f"Seeded Alpaca instance {p['instance_id']!r} and selected it.")


if __name__ == "__main__":
    main()
