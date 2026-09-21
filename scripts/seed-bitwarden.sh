#!/usr/bin/env bash
#
# fedora-init — `just seed-bitwarden`: upsert the vault items the roles read.
#
# The roles that seed secrets fetch them from these Bitwarden login items:
#   aws        username = AWS Access Key ID, password = Secret Access Key   (roles/aws)
#   anthropic  password = Anthropic API key                                (roles/litellm)
#
# Values come from the environment — SEED_AWS_ACCESS_KEY_ID,
# SEED_AWS_SECRET_ACCESS_KEY, SEED_ANTHROPIC_API_KEY — or, when a variable is
# UNSET and stdin is a terminal, from a hidden prompt. Blank means skip, in
# both forms: an exported-but-empty variable skips without prompting, so a
# wrapper can pass exactly the values it has. The names are prefixed on
# purpose: a shell that exports ANTHROPIC_API_KEY for its own reasons must
# never push it to the vault by accident.
#
# Only what was supplied is written: an item none of whose values were given
# is neither created nor touched, an existing item keeps every field that was
# not supplied (an `aws` item updated with only a new secret keeps its key
# id), and nothing else in the vault is read or written. Creating `aws` needs
# both fields — a half item would seed a broken ~/.aws/credentials that the
# aws role never revisits. Items are matched by EXACT name among items of
# ANY type — the same set the roles' bitwarden lookup sees, which filters
# `bw list --search` by name alone — and a same-named non-login item, or two
# items with the name, abort rather than guess: creating a second `anthropic`
# next to a secure note of that name would be exactly the ambiguity that
# fails the play's result_count=1.
#
# Secrets never ride argv (visible in `ps`): jq reads them from its
# environment. A session this script opened is locked on exit whatever
# happens (the install.sh trap rule); one inherited from the shell is used
# and left alone.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/bw-lib.sh

for tool in jq curl python3; do
    command -v "$tool" >/dev/null 2>&1 || { echo "$tool is required (jq comes with the cli_tools role)" >&2; exit 1; }
done

cleanup() {
    [[ -z ${bw_tmp:-} ]] || rm -rf "$bw_tmp"
    [[ -z ${bw_session_opened:-} ]] || bw lock >/dev/null 2>&1 || true
}
trap cleanup EXIT

# value VAR "prompt label" — the variable's value when it is set (empty =
# skip, no prompt), else a hidden prompt on the terminal, else empty.
value() {
    local v=""
    if [[ -v $1 ]]; then
        v="${!1}"
    elif [[ -t 0 ]]; then
        read -rs -p "$2 (blank to skip): " v </dev/tty
        echo >&2
    fi
    printf '%s' "$v"
}

aws_id="$(value SEED_AWS_ACCESS_KEY_ID 'AWS Access Key ID')"
aws_secret="$(value SEED_AWS_SECRET_ACCESS_KEY 'AWS Secret Access Key')"
anthropic_key="$(value SEED_ANTHROPIC_API_KEY 'Anthropic API key')"

if [[ -z $aws_id && -z $aws_secret && -z $anthropic_key ]]; then
    echo "Nothing to seed (no values supplied) — the vault is untouched."
    exit 0
fi

bw_ensure_installed
bw_open_session "seeding vault items"   # unlock/sign-in + sync, re-sign-in on an expired login
[[ -n ${BW_SESSION:-} ]] || { echo "Bitwarden sign-in failed — nothing written." >&2; exit 1; }
# Edits go to the server, so unlike install.sh a cached vault is no use here.
[[ -n $bw_synced ]] || { echo "The vault could not be synced (offline?) — nothing written." >&2; exit 1; }

# upsert_login NAME USERNAME PASSWORD — an empty field means "leave it alone"
# (or null on create). Existing items are edited in place, so their id,
# folder, URIs, notes and TOTP survive; new ones start from bw's own
# templates so the JSON shape tracks the CLI.
upsert_login() {
    local name=$1 id
    export SEED_U=$2 SEED_P=$3
    id="$(bw list items --search "$name" \
          | jq -r --arg n "$name" '
              [.[] | select(.name == $n)]
              | if length > 1 then error("several items are named \($n) — rename one and retry")
                elif length == 1 then
                  (if .[0].type == 1 then .[0].id
                   else error("the item named \($n) is not a login item — rename it and retry") end)
                else empty end')"
    if [[ -n $id ]]; then
        bw get item "$id" \
          | jq '.login.username = (if $ENV.SEED_U == "" then .login.username else $ENV.SEED_U end)
               | .login.password = (if $ENV.SEED_P == "" then .login.password else $ENV.SEED_P end)' \
          | bw encode | bw edit item "$id" >/dev/null
        echo "updated $name"
    else
        jq -n --arg n "$name" \
            --argjson item "$(bw get template item)" \
            --argjson login "$(bw get template item.login)" '
            $item | .type = 1 | .name = $n | .notes = null
                  | .login = ($login | .uris = [] | .totp = null
                              | .username = (if $ENV.SEED_U == "" then null else $ENV.SEED_U end)
                              | .password = (if $ENV.SEED_P == "" then null else $ENV.SEED_P end))' \
          | bw encode | bw create item >/dev/null
        echo "created $name"
    fi
    unset SEED_U SEED_P
}

# item_count NAME — how many vault items (any type) carry exactly that name.
item_count() {
    bw list items --search "$1" | jq -r --arg n "$1" '[.[] | select(.name == $n)] | length'
}

if [[ -n $aws_id || -n $aws_secret ]]; then
    # A partial UPDATE is fine (the other field survives); a partial CREATE
    # would be a half item the aws role seeds verbatim and never revisits.
    if [[ -z $aws_id || -z $aws_secret ]] && [[ "$(item_count aws)" == 0 ]]; then
        echo "aws: creating the item needs BOTH the Access Key ID and the Secret Access Key (an existing item can be updated with one) — nothing written." >&2
        exit 1
    fi
    upsert_login aws "$aws_id" "$aws_secret"
fi
[[ -z $anthropic_key ]] || upsert_login anthropic "" "$anthropic_key"
echo "Done. Roles fetch these on their next run while the seed target is missing."
