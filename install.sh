#!/bin/bash
# gidoon installer — give a Claude Code project a mouth (macOS/launchd).
#
# Two ways in:
#   curl -fsSL https://raw.githubusercontent.com/jeffkward/gidoon/main/install.sh | bash
#     (run from your project's directory; prompts for everything it needs)
#   bash install.sh <name> <project-dir>      # from a local clone, no prompts
#
# Everything it touches, in order (idempotent — re-running updates and
# restarts rather than duplicating):
#   1. checks python3 >= 3.11 (tomllib), git, and the claude binary
#   2. piped from the web: clones the repo to ~/.gidoon and re-execs from
#      there, with prompts rebound to the terminal
#   3. re-run in a project that already has an instance? that instance is
#      updated (matched on cwd) instead of a second one being created
#   4. bot token: reuses ~/.config/gidoon/<name>.env if staged, else asks
#      (walking through BotFather /newbot if they don't have a bot yet)
#   5. chat id: reuses the env if staged, else auto-captures — "send your
#      bot a message now" → polls getUpdates for up to 60s
#   6. instance name: suggested from the folder + bot username
#   7. writes <name>.env (chmod 600) and <name>.toml (kept if it exists;
#      new ones get a face picked from a shortlist)
#   8. symlinks ~/.local/bin/gidoon → this clone's bin/gidoon, so the whole
#      machine can run `gidoon send …` (never clobbers a real file there)
#   9. renders + bootstraps launchd job com.gidoon.<name> pointing at THIS
#      clone's bin/gidoon; stdout/stderr → ~/.config/gidoon/<name>.log
#  10. confirms with getMe + a hello message from the bot
#
# Env overrides (mostly for testing and packaging):
#   GIDOON_HOME       where the piped install clones to  (~/.gidoon)
#   GIDOON_REPO_URL   what it clones                     (this repo)
#   GIDOON_CONF_DIR   instance config dir                (~/.config/gidoon)
#   GIDOON_LOCAL_BIN  where the gidoon command goes      (~/.local/bin)
#   GIDOON_HELLO      text of the install greeting; GIDOON_NO_HELLO=1 skips
set -euo pipefail

API="https://api.telegram.org"
CONF_DIR="${GIDOON_CONF_DIR:-$HOME/.config/gidoon}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

die() { echo "gidoon install: $*" >&2; exit 1; }
note() { echo "• $*"; }

# ── 0. bootstrap (curl | bash) ──────────────────────────────────────────────
# Piped from the web there is no clone yet (no bin/gidoon beside us): fetch
# one into $GIDOON_HOME (plain home dir — launchd-safe) and re-exec from it,
# stdin rebound to the terminal so the prompts below can work.
if [ ! -f "$REPO_DIR/bin/gidoon" ]; then
  GIDOON_HOME="${GIDOON_HOME:-$HOME/.gidoon}"
  REPO_URL="${GIDOON_REPO_URL:-https://github.com/jeffkward/gidoon.git}"
  command -v git >/dev/null || die "git not found"
  if [ -d "$GIDOON_HOME/.git" ]; then
    git -C "$GIDOON_HOME" pull --ff-only --quiet 2>/dev/null || true
    note "updated clone at $GIDOON_HOME"
  else
    git clone --quiet "$REPO_URL" "$GIDOON_HOME" \
      || die "clone failed: $REPO_URL"
    note "cloned gidoon to $GIDOON_HOME"
  fi
  # [ -r /dev/tty ] can pass where opening still fails ("Device not
  # configured" headless) — only an actual open proves the terminal.
  if ( : < /dev/tty ) 2>/dev/null; then
    exec bash "$GIDOON_HOME/install.sh" "$@" < /dev/tty
  fi
  exec bash "$GIDOON_HOME/install.sh" "$@" < /dev/null
fi

# Interactive (no args) sets up a gidoon for the CURRENT directory and asks
# for the instance name later, once the bot username is known to suggest
# from. The two-arg form stays for scripted/local-clone installs.
INTERACTIVE=0
if [ "$#" -eq 2 ]; then
  NAME="$1"
  PROJECT_DIR="$2"
elif [ "$#" -eq 0 ] && [ -t 0 ]; then
  INTERACTIVE=1
  NAME=""
  PROJECT_DIR="$PWD"
else
  die "usage: bash install.sh <name> <project-dir>"
