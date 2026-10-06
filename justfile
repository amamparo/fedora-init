# fedora-init — `just` recipes (`just` alone lists them; the just rpm comes
# with the cli_tools role). Recipes take their arguments verbatim
# (positional-arguments), so quoting survives the trip into install.sh.

set positional-arguments

_default:
    @just --list --unsorted

# `just install` = `./install.sh`; extra words pass straight through, so
# `just install battery zsh` and `just install --check` work as documented
# for the script.

# Run the playbook (arguments go to install.sh: role substrings, --check…)
install *args:
    ./install.sh "$@"

# Values come from SEED_AWS_ACCESS_KEY_ID / SEED_AWS_SECRET_ACCESS_KEY, or
# hidden prompts when unset; a blank skips that value. Only supplied values
# are written — existing items keep their other fields, and nothing else in
# the vault is touched. scripts/seed-bitwarden.sh has the details.

# Upsert the Bitwarden items the roles read (aws)
seed-bitwarden:
    scripts/seed-bitwarden.sh

# The reverse of `just install`: capture the live configs dotfiles/manifest.yml
# allowlists into the roles that own them, scan them for secrets/PII (the repo
# is PUBLIC; no gitleaks = no save), show the diff, ask, then commit and push.
# Licence files go to Bitwarden, never git. `just save --dry-run` shows what
# would change and writes nothing. Flags: scripts/dotfiles.py save --help.

# Save live configs into the repo (scan, commit, push) and secrets to the vault
save *args:
    scripts/save.sh "$@"

# ansible-lint is not a stock rpm; uvx (cli_tools) fetches it when it isn't
# on PATH. The unit tests need no network; the gitleaks ones skip themselves
# when the binary is absent (cli_tools installs it).

# The safe checks from AGENTS.md — never runs the playbook
check:
    ansible-playbook site.yml --syntax-check
    if command -v ansible-lint >/dev/null; then ansible-lint --offline; else uvx ansible-lint --offline; fi
    printf '%s\n' install.sh scripts/*.sh | xargs -n1 bash -n
    python3 -m py_compile library/*.py module_utils/*.py scripts/*.py
    python3 scripts/dotfiles.py lint
    python3 -m unittest discover -s scripts -p 'test_*.py'
