#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"
export SUPERMAKS_RUNTIME="${SUPERMAKS_RUNTIME:-${JARVIS_RUNTIME:-auto}}"

if ! command -v python3 >/dev/null 2>&1; then
  printf 'SuperMaks needs Python 3. Install Python, then run this script again.\n' >&2
  exit 1
fi

exec python3 server.py