fi
[ -d "$PROJECT_DIR" ] || die "project dir not found: $PROJECT_DIR"
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
if [ "$INTERACTIVE" -eq 0 ]; then
  case "$NAME" in
    *[!a-z0-9-]*|"") die "instance name must be lowercase [a-z0-9-]" ;;
  esac
fi

PYTHON3="$(command -v python3)" || die "python3 not found"
"$PYTHON3" -c 'import tomllib' 2>/dev/null \
  || die "python3 >= 3.11 required (tomllib missing in $PYTHON3)"
[ -x "$HOME/.local/bin/claude" ] || command -v claude >/dev/null \
  || die "claude binary not found (~/.local/bin/claude)"

mkdir -p "$CONF_DIR" "$HOME/Library/LaunchAgents"

set_paths() {  # every per-instance path, from $NAME
  ENV_FILE="$CONF_DIR/$NAME.env"
  TOML_FILE="$CONF_DIR/$NAME.toml"
  LOG_FILE="$CONF_DIR/$NAME.log"
  PLIST_LABEL="com.gidoon.$NAME"
  PLIST_FILE="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
}
[ -n "$NAME" ] && set_paths

env_get() {  # env_get KEY  (from $ENV_FILE, empty if absent)
  [ -f "${ENV_FILE:-}" ] || return 0
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$ENV_FILE" | head -1
}

# An interactive re-run in a project that already has an instance is an
# UPDATE, not a second mouth — find it by matching cwd so the token and
# chat id stay staged and nothing gets asked twice.
if [ "$INTERACTIVE" -eq 1 ]; then
  echo "Setting up a gidoon for: $PROJECT_DIR"
  EXISTING="$("$PYTHON3" - "$CONF_DIR" "$PROJECT_DIR" <<'EOF'
import glob, os, sys, tomllib
conf_dir, project_dir = sys.argv[1], sys.argv[2]
for path in sorted(glob.glob(os.path.join(conf_dir, "*.toml"))):
    try:
        with open(path, "rb") as f:
            cwd = tomllib.load(f).get("cwd", "")
    except Exception:
        continue
    cwd = os.path.realpath(os.path.expanduser(cwd))
    if cwd == os.path.realpath(project_dir):
        print(os.path.basename(path)[:-5])
        break
EOF
)"
  if [ -n "$EXISTING" ]; then
    NAME="$EXISTING"
    set_paths
    note "found existing instance '$NAME' for this project — updating it"
  fi
fi

tg() {  # tg <method> [curl -d args...] — prints raw JSON, never the token
  local method="$1"; shift
  curl -fsS --max-time 20 "$API/bot$TOKEN/$method" "$@" 2>/dev/null || true
}

json() {  # json '<python expr over d>'  (stdin = JSON, empty on miss)
  "$PYTHON3" -c "
import json, sys
try:
    d = json.load(sys.stdin)
    v = $1
    print(v if v is not None else '')
except Exception:
    pass"
}

# ── 1. bot + token ──────────────────────────────────────────────────────────
TOKEN="$(env_get GIDOON_BOT_TOKEN)"
if [ -z "$TOKEN" ]; then
  [ -t 0 ] || die "no token staged and no tty to prompt on"
  echo
  read -r -p "Do you have a Telegram bot for this project yet? [y/N] " HAS_BOT
  case "${HAS_BOT:-n}" in
    [Yy]*)
      echo "Find its token in your @BotFather chat: /mybots → your bot →"
      echo "API Token. (It's the long 'digits:letters' string.)"
      ;;
    *)
      echo
      echo "Make one — takes about 30 seconds:"
      echo "  1. Open Telegram and message @BotFather"
      echo "  2. Send /newbot"
      echo "  3. Give it a display name, then a username ending in 'bot'"
      echo "  4. BotFather replies with the token"
      echo
      read -r -p "Press Enter once you have the token… " _
      ;;
  esac
  read -r -p "bot token: " TOKEN
  [ -n "$TOKEN" ] || die "no token given"
fi

BOT_USER="$(tg getMe | json "d['result']['username']")"
[ -n "$BOT_USER" ] || die "token check failed (getMe) — bad token or no network"
note "token OK — bot is @$BOT_USER"

# ── 1b. instance name (suggested from the folder + bot username) ────────────
if [ -z "$NAME" ]; then
  SUGGESTIONS="$("$PYTHON3" - "$REPO_DIR" "$PROJECT_DIR" "$BOT_USER" <<'EOF'
