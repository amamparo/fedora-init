#!/usr/bin/python3
# Apply, guard and record the dotfiles `just save` captured. Thin Ansible
# glue over module_utils/dotfiles_common.py (the same file scripts/dotfiles.py
# imports for the save direction), so what is saved and what is restored are
# one implementation. Must run as the desktop user, never under become — it
# writes $HOME files and reads the user's dconf.
#
# Ops:
#   apply   restore one manifest item the module owns: seed-once files and
#           dirs, seed-keys JSON, add-lines, vault secrets, VS Code extensions.
#           Never overwrites what is already there.
#   guard   for items a ROLE applies (zshrc, ghostty config, the nvim mirror,
#           saved dconf keys): report `drift` when the live file was edited
#           after the last write and never saved, so the role can skip its
#           write instead of clobbering the edit. Read-only.
#   record  after the role wrote: remember the hash of what is live, which is
#           what the next guard compares against.
#
# Check mode is honest: apply/record compute and report but write nothing;
# guard is read-only anyway. Secret payloads arrive as a no_log argument over
# the local connection's stdin (never argv/env) and never appear in results —
# a vault restore reports its target path and mode, never a diff.

DOCUMENTATION = r"""
module: dotfile_sync
short_description: Restore, guard and record fedora-init saved dotfiles
description:
  - See the header of this file and AGENTS.md ("dotfiles").
author:
  - fedora-init
options:
  op:
    description: Operation to run.
    type: str
    required: true
    choices: [apply, guard, record]
  item:
    description:
      - A dotfiles/manifest.yml item plus C(src), the absolute path of its
        saved copy in the repo. Required for file/dir/json/lines/secret items.
    type: dict
  dconf:
    description:
      - For C(guard) and C(record) on saved dconf keys, a list of
        C({key, value}) (value is GVariant text; the HOME placeholder is
        expanded).
    type: list
    elements: dict
  secret_payload:
    description: The vault note text for a C(secret-file) apply.
    type: str
    no_log: true
"""

from ansible.module_utils.basic import AnsibleModule


def main():
    module = AnsibleModule(
        argument_spec={
            "op": {"type": "str", "required": True, "choices": ["apply", "guard", "record"]},
            "item": {"type": "dict"},
            "dconf": {"type": "list", "elements": "dict"},
            "secret_payload": {"type": "str", "no_log": True},
        },
        supports_check_mode=True,
    )
    # Imported AFTER the AnsibleModule is built, on purpose: ansible-lint runs
    # main() with AnsibleModule patched to capture the argument spec and stop,
    # and its own ansible does not know this repo's module_utils path
    # (ansible.cfg) — a top-level import, or one before this constructor,
    # makes every task that uses the module log an "ignored exception".
    # Ansible's module packager still ships the file: it finds the import by
    # walking the whole AST, wherever the statement sits.
    from ansible.module_utils import dotfiles_common as dc

    p = module.params
    ctx = dc.Ctx(check_mode=module.check_mode, secret_payload=p["secret_payload"])
    try:
        if p["op"] == "apply":
            if not p["item"]:
                module.fail_json(msg="apply needs item")
            result = dc.apply_item(p["item"], ctx)
        elif p["dconf"] is not None:
            if p["op"] == "guard":
                result = dc.guard_dconf(p["dconf"], ctx)
            else:
                result = dc.record_dconf([e["key"] for e in p["dconf"]], ctx)
        elif p["item"]:
            op = dc.guard_item if p["op"] == "guard" else dc.record_item
            result = op(p["item"], ctx)
        else:
            module.fail_json(msg="%s needs item or dconf" % p["op"])
    except dc.DotfilesError as e:
        module.fail_json(msg=str(e))
    for w in ctx.warnings:
        module.warn(w)
    module.exit_json(**result)


if __name__ == "__main__":
    main()
