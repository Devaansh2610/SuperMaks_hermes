#!/usr/bin/env bash
# setup-ubuntu.sh — run ONCE on the Ubuntu box (the "Controller" in the
# dual-Hermes setup).
#
# Gets Hermes + the SuperMaks dashboard running here, wired to reach the Mac
# over SSH for anything GUI (computer_use). Safe to re-run: every step checks
# whether it's already done before doing it.
#
#   ./setup-ubuntu.sh
#
# Run setup-mac.sh on the Mac FIRST (or at least have Tailscale up there) —
# this script needs the Mac's Tailscale IP and username, and will offer to
# copy this box's SSH key over automatically if the Mac already accepts a
# password login.
#
# What it does NOT do (needs a human, no way around it):
#   - Tailscale's browser sign-in (opens it for you, then waits)
#   - Typing the Mac's password once, if key auth isn't already set up
#
# Answers you give are remembered in ~/.hermes-dual-setup.conf so re-running
# doesn't ask twice.

set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib.sh
source ./lib.sh

CONFIG_FILE="$HOME/.hermes-dual-setup.conf"
DASHBOARD_REPO="https://github.com/Devaansh2610/SuperMaks_hermes.git"
DASHBOARD_BRANCH="dual-hermes"
DASHBOARD_DIR="$HOME/dashboard"
SSH_KEY="$HOME/.ssh/id_ed25519"

if [[ "$OSTYPE" == darwin* ]]; then
  fail "This script is for Ubuntu. Run setup-mac.sh on the Mac instead."
  exit 1
fi

echo "${BOLD}SuperMaks dual-Hermes — Ubuntu Controller setup${RESET}"
echo "${DIM}Answers are saved to ${CONFIG_FILE} — safe to re-run this any time.${RESET}"

# ── 1. apt dependencies ─────────────────────────────────────
section "1/9  System packages"
say "apt update + install curl git python3 python3-venv python3-pip openssh-client tmux..."
sudo apt update -qq
sudo apt install -y -qq curl git python3 python3-venv python3-pip openssh-client tmux
ok "packages present"

# ── 2. Hermes ────────────────────────────────────────────────
section "2/9  Hermes"
if have hermes; then
  ok "Hermes present ($(hermes --version 2>/dev/null | head -1))"
else
  say "Installing Hermes..."
  curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
  hash -r
  have hermes || { fail "hermes still not on PATH — open a new shell and re-run this script."; exit 1; }
fi

# ── 3. Tailscale ─────────────────────────────────────────────
section "3/9  Tailscale"
if ! have tailscale; then
  say "Installing Tailscale..."
  curl -fsSL https://tailscale.com/install.sh | sh
fi
if tailscale status >/dev/null 2>&1 && tailscale ip -4 >/dev/null 2>&1; then
  ok "Already signed in — IP $(tailscale ip -4)"
else
  say "Starting Tailscale sign-in (opens a browser — use the SAME account as the Mac)..."
  sudo tailscale up
  tailscale ip -4 >/dev/null 2>&1 && ok "Signed in — IP $(tailscale ip -4)" \
    || { fail "Tailscale still isn't up. Run 'sudo tailscale up' yourself, then re-run this script."; exit 1; }
fi

# ── 4. SSH key ───────────────────────────────────────────────
section "4/9  SSH key for reaching the Mac"
if [ -f "$SSH_KEY" ]; then
  ok "key already exists: $SSH_KEY"
else
  say "Generating an SSH key (no passphrase — needed for unattended automation)..."
  ssh-keygen -t ed25519 -N "" -f "$SSH_KEY" -C "hermes-ubuntu-controller" -q
  ok "generated $SSH_KEY"
fi
echo ""
echo "   ${BOLD}This box's public key${RESET} (needs to end up in the Mac's ~/.ssh/authorized_keys):"
echo "   ${DIM}$(cat "${SSH_KEY}.pub")${RESET}"

# ── 5. Reach the Mac ─────────────────────────────────────────
section "5/9  Mac connection details"
prompt_var MAC_TAILSCALE_IP "Mac's Tailscale IP (run 'tailscale ip -4' on the Mac)"
prompt_var MAC_USER "Mac username (run 'whoami' on the Mac)" "$(whoami)"

if ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new \
       -i "$SSH_KEY" "$MAC_USER@$MAC_TAILSCALE_IP" "echo ok" >/dev/null 2>&1; then
  ok "key-based SSH to the Mac already works"
else
  warn "key-based SSH doesn't work yet."
  if confirm "Try copying this key to the Mac now with ssh-copy-id (asks for the Mac's password once)?" y; then
    ssh-copy-id -i "${SSH_KEY}.pub" "$MAC_USER@$MAC_TAILSCALE_IP" \
      && ok "key copied" \
      || warn "ssh-copy-id failed — add the key printed above to the Mac's ~/.ssh/authorized_keys by hand."
  else
    pause_for_manual_step "On the Mac: append the key printed above to ~/.ssh/authorized_keys (setup-mac.sh does this if you paste it in when it asks)."
  fi
fi

# retry loop so one hiccup doesn't kill the whole script
SSH_OK=0
for attempt in 1 2 3; do
  if ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new \
         -i "$SSH_KEY" "$MAC_USER@$MAC_TAILSCALE_IP" "echo ok" >/dev/null 2>&1; then
    SSH_OK=1; break
  fi
  [ "$attempt" -lt 3 ] && { warn "SSH attempt $attempt failed, retrying..."; sleep 2; }
done
if [ "$SSH_OK" = 1 ]; then
  ok "SSH to the Mac works"
else
  fail "SSH to the Mac still doesn't work after 3 tries."
  info "Check on the Mac: sudo systemsetup -getremotelogin  (must say On)"
  info "                   cat ~/.ssh/authorized_keys  (must contain the key printed above)"
  info "Then re-run this script — everything up to here is already saved."
  exit 1
fi

# ── 6. Hermes SSH backend config ────────────────────────────
section "6/9  Point Hermes at the Mac"
mkdir -p "$HOME/.hermes"
ENV_FILE="$HOME/.hermes/.env"
touch "$ENV_FILE"
for kv in "TERMINAL_SSH_HOST=$MAC_TAILSCALE_IP" "TERMINAL_SSH_USER=$MAC_USER" \
          "TERMINAL_SSH_KEY=$SSH_KEY" "TERMINAL_SSH_PERSISTENT=true"; do
  key="${kv%%=*}"
  grep -v "^${key}=" "$ENV_FILE" > "${ENV_FILE}.tmp" 2>/dev/null || true
  echo "$kv" >> "${ENV_FILE}.tmp"
  mv "${ENV_FILE}.tmp" "$ENV_FILE"
done
ok "wrote SSH settings to $ENV_FILE"

if ! grep -q "_API_KEY=" "$ENV_FILE" 2>/dev/null; then
  echo ""
  echo "   No LLM provider key found in $ENV_FILE yet."
  read -r -p "   Paste an OPENROUTER_API_KEY now (blank to skip and add it later): " OR_KEY
  [ -n "$OR_KEY" ] && echo "OPENROUTER_API_KEY=$OR_KEY" >> "$ENV_FILE" && ok "saved"
fi

hermes config set terminal.backend ssh >/dev/null 2>&1 && ok "terminal.backend = ssh" \
  || warn "couldn't set terminal.backend — set it by hand: hermes config set terminal.backend ssh"

say "Testing the SSH backend end to end (Ubuntu Hermes → Mac)..."
BACKEND_TEST="$(hermes chat -q "Run: echo 'dual-hermes SSH backend OK' && whoami" 2>&1 || true)"
echo "$BACKEND_TEST" | tail -6 | sed 's/^/   /'
echo "$BACKEND_TEST" | grep -q "dual-hermes SSH backend OK" \
  && ok "Hermes is reaching the Mac" \
  || warn "that didn't look right — check the output above"

# ── 7. Clone the dashboard ──────────────────────────────────
section "7/9  SuperMaks dashboard"
if [ -d "$DASHBOARD_DIR/.git" ]; then
  say "Dashboard already cloned — pulling latest on $DASHBOARD_BRANCH..."
  git -C "$DASHBOARD_DIR" fetch origin "$DASHBOARD_BRANCH"
  git -C "$DASHBOARD_DIR" checkout "$DASHBOARD_BRANCH"
  git -C "$DASHBOARD_DIR" pull --ff-only origin "$DASHBOARD_BRANCH"
