# Gidoon

**Gidoon** (Anishinaabemowin: *gi-* your + *-doon* mouth, "your mouth") adds *your mouth* to any Claude Code project via Telegram: a bot wired to a persistent headless Claude session in that project's directory. Text your project from anywhere; it answers as itself, speaking with whatever the project has set up (CLAUDE.md, files, MCP tools) and remembering the conversation between texts.

## Requirements

macOS, python3 3.11+, git, the `claude` CLI, and a Telegram bot (send `/newbot` to @BotFather).

## Quick Install

From your Claude Code project directory:

```
curl -fsSL https://raw.githubusercontent.com/jeffkward/gidoon/main/install.sh | bash
```

### Manual Install

1. `git clone` this repo anywhere launchd-safe (not iCloud)
2. Run `bash install.sh <name> <project-dir>`

## Installer Steps

This command clones gidoon to `~/.gidoon`, asks for an instance name, prompts for the Telegram bot token, captures your chat id (you send the bot an initial message), writes the instance config, installs the `gidoon` command in `~/.local/bin`, and starts a launchd daemon (`com.gidoon.<name>`) that survives reboots and network drops. There is nothing to remember to start or run inside the project itself: the daemon runs every turn in the project's directory for you.

Then just text the bot. While Claude works you see a live tool checklist (`💻 Bash ×2 … ✅`); the answer arrives as its own message. `/clear` resets the conversation context, same as it does in Claude Code, and `/help` lists whatever commands your instance understands. (Telegram sends `/start` when a chat is new or was deleted and reopened — gidoon treats that as a clear too, since resuming a conversation you can no longer see is the wrong default.)

Want to run multiple gidoon instances in more than one project? Run the installer again with a different name, project dir, and bot. Instances are independent; the one rule is one bot per instance, because Telegram allows a single poller per bot token.

## How It Works

- One daemon and one Telegram bot per instance; it answers exactly one chat id.
- Every turn is `claude -p` resuming one persistent session in the project directory, so the bot speaks *as* the project.
- A built-in system prompt tells each turn what's happening (the person is on Telegram, this isn't an interactive terminal session or the project's scheduler).
- Permission posture, model, and tool allowlist are enforced via a per-instance config (TOML).

## Structure

| Filename | Purpose |
|---|---|
| `~/.config/gidoon/<name>.toml` | instance config (every key documented in config.example.toml) |
| `~/.config/gidoon/<name>.env` | bot token + your chat id (chmod 600) |
| `~/.config/gidoon/<name>-session.json` | conversation state (type `/clear` to reset it) |
| `~/.config/gidoon/<name>-usage.jsonl` | per-turn token counts (off when a host keeps the books) |
| `~/.config/gidoon/<name>.log` | daemon log |
| `~/Library/LaunchAgents/com.gidoon.<name>.plist` | the launchd job |
| `~/.local/bin/gidoon` | the `gidoon` command (symlink into the clone) |

## Outbound Sending

Installing an instance sets up a bot, a token, and a chat, so gidoon gives every script on the machine outbound messaging for free:

```
gidoon send <name> "backup finished ✅"
nightly-report.sh | gidoon send <name>     # no message arg → stdin
```

Cron jobs, build scripts, other daemons: anything that can run a command can notify you, on the same chat thread as the conversation. It's fire-and-forget: `send` lobs the message over the fence and exits. No listener, no polling, nothing that can collide with the daemon.

### Command Line Gotchas

**Double Quotes:**

Inside **double** quotes your shell expands `$name`, `$1`, `${x}`, `` `cmd` ``, and `$(cmd)` before gidoon ever runs — so a price or a variable-looking word silently disappears:

```
gidoon send work "can I borrow $5 bux?"     →  "can I borrow  bux?"
```

**Single Quotes:**

Use **single** quotes for anything you type by hand. 


```
gidoon send work 'can I borrow $5 bux?'     →  "can I borrow $5 bux?"
```

**Solution: Pipe it in!**


For generated text (logs, reports, anything with amounts or symbols in it) keep the message off the command line entirely and pipe it in:

```
printf '%s\n' "$report" | gidoon send work
gidoon send work < message.txt
```

gidoon receives whatever the shell hands it; text the shell already discarded is unrecoverable. This matters most for agents driving `gidoon send` from a tool call, where a mangled message looks perfectly fine in the logs.

## Managing Instances

```
gidoon                             # what all of this is (usage)
gidoon update                      # pull the latest gidoon, restart its instances
gidoon uninstall <name>            # stop and remove one instance
gidoon uninstall --all             # stop and remove EVERY instance
```

`update` pulls the latest gidoon and restarts your instances.

`uninstall` shows exactly what it will stop and delete, then asks (`--yes` skips the prompt). It removes the launchd job and all instance files, and removing the last instance also removes the `gidoon` command. The clone itself stays put, so `rm -rf ~/.gidoon` is the last step if you want it gone entirely. Deleting the bot is up to you, in BotFather.

## Being a Project's Mouth

Standalone, gidoon needs none of this — it keeps its own books and answers on its own. But a project that already has a chat surface of its own (a web UI, a CLI, a database of the conversation) wants Telegram to be *another mouth on the same conversation*, not a second one running beside it. Five optional keys make that possible, all documented in full in `config.example.toml`:

| Key | What the host gets |
|---|---|
| `turn_lock` | Serializes turns with the host's own, so two `claude -p --resume` processes never run on one session and corrupt it. Both sides use the same path and the same rule. |
| `post_turn_hook` | Each completed turn as JSON on stdin — prompt, reply, duration, and the runtime's own token block passed through untranslated, so the host can keep a ledger with no mapping layer to rot. |
| `reset_hook` | Fires when context is cleared. The host's record of the conversation outlives the session, so without this it holds messages the model no longer remembers and nothing says so. |
| `command_hooks` | The host defines its own slash commands, descriptions and all; they appear in Telegram's menu and in `/help` with no gidoon code involved. |
| `log_token_usage` | Set false when the host records turns itself, so the same turn doesn't land in two ledgers and leave one to rot. |

Both hook families are fire-and-forget on purpose: they run after the answer is already sent, output is ignored, and any failure is logged and goes no further. Bookkeeping must never cost the owner a reply.

The session file is shared state under this arrangement — the host clears context by nulling `session_id` in it, and starts conversations gidoon should continue — so gidoon re-reads it under the turn lock rather than trusting a copy cached at startup.

`gidoon_render.py` is deliberately dependency-free (its import set is exactly `{"re"}`, and a test enforces that) so a host can vendor it and draw the identical tool checklist on its own surface instead of writing a second one that quietly drifts.

## Why not a Telegram MCP plugin?

Telegram MCP plugins for Claude Code are the inside-out version of this. A plugin gives a running session a Telegram surface: the model decides when to check messages and when to reply, and when that session dies (reboot, crash, closed laptop) the bot dies with it. If anything else ever polls the same token (a second session, a test script), Telegram returns 409 to both and the bot goes silent.

gidoon inverts the shape. A small KeepAlive daemon owns Telegram, and each inbound text becomes one headless `claude -p` turn in your project. You get an always-on bot that survives everything a session doesn't, plus conversation UX a plugin can't give you: an acknowledgment on your phone within seconds, a live tool checklist while Claude works, long answers split cleanly, and a session that heals itself when resume fails.

## Contributing

If you're improving gidoon, see CLAUDE.md for hard-won gotchas baked in there.

Run the test suite and ensure all tests pass before submitting a PR:

```
python3 -m unittest discover tests
```

## License

MIT — see [LICENSE](LICENSE).
