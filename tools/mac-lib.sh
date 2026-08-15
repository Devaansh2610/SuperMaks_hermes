#!/usr/bin/env bash
# Shared by every mac-* tool. Not executable on its own.
#
# One multiplexed SSH connection is reused across every call, so a burst of ten
# tool calls costs one handshake rather than ten.

MAC_SSH_HOST="${MAC_SSH_HOST:-mac}"
MAC_TIMEOUT="${MAC_TIMEOUT:-20}"

_mac_opts=(
  -o BatchMode=yes
  -o ConnectTimeout=6
  -o StrictHostKeyChecking=accept-new
  -o ControlMaster=auto
  -o "ControlPath=$HOME/.ssh/supermaks-%r@%h:%p"
  -o ControlPersist=120
  -o ServerAliveInterval=10
)

# Wrap a string in single quotes for the REMOTE shell. ssh flattens its argv into
# one string and the Mac's login shell re-parses it, so anything with a space,
# quote, or $ must be quoted by us or it silently comes apart.
mac_q() {
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

# Run a raw command string on the Mac.
mac_run() {
  ssh "${_mac_opts[@]}" "$MAC_SSH_HOST" -- "$1"
}

# Run AppleScript on the Mac.
mac_osa() {
  mac_run "osascript -e $(mac_q "$1")"
}

# Run a whole multi-line script on the Mac, on stdin.
#
# A script with its own quoting cannot survive being flattened into an ssh
# command line and re-parsed by the remote shell — the quotes get consumed at
# the wrong level. Base64 sidesteps parsing entirely: the wire carries one
# alphanumeric blob and the Mac decodes it back to exactly what we wrote.
mac_script() {
  local blob
  blob="$(printf '%s' "$1" | base64 | tr -d '\n')"
  mac_run "printf %s $blob | base64 -d | /bin/bash"
}

mac_die() { printf '%s\n' "$*" >&2; exit 1; }

mac_need_arg() {
  [ -n "${1:-}" ] || mac_die "usage: $(basename "$0") $2"
}

# macOS refuses GUI automation to an SSH session until it is granted the right
# TCC permission, and the failure is a bare non-zero exit with no explanation.
# Every GUI tool routes failures through here so the agent gets a sentence it
# can actually relay to the user.
mac_gui_hint() {
  cat >&2 <<'EOF'
The Mac refused that GUI action. Usually one of:
  - the Mac is asleep or sitting at the lock screen (no session to script)
  - /usr/libexec/sshd-keygen-wrapper lacks Accessibility, Automation, or
    Screen Recording under System Settings > Privacy & Security
EOF
}
