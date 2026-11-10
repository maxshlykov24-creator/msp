#!/bin/sh
# Shared pause, lock, index and outgoing-history checks.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root=$(CDPATH= cd -- "$script_dir/../../../.." && pwd)
cd "$root"
exec python3 "$script_dir/vault_sync.py" pull