else
  say "Cloning $DASHBOARD_REPO ($DASHBOARD_BRANCH)..."
  git clone --branch "$DASHBOARD_BRANCH" "$DASHBOARD_REPO" "$DASHBOARD_DIR"
fi
ok "dashboard at $DASHBOARD_DIR on branch $(git -C "$DASHBOARD_DIR" branch --show-current)"

if [ ! -f "$DASHBOARD_DIR/.env" ] && [ -f "$DASHBOARD_DIR/.env.example" ]; then
  cp "$DASHBOARD_DIR/.env.example" "$DASHBOARD_DIR/.env"
  ok "created dashboard .env from .env.example — edit $DASHBOARD_DIR/.env before your first real run"
else
  ok "dashboard .env already exists — leaving it alone"
fi
# This box has no browser you'd ever look at — you'll open the dashboard on
# the Mac (see the tunnel command at the end). Without this, server.py's
# default (SUPERMAKS_OPEN=1) tries webbrowser.open() here on every start.
if grep -q "^SUPERMAKS_OPEN=" "$DASHBOARD_DIR/.env" 2>/dev/null; then
  sed -i.bak 's/^SUPERMAKS_OPEN=.*/SUPERMAKS_OPEN=0/' "$DASHBOARD_DIR/.env" && rm -f "$DASHBOARD_DIR/.env.bak"
else
  echo "SUPERMAKS_OPEN=0" >> "$DASHBOARD_DIR/.env"
fi
ok "SUPERMAKS_OPEN=0 (no browser launch attempt on this headless box)"

# ── 8. Start Hermes main + dashboard ────────────────────────
section "8/9  Starting services"
tmux kill-session -t hermes-main 2>/dev/null || true
tmux new-session -d -s hermes-main -x 120 -y 40 'hermes'
ok "hermes-main tmux session started"

tmux kill-session -t dashboard 2>/dev/null || true
chmod +x "$DASHBOARD_DIR/start.sh" "$DASHBOARD_DIR/install.sh" 2>/dev/null || true
tmux new-session -d -s dashboard -c "$DASHBOARD_DIR" './start.sh'
sleep 2
if tmux has-session -t dashboard 2>/dev/null; then
  ok "dashboard tmux session started"
else
  fail "dashboard session died immediately — check: tmux capture-pane -t dashboard -p"
fi

# ── 9. Verify ────────────────────────────────────────────────
section "9/9  Verification"
UBUNTU_TAILSCALE_IP="$(tailscale ip -4 2>/dev/null || echo "?")"
PORT="$(grep -m1 '^SUPERMAKS_PORT=' "$DASHBOARD_DIR/.env" 2>/dev/null | cut -d= -f2)"
PORT="${PORT:-8730}"

echo ""
echo "${BOLD}════════════════════════════════════════════════════════════${RESET}"
echo "${BOLD} Ubuntu Controller is set up.${RESET}"
echo "${BOLD}════════════════════════════════════════════════════════════${RESET}"
echo ""
echo "  Ubuntu Tailscale IP:  ${BOLD}${UBUNTU_TAILSCALE_IP}${RESET}"
echo "  Dashboard port:       ${BOLD}${PORT}${RESET}  ${DIM}(from SUPERMAKS_PORT in .env)${RESET}"
echo ""
echo "  ${YELLOW}Important:${RESET} the dashboard server only accepts requests whose Host header"
echo "  is 127.0.0.1 or localhost — opening http://${UBUNTU_TAILSCALE_IP}:${PORT} directly in"
echo "  the Mac's browser will currently be rejected. Until that's changed in the"
echo "  dashboard code, reach it through an SSH tunnel from the Mac instead:"
echo ""
echo "    ${BOLD}ssh -N -L ${PORT}:127.0.0.1:${PORT} $(whoami)@${UBUNTU_TAILSCALE_IP}${RESET}"
echo ""
echo "  then open ${BOLD}http://127.0.0.1:${PORT}${RESET} on the Mac."
echo ""
echo "  Sessions:"
echo "    Hermes:     tmux attach -t hermes-main"
echo "    Dashboard:  tmux attach -t dashboard   ${DIM}(Ctrl+B, D to detach)${RESET}"
echo ""
echo "  Re-run this script any time — it picks up where it left off."
