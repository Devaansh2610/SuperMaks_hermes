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

chmod +x start.sh install.sh setup-mac.sh
chmod +x tools/mac-* 2>/dev/null || true

# The mac-* tools are only useful to Hermes if Hermes can find them. The
# dashboard puts ./tools on PATH for its own child process, but a Hermes session
# you start yourself in another terminal won't have it unless it's on your PATH.
tools_dir="$PWD/tools"
if ! command -v mac-sh >/dev/null 2>&1; then
  printf '\nTo let Hermes drive the Mac from any terminal, add this to your shell rc:\n'
  printf '  export PATH="%s:$PATH"\n' "$tools_dir"
fi

if [ ! -f "$HOME/.ssh/config" ] || ! grep -q '^Host mac$' "$HOME/.ssh/config" 2>/dev/null; then
  printf '\nThe Mac bridge is not set up yet. Run ./setup-mac.sh when ready.\n'
fi

printf '\nSuperMaks is ready. Launching with the configured Hermes profile.\n'
printf 'Dashboard: http://127.0.0.1:%s\n\n' "${SUPERMAKS_PORT:-8730}"
exec ./start.sh
