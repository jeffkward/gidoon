# Gidoon

Standalone, installable-anywhere repo. No owner names, paths, tokens, or
project specifics in code or docs — everything instance-specific lives in
the owner's `~/.config/gidoon/` (TOML + env files).

Rules here:
- Stdlib-only Python (no pip, no venv). launchd-safe home (keep this repo
  outside iCloud/synced folders — instances exec bin/gidoon directly).
- TDD, real exit codes, characterization tests before refactors.
- The hard-won engine laws are documented inline where they bite —
  gidoon.py's module docstring is the canonical list. The big ones:
  never mix select() with buffered readers (raw fds only); the result event
  ends a turn, never wait for EOF; the inbound prompt travels over stdin,
  never argv; one getUpdates poller per bot token (competing pollers = 409s
  and a silent bot — field symptom: `poll error (409)` repeating in
  `<name>.log`; the daemon backs off but stays mute until the other poller
  is found and stopped). `gidoon send` is exempt from the poller law: it's
  fire-and-forget, sendMessage only, no listener.

`gidoon update` restarts only the instances whose launchd job runs from the
clone it was invoked from (`plist_targets_repo`) — deliberate, so a second
clone (a dev tree beside an installed one) can't restart the other's
instances out from under it. Keep that scoping if you touch update.

Layout: `gidoon.py` (the engine: config, session state, turn machinery,
status rendering — where the tests point) · `bin/gidoon` (the daemon:
Telegram transport + conversation UX) · `install.sh` (interactive macOS
bootstrap) · `config.example.toml` (every key, documented) · `tests/`
(stdlib unittest, no network).
