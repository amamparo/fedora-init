# fedora-init — `just` recipes (`just` alone lists them; the just rpm comes
# with the cli_tools role). The playbook itself stays `./install.sh`.

_default:
    @just --list --unsorted

# Values come from SEED_AWS_ACCESS_KEY_ID / SEED_AWS_SECRET_ACCESS_KEY /
# SEED_ANTHROPIC_API_KEY, or hidden prompts when unset; a blank skips that
# value. Only supplied values are written — existing items keep their other
# fields, and nothing else in the vault is touched. scripts/seed-bitwarden.sh
# has the details.

# Upsert the Bitwarden items the roles read (aws, anthropic)
seed-bitwarden:
    scripts/seed-bitwarden.sh

# ansible-lint is not a stock rpm; uvx (cli_tools) fetches it when it isn't
# on PATH.

# The safe checks from CLAUDE.md — never runs the playbook
check:
    ansible-playbook site.yml --syntax-check
    if command -v ansible-lint >/dev/null; then ansible-lint --offline; else uvx ansible-lint --offline; fi
    printf '%s\n' install.sh scripts/*.sh | xargs -n1 bash -n
    python3 -m py_compile library/*.py