import sys
sys.path.insert(0, sys.argv[1])
import gidoon
print("\n".join(gidoon.suggest_instance_names(sys.argv[2], sys.argv[3])))
EOF
)"
  echo
  echo "Name this instance (used for its config, log, and launchd job):"
  IDX=0
  while IFS= read -r suggestion; do
    [ -n "$suggestion" ] || continue
    IDX=$((IDX + 1))
    eval "SUGGEST_$IDX=\$suggestion"
    echo "  $IDX. $suggestion"
  done <<< "$SUGGESTIONS"
  echo "  or type your own (lowercase letters, digits, hyphens)"
  while [ -z "$NAME" ]; do
    read -r -p "instance name [1]: " REPLY_NAME
    REPLY_NAME="${REPLY_NAME:-1}"
    case "$REPLY_NAME" in
      [1-9]) eval "NAME=\${SUGGEST_$REPLY_NAME:-}" ;;
      *) NAME="$REPLY_NAME" ;;
    esac
    case "$NAME" in
      *[!a-z0-9-]*|"")
        echo "  (lowercase letters, digits, and hyphens only)"
        NAME=""
        ;;
    esac
  done
  set_paths
  note "instance name: $NAME"
fi

# ── 2. chat id ──────────────────────────────────────────────────────────────
CHAT_ID="$(env_get GIDOON_CHAT_ID)"
if [ -z "$CHAT_ID" ]; then
  [ -t 0 ] || die "no chat id in $ENV_FILE and no tty for capture"
  echo "Now send @$BOT_USER any message from YOUR Telegram account…"
  echo "(waiting up to 60s — this only works if no daemon is polling the token)"
  for _ in $(seq 1 12); do
    CHAT_ID="$(tg getUpdates -d timeout=0 \
      | json "next((u['message']['chat']['id'] for u in d['result'] if 'message' in u), None)")"
    [ -n "$CHAT_ID" ] && break
    sleep 5
  done
  [ -n "$CHAT_ID" ] || die "no message seen in 60s — send one and re-run"
  note "captured chat id $CHAT_ID"
fi

# ── 3. env + toml ───────────────────────────────────────────────────────────
umask 077
printf 'GIDOON_BOT_TOKEN=%s\nGIDOON_CHAT_ID=%s\n' "$TOKEN" "$CHAT_ID" \
  > "$ENV_FILE"
chmod 600 "$ENV_FILE"
note "wrote $ENV_FILE"

if [ -f "$TOML_FILE" ]; then
  note "kept existing $TOML_FILE (delete it to regenerate)"
else
  # The face rides every status message ("🐻💭", "🐻 ✅"), so let them pick
  # one now. Any emoji works; this is just a shortlist to save typing.
  FACES=("🐻" "🐵" "🤖" "🐱" "🐺" "🦄" "🦊" "🐸" "🐼")
  FACE="${FACES[0]}"
  if [ "$INTERACTIVE" -eq 1 ]; then
    echo
    echo "Pick a face for this instance — it fronts every status message:"
    echo "  1) ${FACES[0]}   2) ${FACES[1]}   3) ${FACES[2]}   4) ${FACES[3]}   5) ${FACES[4]}"
    echo "  6) ${FACES[5]}   7) ${FACES[6]}   8) ${FACES[7]}   9) ${FACES[8]}"
    while :; do
      read -r -p "face [1], or paste any emoji: " PICK
      case "${PICK:-1}" in
        [1-9]) FACE="${FACES[$((PICK - 1))]}"; break ;;
      esac
      # A pasted face has to survive `emoji = "…"` in the TOML and look
      # like something; anything else re-asks instead of writing a config
      # the daemon can't read.
      if "$PYTHON3" - "$REPO_DIR" "$PICK" <<'EOF'
import sys
sys.path.insert(0, sys.argv[1])
import gidoon
sys.exit(0 if gidoon.is_usable_face(sys.argv[2]) else 1)
EOF
      then
        FACE="$PICK"
        break
      fi
      echo "  (that won't work as a face — pick 1-9 or paste a single emoji)"
    done
  fi
  cat > "$TOML_FILE" <<EOF
