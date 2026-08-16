#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  printf 'Python 3 is required but was not found.\n' >&2
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  printf 'Created private .env from .env.example.\n'
  printf 'Add your FISH_AUDIO_API_KEY to it for voice.\n'
fi

# Read only the HERMES_CMD setting needed for this prerequisite check. The
# Python server loads the rest of .env itself; this script never evaluates it.
configured_hermes="${HERMES_CMD:-}"
if [ -z "$configured_hermes" ]; then
  while IFS='=' read -r key value; do
    if [ "${key#\#}" = "$key" ] && [ "$key" = "HERMES_CMD" ]; then
      configured_hermes="${value%\"}"; configured_hermes="${configured_hermes#\"}"
      configured_hermes="${configured_hermes%\'}"; configured_hermes="${configured_hermes#\'}"
      break
    fi
  done < .env
fi

if ! command -v hermes >/dev/null 2>&1 && [ -z "$configured_hermes" ]; then
  cat >&2 <<'EOF'
Hermes Agent is required but was not found.
Install it from https://hermes-agent.nousresearch.com/docs/ or set HERMES_CMD in .env.
EOF
  exit 1
fi

chmod +x start.sh install.sh


printf '\nSuperMaks is ready. Launching with the configured Hermes profile.\n'
printf 'Dashboard: http://127.0.0.1:%s\n\n' "${SUPERMAKS_PORT:-8730}"
exec ./start.sh
