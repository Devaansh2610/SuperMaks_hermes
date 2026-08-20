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

## Re-running

Both scripts are idempotent — every step checks its own precondition first.
Safe to run again after fixing something instead of undoing anything by hand.
Non-interactive rerun (skips every prompt it already has an answer for):

```bash
MAC_TAILSCALE_IP=100.x.y.z MAC_USER=devaansh ./setup-ubuntu.sh
```

## What these don't cover yet

The dashboard code itself still assumes the Python server and the browser
you're looking at are the same machine — two known gaps, not yet fixed:

- **Wake-tab windows** (`open_wake_links()` in `server.py`) run local macOS
  `osascript`/`open` calls. Running the dashboard on Ubuntu, those calls
  would execute on Ubuntu, which has no Safari. This project actually had a
  fix for exactly this shape of problem once — a `mac.py` SSH bridge with a
  small allowlisted set of named remote actions, removed in commit `76ff0c5`
  when the project consolidated to single-Mac. Reviving something like it
  (or a targeted piece of it, just for opening URLs) is the next piece.
- **`_host_ok()`** in `server.py` only accepts a `127.0.0.1`/`localhost` Host
  header and binds to loopback only — opening the dashboard at the Ubuntu
  box's Tailscale IP directly from the Mac browser gets rejected. Until
  that's addressed, reach it through the SSH tunnel command printed at the
  end of `setup-ubuntu.sh`.