# gidoon instance "$NAME" — see config.example.toml in the repo for all keys
label = "$NAME"
env_file = "$ENV_FILE"
cwd = "$PROJECT_DIR"
commands = ["new"]
# The face shown in status messages — change it whenever you like.
emoji = "$FACE"
# Every turn carries gidoon's built-in system prompt (its identity: a headless
# Telegram mouth, not an interactive session). To replace it, set:
#system_prompt = ""
EOF
  note "wrote $TOML_FILE (face: $FACE — change it there anytime)"
fi

# Validate the config exactly the way the daemon will read it.
"$PYTHON3" - "$TOML_FILE" <<EOF
import sys
sys.path.insert(0, "$REPO_DIR")
import gidoon
gidoon.load_config(sys.argv[1])
EOF
note "config validates"

# ── 4. launchd ──────────────────────────────────────────────────────────────
cat > "$PLIST_FILE" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$PLIST_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON3</string>
    <string>$REPO_DIR/bin/gidoon</string>
    <string>--config</string>
    <string>$TOML_FILE</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG_FILE</string>
  <key>StandardErrorPath</key><string>$LOG_FILE</string>
</dict>
</plist>
EOF
note "wrote $PLIST_FILE"

# ── 4b. the `gidoon` command ────────────────────────────────────────────────
# One symlink so the whole machine can run `gidoon send …` / `gidoon update`
# without knowing where the clone lives. Never clobber a real file of that
# name — only our own (or a stale) symlink gets replaced.
LOCAL_BIN="${GIDOON_LOCAL_BIN:-$HOME/.local/bin}"
LINK="$LOCAL_BIN/gidoon"
mkdir -p "$LOCAL_BIN"
if [ -e "$LINK" ] && [ ! -L "$LINK" ]; then
  echo "WARNING: $LINK exists and isn't a symlink — leaving it alone." >&2
  echo "         Run gidoon as $REPO_DIR/bin/gidoon" >&2
else
  ln -sfn "$REPO_DIR/bin/gidoon" "$LINK"
  note "linked $LINK → $REPO_DIR/bin/gidoon"
  case ":$PATH:" in
    *":$LOCAL_BIN:"*) ;;
    *)
      echo "NOTE: $LOCAL_BIN isn't on your PATH. Add it:" >&2
      echo "      echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc" \
        >&2
      ;;
  esac
fi

GUI_DOMAIN="gui/$(id -u)"
launchctl bootout "$GUI_DOMAIN/$PLIST_LABEL" 2>/dev/null || true
launchctl bootstrap "$GUI_DOMAIN" "$PLIST_FILE"
launchctl kickstart -k "$GUI_DOMAIN/$PLIST_LABEL" >/dev/null 2>&1 || true
note "launchd job $PLIST_LABEL bootstrapped"

# ── 5. confirm ──────────────────────────────────────────────────────────────
sleep 2
if launchctl print "$GUI_DOMAIN/$PLIST_LABEL" 2>/dev/null \
    | grep -q 'state = running'; then
  note "daemon running"
else
  echo "WARNING: daemon not in 'running' state yet — check $LOG_FILE" >&2
fi

EMOJI="$("$PYTHON3" -c "
import tomllib
print(tomllib.load(open('$TOML_FILE','rb')).get('emoji','🗣'))")"
if [ "${GIDOON_NO_HELLO:-0}" != "1" ]; then
  HELLO="${GIDOON_HELLO:-$EMOJI gidoon installed — this project now has a mouth. Send me something.}"
  OK="$(tg sendMessage -d "chat_id=$CHAT_ID" --data-urlencode "text=$HELLO" \
    | json "d['ok']")"
  [ "$OK" = "True" ] && note "hello sent to chat $CHAT_ID" \
    || echo "WARNING: hello send failed" >&2
fi

echo
echo "gidoon instance '$NAME' is live: @$BOT_USER ↔ $PROJECT_DIR"
echo "  log:     $LOG_FILE"
echo "  session: $CONF_DIR/$NAME-session.json   (/clear resets)"
echo "  usage:   $CONF_DIR/$NAME-usage.jsonl"
echo
echo "Bonus — anything on this machine can now message you through the bot:"
echo "  gidoon send $NAME \"backup finished ✅\""
echo "  some-job.sh | gidoon send $NAME    # stdin works too"
echo
echo "Also: gidoon update  ·  gidoon uninstall $NAME  ·  gidoon (usage)"
