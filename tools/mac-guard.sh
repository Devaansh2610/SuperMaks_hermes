#!/usr/bin/env bash
# The confirmation gate for shell-level Mac actions.
#
# mac-sh and mac-osa are general-purpose — they can run anything, including
# something destructive. This is the enforcement point: it classifies the
# command BEFORE ssh is ever called, and for anything risky it files a pending
# approval and BLOCKS until a human clicks Approve or Deny in the dashboard,
# or it times out. This happens in the script itself — the agent cannot argue
# its way past it, because the check runs whether or not it "means well."
#
# Three verdicts:
#   SAFE    — read-only or harmless, runs immediately
#   CONFIRM — files a request in .mac_approvals/ and waits
#   DENY    — refused outright, no confirmation possible, ever

SUPERMAKS_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APPROVAL_DIR="${MAC_APPROVAL_DIR:-$SUPERMAKS_HOME/.mac_approvals}"
CONFIRM_MODE="${MAC_CONFIRM_MODE:-risky}"      # risky | all | off
APPROVAL_TIMEOUT="${MAC_APPROVAL_TIMEOUT:-90}"

mkdir -p "$APPROVAL_DIR"

# Never allowed, confirmation or not. Keep this list about outcomes
# (data loss, disk-level, disabling security), not about specific commands —
# there is always another way to spell rm -rf.
_DENY_PATTERNS=(
  'rm[[:space:]].*-[a-zA-Z]*r[a-zA-Z]*f|rm[[:space:]].*-[a-zA-Z]*f[a-zA-Z]*r'
  '/[[:space:]]*$'                      # an rm/mv/dd target of bare /
  'diskutil[[:space:]]+(erase|partition|reformat|zerodisk)'
  '\bdd[[:space:]]+if='
  '\bmkfs\b'
  '\bshutdown[[:space:]]+-h\b' '\breboot\b' '\bhalt\b'
  'csrutil[[:space:]]+disable'
  'spctl[[:space:]]+--master-disable'
  'erase all content'
  '>[[:space:]]*/dev/r?disk'
  'rm[[:space:]].*(/System|/Library)\b'
  ':\(\)\{[[:space:]]*:\|:'             # fork bomb
)

# Needs a human click, every time this mode is active.
_CONFIRM_PATTERNS=(
  '\brm\b' '\bsudo\b' '\bmv\b' '\bkillall\b' '\bpkill\b'
  'crontab[[:space:]]+-r' 'defaults[[:space:]]+delete'
  'empty trash' 'move to trash' 'delete[[:space:]]'
  '\bchmod[[:space:]]+-R\b' '\bchown[[:space:]]+-R\b'
  'launchctl[[:space:]]+(unload|remove|bootout)'
  '\|[[:space:]]*(ba)?sh\b' 'curl.*\|.*sh' '>[[:space:]]*/'
)

_matches(){ local text="$1"; shift; local p; for p in "$@"; do [[ "$text" =~ $p ]] && return 0; done; return 1; }

mac_classify(){
  local text="$1"
  if _matches "$text" "${_DENY_PATTERNS[@]}"; then echo DENY; return; fi
  case "$CONFIRM_MODE" in
    off)  echo SAFE;    return ;;
    all)  echo CONFIRM; return ;;
  esac
  if _matches "$text" "${_CONFIRM_PATTERNS[@]}"; then echo CONFIRM; else echo SAFE; fi
}

# mac_guard <tool-name> <one-line description> <the actual command/script text>
# Returns 0 if allowed to proceed; 1 with an explanation on stderr otherwise.
# Hermes sees this as the tool call simply taking a while and then either
# succeeding or returning an error — which is exactly what should happen.
mac_guard(){
  local tool="$1" desc="$2" cmdtext="$3"
  local verdict; verdict="$(mac_classify "$cmdtext")"

  case "$verdict" in
    DENY)
      printf 'Refused: this pattern is never allowed from here, approval or not.\n  %s\n' "$cmdtext" >&2
      return 1
      ;;
    SAFE)
      return 0
      ;;
  esac

  # CONFIRM: file a request, then block on it.
  local id dir
  id="$(date +%s%N)$$"
  dir="$APPROVAL_DIR/$id"
  mkdir -p "$dir"
  printf '%s' "$tool"    > "$dir/tool"
  printf '%s' "$desc"    > "$dir/desc"
  printf '%s' "$cmdtext" > "$dir/cmd"
  printf '%s' "$(date +%s)" > "$dir/ts"
  printf 'pending'       > "$dir/status"

  printf 'Filed for approval in the SuperMaks dashboard — waiting up to %ss.\n' "$APPROVAL_TIMEOUT" >&2
  local waited=0 status
  while [ "$waited" -lt "$APPROVAL_TIMEOUT" ]; do
    status="$(cat "$dir/status" 2>/dev/null || echo pending)"
    if [ "$status" = "approved" ]; then rm -rf "$dir"; return 0; fi
    if [ "$status" = "denied" ]; then
      printf 'Denied in the dashboard.\n' >&2
      rm -rf "$dir"; return 1
    fi
    sleep 1; waited=$((waited + 1))
  done
  printf 'Timed out waiting for approval (%ss) — treated as denied.\n' "$APPROVAL_TIMEOUT" >&2
  rm -rf "$dir"
  return 1
}
