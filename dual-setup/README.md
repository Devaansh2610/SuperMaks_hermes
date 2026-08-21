# Dual-Hermes setup — two scripts, one per machine

Consolidates everything in `~/Desktop/hermes-dual-setup/COMPLETE_SETUP_GUIDE.md`
into one script per machine. Each one walks through every step in order,
checks whether that step is already done before doing it (so re-running after
a failure just picks up where it left off), and pauses with clear instructions
at the handful of points that genuinely need a human (Tailscale's browser
sign-in, granting macOS Accessibility/Screen Recording, one password prompt).

## Order

1. **On the Mac:**
   ```bash
   cd dual-setup
   ./setup-mac.sh
   ```
   Ends by printing the Mac's Tailscale IP and username — you'll need both
   for the next step.

2. **On Ubuntu**, with this repo checked out on the `dual-hermes` branch:
   ```bash
   cd dual-setup
   ./setup-ubuntu.sh
   ```
   Asks for the Mac's IP/username from step 1 (remembers the answer in
   `~/.hermes-dual-setup.conf` for next time), generates and offers to copy
   over its own SSH key, wires Hermes' `terminal.backend` to reach the Mac,
   clones/updates the dashboard, and starts both `hermes-main` and
   `dashboard` in tmux.

Both scripts also call `apply_command_denylist` (in `lib.sh`) right after
Hermes is confirmed installed — hard-blocks `rm -rf`, `sudo`, `mkfs`, `dd`,
`shutdown`, `reboot`, `kill -9`, `git reset --hard`, and `git push --force` in
`~/.hermes/config.yaml`'s `approvals.deny`, on whichever machine's Hermes is
the one actually receiving tool calls (Ubuntu's, in this setup). See the
README's Security section for why a hard deny beats Hermes' normal "ask"
prompt in a headless dashboard.

## Re-running

Both scripts are idempotent — every step checks its own precondition first.
Safe to run again after fixing something instead of undoing anything by hand.
Non-interactive rerun (skips every prompt it already has an answer for):

```bash
MAC_TAILSCALE_IP=100.x.y.z MAC_USER=devaansh ./setup-ubuntu.sh
```

## What these don't cover yet

The dashboard code originally assumed the Python server and the browser
you're looking at are the same machine. One of the two gaps that broke is
fixed; one remains:

- ✅ **Wake-tab windows** (`open_wake_links()` in `server.py`) used to run
  local macOS `osascript`/`open` calls unconditionally — broken the moment
  the dashboard runs on Ubuntu. Fixed via `mac_bridge.py`: every Mac-only
  action now runs locally if this box IS the Mac, or over SSH to
  `SUPERMAKS_MAC_SSH_HOST` (falling back to the same `TERMINAL_SSH_HOST` this
  setup script already wrote to `~/.hermes/.env`) if it isn't. This revives
  the shape of the `mac.py` SSH bridge this project had once before
  (removed in `76ff0c5` when it consolidated to single-Mac), scoped to just
  what the wake flow needs.
- ⏳ **`_host_ok()`** in `server.py` still only accepts a `127.0.0.1`/
  `localhost` Host header and binds to loopback only — opening the dashboard
  at the Ubuntu box's Tailscale IP directly from the Mac browser gets
  rejected. Reach it through the SSH tunnel command `setup-ubuntu.sh` prints
  at the end until this one's addressed too.
