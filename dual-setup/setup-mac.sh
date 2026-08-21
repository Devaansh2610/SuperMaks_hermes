#!/usr/bin/env bash
# setup-mac.sh — run ONCE on the Mac (the "Worker" in the dual-Hermes setup).
#
# Gets this machine to the point where a Ubuntu "Controller" box can reach it
# over SSH and drive its screen via Hermes' computer_use. Safe to re-run: every
# step checks whether it's already done before doing it.
#
#   ./setup-mac.sh
#
# What it does NOT do (needs a human, no way around it):
#   - Tailscale's browser sign-in (opens it for you, then waits)
#   - Granting Accessibility / Screen Recording to CuaDriver.app in System
#     Settings (opens the right pane for you, then waits)
#   - The `sudo` password prompt for enabling Remote Login
#
# Answers you give (Ubuntu's SSH public key, mainly) are remembered in
# ~/.hermes-dual-setup.conf so re-running doesn't ask twice.

set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib.sh
source ./lib.sh

CONFIG_FILE="$HOME/.hermes-dual-setup.conf"

if [[ "$OSTYPE" != darwin* ]]; then
  fail "This script is for the Mac. Run setup-ubuntu.sh on the Ubuntu box instead."
  exit 1
fi

echo "${BOLD}SuperMaks dual-Hermes — Mac Worker setup${RESET}"
echo "${DIM}Every step below checks what's already done first — safe to re-run this any time.${RESET}"

# ── 1. Homebrew ─────────────────────────────────────────────
section "1/5  Homebrew"
if have brew; then
  ok "Homebrew present"
else
  fail "Homebrew not found — this script installs everything else through it."
  info "Install it yourself first: https://brew.sh"
  info '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  exit 1
fi

# ── 2. Tailscale ─────────────────────────────────────────────
section "2/5  Tailscale"
if ! have tailscale; then
  say "Installing Tailscale..."
  brew install tailscale && ok "installed" || { fail "brew install tailscale failed"; exit 1; }
fi
if tailscale status >/dev/null 2>&1 && tailscale ip -4 >/dev/null 2>&1; then
  ok "Already signed in — IP $(tailscale ip -4)"
else
  say "Starting Tailscale sign-in (opens a browser)..."
  sudo tailscale up
  if tailscale ip -4 >/dev/null 2>&1; then
    ok "Signed in — IP $(tailscale ip -4)"
  else
    fail "Tailscale still isn't up. Run 'sudo tailscale up' yourself, then re-run this script."
    exit 1
  fi
fi
MAC_TAILSCALE_IP="$(tailscale ip -4 2>/dev/null || echo "?")"

# ── 3. Hermes + computer_use ────────────────────────────────
section "3/5  Hermes + computer_use"
if have hermes; then
  ok "Hermes present ($(hermes --version 2>/dev/null | head -1))"
else
  say "Installing Hermes..."
  curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
  hash -r
  have hermes || { fail "hermes still not on PATH — open a new terminal and re-run this script."; exit 1; }
fi
apply_command_denylist

if hermes tools list 2>/dev/null | grep -qi "computer_use"; then
  ok "computer_use toolset enabled"
else
  say "Enabling computer_use (installs cua-driver)..."
  hermes tools enable computer_use
fi

say "Running computer-use doctor..."
DOCTOR_OUT="$(hermes computer-use doctor 2>&1 || true)"
echo "$DOCTOR_OUT" | sed 's/^/   /'
if echo "$DOCTOR_OUT" | grep -q "overall: ok"; then
  ok "computer-use doctor: all green"
else
  warn "computer-use doctor found gaps — usually Accessibility / Screen Recording permissions."
  CUA_APP="/Applications/CuaDriver.app"
  [ -d "$CUA_APP" ] || CUA_APP="$HOME/.local/share/cua-driver/CuaDriver.app"
  if [ -d "$CUA_APP" ]; then
    open -R "$CUA_APP" 2>/dev/null || true
  fi
  open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" 2>/dev/null || true
  pause_for_manual_step "In the System Settings window that just opened: click + and add ${CUA_APP}, then do the same under Screen Recording (same Privacy & Security page, different tab)."
  open "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture" 2>/dev/null || true
  read -r -p "   Press Enter once Screen Recording is granted too... " _
  DOCTOR_OUT="$(hermes computer-use doctor 2>&1 || true)"
  echo "$DOCTOR_OUT" | sed 's/^/   /'
  echo "$DOCTOR_OUT" | grep -q "overall: ok" && ok "computer-use doctor: all green now" \
    || warn "still not fully green — you can continue and fix this later with: hermes computer-use doctor"
fi

# ── 4. SSH server ────────────────────────────────────────────
section "4/5  Remote Login (SSH server)"
if sudo systemsetup -getremotelogin 2>/dev/null | grep -qi "on"; then
  ok "Remote Login already on"
else
  say "Enabling Remote Login (needs your password)..."
  sudo systemsetup -setremotelogin on
  sudo systemsetup -getremotelogin 2>/dev/null | grep -qi "on" && ok "Remote Login on" \
    || { fail "couldn't enable Remote Login"; exit 1; }
fi
info "If your Mac firewall is on: System Settings → Network → Firewall → Options — make sure"
info "'Block all incoming connections' is OFF, or that Terminal/sshd is allowed."

# ── 5. Ubuntu's SSH key ─────────────────────────────────────
section "5/5  Authorize the Ubuntu controller's SSH key"
mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
if [ -f "$HOME/.ssh/authorized_keys" ] && [ -s "$HOME/.ssh/authorized_keys" ] \
   && confirm "authorized_keys already has entries — skip adding another key?" y; then
  ok "leaving authorized_keys as-is"
else
  echo "   Paste the Ubuntu box's public key now (run 'cat ~/.ssh/id_ed25519.pub' there if you"
  echo "   haven't already — setup-ubuntu.sh prints it for you), or leave blank to do this later:"
  read -r -p "   ssh-ed25519 AAAA... : " UBUNTU_PUBKEY
  if [ -n "$UBUNTU_PUBKEY" ]; then
    if line_in_file "$HOME/.ssh/authorized_keys" "$UBUNTU_PUBKEY"; then
      chmod 600 "$HOME/.ssh/authorized_keys"
      ok "key added"
    else
      ok "that key was already present"
    fi
  else
    warn "skipped — Ubuntu won't be able to log in until you add its key to"
    warn "  ~/.ssh/authorized_keys  by hand, or re-run this script."
  fi
fi

echo ""
echo "${BOLD}════════════════════════════════════════════════════════════${RESET}"
echo "${BOLD} Mac side is set up.${RESET}"
echo "${BOLD}════════════════════════════════════════════════════════════${RESET}"
echo ""
echo "  Mac username:      ${DIM}$(whoami)${RESET}"
echo "  Mac Tailscale IP:  ${BOLD}${MAC_TAILSCALE_IP}${RESET}   ${DIM}← you'll need this on Ubuntu${RESET}"
echo ""
echo "  Nothing needs to keep running here — no worker process, no tmux session."
echo "  /mac requests spin up their own fresh 'hermes -t computer_use chat' over SSH"
echo "  each time; what matters is already done above: computer_use enabled +"
echo "  permissioned, and sshd on. Verify any time with: hermes computer-use doctor"
echo ""
echo "  Now run setup-ubuntu.sh on the Ubuntu box, and give it the two values above."
