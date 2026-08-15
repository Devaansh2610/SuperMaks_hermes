#!/usr/bin/env bash
# Wire this Linux machine to the Mac it will be driving.
#
#   ./setup-mac.sh you@macbook.local
#
# Creates a dedicated key, authorizes it on the Mac, writes a `mac` Host block
# with connection multiplexing, then proves the link works and reports which
# macOS permissions still need granting by hand.
set -euo pipefail

cd "$(dirname "$0")"

TARGET="${1:-}"
if [ -z "$TARGET" ]; then
  printf 'Usage: ./setup-mac.sh user@hostname\n' >&2
  printf 'Find it on the Mac: System Settings > General > Sharing > Remote Login.\n' >&2
  exit 1
fi

USER_PART="${TARGET%%@*}"
HOST_PART="${TARGET##*@}"
if [ "$USER_PART" = "$TARGET" ]; then
  printf 'Give a full user@hostname, e.g. maks@macbook.local\n' >&2
  exit 1
fi

KEY="$HOME/.ssh/supermaks_mac_ed25519"
SSH_CONFIG="$HOME/.ssh/config"

mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"

# ── 1. a key used for nothing else ───────────────────────────
if [ ! -f "$KEY" ]; then
  printf '\n[1/5] Creating a dedicated key for the Mac bridge.\n'
  ssh-keygen -t ed25519 -N '' -C "supermaks@$(hostname)" -f "$KEY"
else
  printf '\n[1/5] Key already exists: %s\n' "$KEY"
fi

# ── 2. authorize it on the Mac ───────────────────────────────
printf '\n[2/5] Authorizing the key on %s.\n' "$HOST_PART"
printf '      You will be asked for the Mac account password once, and only once.\n'
ssh-copy-id -i "$KEY.pub" "$TARGET"

# ── 3. the Host block ────────────────────────────────────────
# ControlMaster is the difference between snappy and unusable: the status panel
# polls every few seconds, and without a shared connection each poll pays a full
# SSH handshake.
printf '\n[3/5] Writing the `mac` Host block into %s.\n' "$SSH_CONFIG"
touch "$SSH_CONFIG"; chmod 600 "$SSH_CONFIG"
if grep -qE '^[[:space:]]*Host[[:space:]]+mac[[:space:]]*$' "$SSH_CONFIG"; then
  printf '      A `Host mac` block already exists — leaving it alone.\n'
else
  cat >> "$SSH_CONFIG" <<EOF

# added by SuperMaks setup-mac.sh
Host mac
    HostName $HOST_PART
    User $USER_PART
    IdentityFile $KEY
    IdentitiesOnly yes
    ControlMaster auto
    ControlPath ~/.ssh/supermaks-%r@%h:%p
    ControlPersist 120
    ServerAliveInterval 10
EOF
fi

# ── 4. prove it ──────────────────────────────────────────────
printf '\n[4/5] Testing the link.\n'
if ! ssh -o BatchMode=yes -o ConnectTimeout=8 mac 'true' 2>/dev/null; then
  printf '      FAILED — could not log in without a password.\n' >&2
  printf '      Check Remote Login is on and that %s resolves from here.\n' "$HOST_PART" >&2
  exit 1
fi
MAC_NAME="$(ssh -o BatchMode=yes mac 'scutil --get ComputerName' 2>/dev/null || echo "$HOST_PART")"
printf '      Connected to %s.\n' "$MAC_NAME"

# ── 5. what still needs a human ──────────────────────────────
printf '\n[5/5] Checking what macOS will still refuse.\n'

FRONT="$(ssh -o BatchMode=yes mac 'osascript -e '"'"'tell application "System Events" to get name of first application process whose frontmost is true'"'"' 2>/dev/null' || true)"
if [ -n "$FRONT" ]; then
  printf '      GUI scripting works. Front app right now: %s\n' "$FRONT"
else
  printf '      GUI scripting is BLOCKED. On the Mac, open\n'
  printf '        System Settings > Privacy & Security\n'
  printf '      and add /usr/libexec/sshd-keygen-wrapper under BOTH\n'
  printf '        Accessibility   and   Automation\n'
  printf '      (press Cmd+Shift+G in the file picker to type the path).\n'
fi

if ssh -o BatchMode=yes mac 'screencapture -x /tmp/supermaks-probe.jpg 2>/dev/null && rm -f /tmp/supermaks-probe.jpg'; then
  printf '      Screen capture works.\n'
else
  printf '      Screen capture is BLOCKED. Add the same sshd-keygen-wrapper under\n'
  printf '        Privacy & Security > Screen Recording\n'
fi

if ssh -o BatchMode=yes mac 'command -v cliclick >/dev/null 2>&1'; then
  printf '      cliclick present — mouse and keyboard control available.\n'
else
  printf '      cliclick missing. On the Mac run:  brew install cliclick\n'
  printf '      (needed only for mac-click / mac-type / mac-key)\n'
fi

cat <<EOF

Done. Two things worth knowing:

  * GUI actions need the Mac awake and logged in at the console. A locked login
    screen has no session to script. Consider running  caffeinate -disu  there.
  * Set MAC_SSH_HOST=mac in .env (it already is by default).

Try it:  ./tools/mac-status
EOF
