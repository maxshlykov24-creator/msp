#!/bin/bash
# Отвязанный от Cursor ночной прогон code=вариация.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
STATE="$DIR/../../_private/batch_code_eq_variation"
mkdir -p "$STATE"
cd "$DIR"
nohup python3 -u batch_code_eq_variation.py --apply --resume \
  >> "$STATE/nohup.out" 2>&1 &
echo $! > "$STATE/pid.txt"
echo "started pid=$(cat "$STATE/pid.txt")"
echo "log: $STATE/nohup.out"
echo "checkpoint: $STATE/checkpoint.json"
