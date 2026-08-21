#!/usr/bin/env bash
# Shared helpers for setup-mac.sh / setup-ubuntu.sh. Sourced, not run directly.
#
# Everything here is deliberately idempotent: every function either checks
# "is this already done?" before doing it, or is safe to run twice. That's
# what makes it safe to re-run either setup script after a failure instead of
# having to unwind it by hand.

BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; RESET=$'\033[0m'

say()  { echo "${BOLD}==>${RESET} $*"; }
ok()   { echo "   ${GREEN}✓${RESET} $*"; }
warn() { echo "   ${YELLOW}⚠${RESET} $*"; }
fail() { echo "   ${RED}✗${RESET} $*"; }
info() { echo "   ${DIM}$*${RESET}"; }

section() {
  echo ""
  echo "${BOLD}────────────────────────────────────────────────────────────${RESET}"
  echo "${BOLD}$*${RESET}"
  echo "${BOLD}────────────────────────────────────────────────────────────${RESET}"
}

# prompt_var NAME "Question text" "default value"
# Skips the prompt entirely if NAME is already set in the environment (so
# MAC_IP=100.1.2.3 ./setup-ubuntu.sh works non-interactively for reruns/CI),
# and persists whatever's answered into $CONFIG_FILE so the next run of
# *either* script remembers it.
prompt_var() {
  local name="$1" question="$2" default="${3:-}"
  local current="${!name:-}"
  if [ -n "$current" ]; then
    return 0        # already set via env — respect it, don't ask again
  fi
  local saved=""
  if [ -f "$CONFIG_FILE" ]; then
    saved=$(grep -m1 "^${name}=" "$CONFIG_FILE" 2>/dev/null | cut -d= -f2-)
  fi
  local shown_default="${saved:-$default}"
  local answer=""
  if [ -n "$shown_default" ]; then
    read -r -p "   ${name}? [${shown_default}]: " answer
    answer="${answer:-$shown_default}"
  else
    while [ -z "$answer" ]; do
      read -r -p "   ${name}: " answer
    done
  fi
  export "$name=$answer"
  save_var "$name" "$answer"
}

save_var() {
  local name="$1" value="$2"
  touch "$CONFIG_FILE"
  if grep -q "^${name}=" "$CONFIG_FILE" 2>/dev/null; then
    # portable in-place edit: write to temp, replace (avoids sed -i's
    # incompatible syntax between macOS/BSD sed and GNU sed)
    grep -v "^${name}=" "$CONFIG_FILE" > "${CONFIG_FILE}.tmp" || true
    echo "${name}=${value}" >> "${CONFIG_FILE}.tmp"
    mv "${CONFIG_FILE}.tmp" "$CONFIG_FILE"
  else
    echo "${name}=${value}" >> "$CONFIG_FILE"
  fi
}

confirm() {
  # confirm "Question" [default: y|n]
  local question="$1" default="${2:-y}"
  local hint="y/N"; [ "$default" = "y" ] && hint="Y/n"
  local answer=""
  read -r -p "   ${question} [${hint}]: " answer
  answer="${answer:-$default}"
  [[ "$answer" =~ ^[Yy] ]]
}

pause_for_manual_step() {
  echo ""
  warn "$1"
  read -r -p "   Press Enter once that's done (or Ctrl+C to stop here and resume later)... " _
}

# line_in_file FILE LINE — appends LINE to FILE if not already present.
# Used for authorized_keys, shell rc files, etc.
line_in_file() {
  local file="$1" line="$2"
  mkdir -p "$(dirname "$file")"
  touch "$file"
  if ! grep -qF "$line" "$file" 2>/dev/null; then
    echo "$line" >> "$file"
    return 0
  fi
  return 1
}

have() { command -v "$1" >/dev/null 2>&1; }

# apply_command_denylist — hard-blocks a fixed set of destructive commands in
# Hermes' OWN approvals.deny (fnmatch globs, checked before --yolo, cannot be
# bypassed by the agent). This dashboard runs Hermes headless, so its normal
# "ask-approval" prompt has no terminal to render on and just stalls ~60s
# before auto-denying anyway — a hard deny is instant and gives the same
# outcome honestly instead of silently. Idempotent: skips if already applied.
# Read-only commands (ssh, git status, ls, pwd, cat, mkdir, python, npm, uv,
# ...) need nothing — Hermes allows those by default already.
apply_command_denylist() {
  local cfg="$HOME/.hermes/config.yaml"
  mkdir -p "$(dirname "$cfg")"; touch "$cfg"
  if grep -q "SuperMaks: hard-blocked commands" "$cfg" 2>/dev/null; then
    ok "command denylist already applied ($cfg)"
    return 0
  fi
  cat >> "$cfg" <<'EOF'

# ─── SuperMaks: hard-blocked commands ──────────────────────────────────
# fnmatch globs against the full command line, matched BEFORE --yolo /
# approvals.mode=off — cannot be bypassed by the agent.
approvals:
  deny:
    - "rm -rf*"
    - "sudo*"
    - "mkfs*"
    - "dd *"
    - "shutdown*"
    - "reboot*"
    - "kill -9*"
    - "git reset --hard*"
    - "git push --force*"
    - "git push -f*"
EOF
  ok "hard-blocked rm -rf / sudo / mkfs / dd / shutdown / reboot / kill -9 / git reset --hard / git push --force in $cfg"
}
