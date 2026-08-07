"""gidoon — the turn machinery under a gidoon instance.

No owner names, paths, or project specifics live here — everything
instance-bound arrives via the TOML config in ~/.config/gidoon/<name>.toml.

The hard-won laws, learned live before this repo was born:
  - stream_claude uses binary pipes + raw os.read with select(), never a
    buffered TextIOWrapper — readline() buffers ahead, so the result line
    can sit in Python's buffer while select() waits on an fd that will
    never signal again (bit a live daemon before this repo existed).
  - The `result` event IS the turn's end — never wait for EOF. Under
    launchd, claude has been seen lingering after completing (open API
    sockets keep node alive); the finally block reaps or kills leftovers.
  - The prompt travels over stdin, never argv — an untrusted message that
    starts with '-' must not be parseable as a CLI flag.
  - "No conversation found" on stderr while resuming → a distinct
    resume_failed marker so the caller can drop the dead session id and
    retry fresh, rather than wedging every future turn. The signature can
    arrive alongside a clean-looking empty result event, not only on a
    no-result crash.
Rendering is NOT here. The tool-checklist layer (format_tool_label,
collapse_tool_runs, extract_text, …) lives in gidoon_render.py, which is
pure and meant to be vendored by a host project that renders the same turn
on its own surface. Every name is re-exported below, so `import gidoon as
core` sees no difference.

Stdlib only. Python >= 3.11 (tomllib).
"""
import json
import os
import plistlib
import re
import select
import signal
import subprocess
import time
import tomllib

CONFIG_DIR = os.path.expanduser("~/.config/gidoon")

DEFAULT_CLAUDE_BIN = "~/.local/bin/claude"
DEFAULT_EMOJI = "\U0001f5e3"  # 🗣
DEFAULT_TIMEOUT_SECS = 600

# Appended to the system prompt of EVERY turn (claude --append-system-prompt),
# so the turn knows what it is before it reads a word of the project's own
# docs — a project CLAUDE.md written for interactive sessions can otherwise
# make the mouth act like one (a live instance's first turn once ran its
# project's session-start checklist and armed a duplicate scheduler cron).
# Owners can replace it via the `system_prompt` TOML key.
DEFAULT_SYSTEM_PROMPT = (
    "The person is talking to you over chat (Telegram), not at a terminal: "
    "this is a headless conversational turn, and your final answer text is "
    "relayed to them verbatim — just answer; nothing else you print reaches "
    "them. You are not this project's scheduler, heartbeat, or deploy "
    "owner: skip any session-start checklists in the project docs, and "
    "never create, modify, or delete scheduled jobs. The person is likely "
    "away from this machine — never open apps, windows, or browser tabs on "
    "it, and don't start interactive commands or long-lived foreground "
    "processes that would hang the turn. They can't see the terminal: "
    "confirm before anything destructive or irreversible."
)

# The command registry — everything an instance MAY enable in its TOML
# `commands` list. Just /new for now (not even /help); the registry stays
# a table so instances can add later.
COMMANDS = {
    "new": "start a fresh conversation (forgets chat history, not the project)",
}

# Sane PATH for the child claude process (and hooks): claude's home, brew,
# and the system paths — deliberately nothing project- or owner-specific.
SANE_PATH = ":".join([
    os.path.expanduser("~/.local/bin"),   # claude
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin", "/bin", "/usr/sbin", "/sbin",
])
ENV_PASSTHROUGH = ("HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG")


# ── instances: naming, files, launchd jobs ─────────────────────────────────

def sanitize_instance_name(text):
    """Coerce arbitrary text to the instance-name rule (lowercase
    [a-z0-9-]): lowercase, every other run of characters becomes one
    hyphen, edges trimmed. Returns "" if nothing usable survives."""
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def strip_bot_suffix(username):
    """Drop a bot-username suffix: "MyAwesomeBot" -> "MyAwesome",
    "my_thing_bot" -> "my_thing". Only a separator or a capital B marks a
    real suffix, so an ordinary word ending in "bot" ("robot") is left
    alone, as is a name that is nothing but the suffix."""
    stripped = re.sub(r"(?:[_-][Bb]ot|Bot)$", "", username or "")
    return stripped or (username or "")


def suggest_instance_names(project_dir, bot_username=None):
    """Ordered, deduped instance-name suggestions for the installer: the
    project's folder name first (what the person is most likely thinking
    of), then the bot username minus its Bot suffix. Unusable candidates
    are dropped, so this can return []."""
    candidates = [
        sanitize_instance_name(os.path.basename(
            os.path.normpath(project_dir or ""))),
        sanitize_instance_name(strip_bot_suffix(bot_username)),
    ]
    out = []
    for name in candidates:
        if name and name not in out:
            out.append(name)
    return out


LAUNCHD_PREFIX = "com.gidoon."

# Everything an instance owns under CONFIG_DIR, as "<name>" + suffix. Listed
# explicitly rather than globbed on "<name>*" so a name can never claim a
# longer sibling's files ("work" vs "workshop").
#
# "-costs.jsonl" is the pre-2026-08-07 name of the token log and is kept
# here on purpose: nothing writes it any more, but `uninstall` must still
# remove one from every machine that ran the older version.
INSTANCE_SUFFIXES = (".toml", ".env", "-session.json", "-usage.jsonl",
                     "-costs.jsonl", ".log")


def is_valid_instance_name(name):
    """The instance-name rule, enforced wherever a name arrives from
    outside (argv). Names become file paths and a launchd label, so a name
    carrying "/" or ".." could reach outside CONFIG_DIR — `uninstall`
    deletes files, so this is a guard, not a formality."""
    return bool(name) and re.fullmatch(r"[a-z0-9-]+", name) is not None


def list_instances(conf_dir=None):
    """Instance names in conf_dir, sorted — one per <name>.toml."""
    conf_dir = conf_dir or CONFIG_DIR
    try:
        entries = os.listdir(conf_dir)
    except OSError:
        return []
    return sorted(e[:-5] for e in entries if e.endswith(".toml"))


def instance_files(name, conf_dir=None):
    """Existing files belonging to instance <name>, in INSTANCE_SUFFIXES
    order. Missing ones are skipped, so this is also the "is it installed"
    answer."""
    conf_dir = conf_dir or CONFIG_DIR
    paths = [os.path.join(conf_dir, name + suffix)
             for suffix in INSTANCE_SUFFIXES]
    return [p for p in paths if os.path.exists(p)]


def plist_targets_repo(data, repo_dir):
    """True iff a launchd plist's ProgramArguments run a daemon out of
    repo_dir — how `update` finds the jobs THIS clone is responsible for
    and leaves other clones' instances alone. Unparseable plists are
    False, never an exception."""
    try:
        args = plistlib.loads(data).get("ProgramArguments") or []
    except Exception:
        return False
    root = os.path.realpath(repo_dir) + os.sep
    # Absolute args only: realpath() would resolve a bare flag like
    # "--config" against the CWD, which can land inside repo_dir and match
    # a plist that has nothing to do with this clone.
    return any(isinstance(a, str) and os.path.isabs(a)
               and os.path.realpath(a).startswith(root) for a in args)


MAX_FACE_CODEPOINTS = 8  # roomy enough for a family ZWJ sequence


def is_usable_face(text):
    """True if `text` can serve as an instance's face. Not an emoji
    validator — the bar is: it survives `emoji = "<text>"` in the generated
    TOML, and it isn't obviously typed instead of pasted.

    One rule does the work: no ASCII. Every emoji lives well above ASCII,
    while everything that would break the TOML (a quote, a backslash, a
    newline) and every accidental word ("hello", "x") is ASCII."""
    text = (text or "").strip()
    if not text or len(text) > MAX_FACE_CODEPOINTS:
        return False
    return all(ord(ch) > 127 for ch in text)


def resolve_instance_config(name):
    """`gidoon send <instance>` resolution: a bare instance name maps to
    ~/.config/gidoon/<name>.toml; anything carrying a path separator or a
    .toml suffix is already a path and passes through untouched."""
    if os.sep in name or name.endswith(".toml"):
        return name
    return os.path.join(CONFIG_DIR, f"{name}.toml")


def build_env():
    env = {"PATH": SANE_PATH}
    for key in ENV_PASSTHROUGH:
        if key in os.environ:
            env[key] = os.environ[key]
    env.setdefault("HOME", os.path.expanduser("~"))
    return env


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def log_line(path, line):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{now_iso()} {line}\n")


# ── config ──────────────────────────────────────────────────────────────────

class ConfigError(ValueError):
    pass


_KNOWN_KEYS = {"label", "env_file", "cwd", "permission_mode", "allowed_tools",
               "model", "emoji", "pre_turn_hook", "commands", "timeout_secs",
               "claude_bin", "system_prompt", "log_token_usage"}
_REQUIRED_KEYS = ("label", "env_file", "cwd")


def load_config(path, state_dir=None):
    """Load + validate an instance TOML. Returns a plain dict with defaults
    applied and derived state paths (session/usage/log) keyed by the config
    file's stem — ~/.config/gidoon/<name>-session.json etc.

    permission_mode ABSENT or empty string → None → the daemon passes no
    --permission-mode flag, so the turn inherits the user's own default
    mode. Same rule for allowed_tools (empty = no --allowedTools) and
    model (absent = no --model).

    log_token_usage is the exception that proves that rule: absent → True,
    because a standalone mouth is the only thing keeping books. A host
    project that records turns itself sets it false.
    """
    state_dir = state_dir or CONFIG_DIR
    name = os.path.splitext(os.path.basename(path))[0]
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"config not found: {path}")
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"config is not valid TOML: {exc}")

    unknown = set(raw) - _KNOWN_KEYS
    if unknown:
        raise ConfigError(f"unknown config keys: {', '.join(sorted(unknown))}")
    for key in _REQUIRED_KEYS:
        if not raw.get(key):
            raise ConfigError(f"config missing required key: {key}")

    allowed_tools = raw.get("allowed_tools", [])
    if not isinstance(allowed_tools, list) or \
            not all(isinstance(t, str) for t in allowed_tools):
        raise ConfigError("allowed_tools must be a list of strings")
    log_usage = raw.get("log_token_usage", True)
    if not isinstance(log_usage, bool):
        raise ConfigError("log_token_usage must be true or false")
    commands = raw.get("commands", ["new"])
    if not isinstance(commands, list):
        raise ConfigError("commands must be a list")
    for cmd in commands:
        if cmd not in COMMANDS:
            raise ConfigError(f"unknown command in config: {cmd!r} "
                              f"(known: {', '.join(sorted(COMMANDS))})")

    return {
        "name": name,
        "label": str(raw["label"]),
        "env_file": os.path.expanduser(raw["env_file"]),
        "cwd": os.path.expanduser(raw["cwd"]),
        "permission_mode": raw.get("permission_mode") or None,
        "allowed_tools": allowed_tools,
        "model": raw.get("model") or None,
        "emoji": raw.get("emoji", DEFAULT_EMOJI),
        "pre_turn_hook": raw.get("pre_turn_hook") or None,
        # Unlike the posture keys, absent does NOT mean "no flag" — the
        # identity text is a property of being a gidoon turn. Absent/empty
        # → the built-in default; a set value replaces it wholesale.
        "system_prompt": raw.get("system_prompt") or DEFAULT_SYSTEM_PROMPT,
        "commands": commands,
        "log_token_usage": log_usage,
        "timeout_secs": int(raw.get("timeout_secs", DEFAULT_TIMEOUT_SECS)),
        "claude_bin": os.path.expanduser(
            raw.get("claude_bin", DEFAULT_CLAUDE_BIN)),
        "session_path": os.path.join(state_dir, f"{name}-session.json"),
        "usage_path": os.path.join(state_dir, f"{name}-usage.jsonl"),
        "log_path": os.path.join(state_dir, f"{name}.log"),
    }


def read_env(path):
    """Parse KEY=VALUE lines; comments/blanks skipped. Values never logged."""
    env = {}
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


# ── session state ───────────────────────────────────────────────────────────

def load_session(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"offset": 0, "session_id": None}


def save_session(path, state):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, path)


# ── token usage log ─────────────────────────────────────────────────────────

def append_usage(path, result, duration_ms, exit_label):
    """One plain jsonl line per turn — a record, NOT enforcement. Spend
    control is the brain's job (a harness plugs its cap in via the
    pre_turn_hook); gidoon just keeps honest books.

    Tokens, never dollars: `claude -p` reports a total_cost_usd, but what a
    turn actually costs depends on the reader's plan (a subscription seat
    makes the number fiction), so this records the four token counts and
    leaves the pricing to whoever reads it."""
    usage = result.get("usage") or {}
    line = {
        "ts": now_iso(),
        "duration_ms": duration_ms,
        "num_turns": result.get("num_turns"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_creation_input_tokens":
            usage.get("cache_creation_input_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "exit": exit_label,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


# ── rendering (vendorable) ──────────────────────────────────────────────
# Lives in gidoon_render.py so a host project can vendor that file whole
# and render from the same source. Re-exported here so `import gidoon as
# core` is unaffected — same objects, never copies.
from gidoon_render import (  # noqa: F401  (re-exported)
    TOOL_EMOJI,
    FALLBACK_TOOL_EMOJI,
    TOOL_DISPLAY,
    MCP_PROVIDER_EMOJI,
    FALLBACK_MCP_EMOJI,
    humanize_tool_name,
    title_words,
    format_tool_label,
    collapse_tool_runs,
    count_suffix,
    collapse_tool_lines,
    extract_text,
    extract_tools,
)


# ── the turn ────────────────────────────────────────────────────────────────

def build_cmd(claude_bin, session_id=None, permission_mode=None,
              allowed_tools=None, model=None, system_prompt=None):
    """The claude argv for one turn. The prompt is NEVER here — it goes
    over stdin (see stream_claude). Optional postures are optional flags:
    absent config → absent flag → inherit the user's own defaults.
    system_prompt travels as argv (unlike the inbound message, it's
    owner-written config, not untrusted input)."""
    cmd = [claude_bin, "-p"]
    if session_id:
        cmd += ["--resume", session_id]
    cmd += ["--output-format", "stream-json", "--verbose"]
    if permission_mode:
        cmd += ["--permission-mode", permission_mode]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    if model:
        cmd += ["--model", model]
    if system_prompt:
        cmd += ["--append-system-prompt", system_prompt]
    return cmd


def stream_claude(prompt, session_id, on_event, cwd,
                  timeout_secs=DEFAULT_TIMEOUT_SECS, log=None,
                  claude_bin=None, permission_mode=None, allowed_tools=None,
                  model=None, system_prompt=None):
    """Run one claude turn; dispatch parsed events; return the result event
    (or {'subtype': 'timeout'} / {'subtype': 'crash'} /
    {'subtype': 'resume_failed'} markers).

    The prompt is delivered over stdin — never as an argv positional — so
    an untrusted message that happens to start with '-' can't be parsed
    as a CLI flag by the claude binary."""
    log = log or (lambda line: None)
    claude_bin = claude_bin or os.path.expanduser(DEFAULT_CLAUDE_BIN)
    cmd = build_cmd(claude_bin, session_id=session_id,
                    permission_mode=permission_mode,
                    allowed_tools=allowed_tools, model=model,
                    system_prompt=system_prompt)
    # Binary pipes + raw os.read, deliberately: mixing select() with a
    # buffered TextIOWrapper is a classic wedge — readline() buffers ahead,
    # so the result line can sit in Python's buffer while select() waits on
    # an fd that will never signal again. Raw reads mean select and the
    # data can never disagree.
    proc = subprocess.Popen(cmd, cwd=cwd, env=build_env(),
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)
    try:
        proc.stdin.write(prompt.encode("utf-8"))
        proc.stdin.close()
    except BrokenPipeError:
        pass
    deadline = time.time() + timeout_secs
    result = None
    buf = b""
    # Rolling tail of stderr — length only ever feeds the crash log line's
    # char count; the content itself is never logged.
    stderr_tail = ""
    try:
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError
            ready, _, _ = select.select([proc.stdout, proc.stderr], [], [],
                                        min(remaining, 5))
            if proc.stderr in ready:
                try:
                    chunk = os.read(proc.stderr.fileno(), 8192).decode(
                        "utf-8", "replace")
                except OSError:
                    chunk = ""
                if chunk:
                    stderr_tail = (stderr_tail + chunk)[-200:]
            if proc.stdout in ready:
                try:
                    chunk = os.read(proc.stdout.fileno(), 65536)
                except OSError:
                    chunk = b""
                if not chunk:
                    break                      # EOF
                buf += chunk
                for raw in buf.split(b"\n")[:-1]:
                    try:
                        event = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if event.get("type") == "result":
                        result = event
                    on_event(event)
                buf = buf.rsplit(b"\n", 1)[-1] if b"\n" in buf else buf
                if result is not None:
                    # The result event IS the turn's end — never wait for
                    # EOF. Under launchd claude has been seen lingering
                    # after completing (open API sockets keep node alive).
                    # The finally block below reaps or kills the leftover.
                    break
            elif not ready:
                if proc.poll() is not None:
                    break
                continue
    except TimeoutError:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.communicate()
        log(f"turn timeout — stderr tail: {stderr_tail!r}")
        return {"subtype": "timeout"}
    finally:
        if proc.poll() is None:
            try:
                # Post-result we already have the answer — give a lingerer
                # 3s of grace, not 15.
                proc.wait(timeout=3 if result is not None else 15)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
    # Drain remaining stderr regardless of outcome — the resume-failure
    # signature can arrive alongside a clean-looking empty result event,
    # not only on a no-result crash.
    try:
        extra = os.read(proc.stderr.fileno(), 8192).decode("utf-8", "replace")
    except OSError:
        extra = ""
    if extra:
        stderr_tail = (stderr_tail + extra)[-200:]
    for pipe in (proc.stdout, proc.stderr):
        try:
            pipe.close()
        except OSError:
            pass
    # A resumed session that no longer exists (transcript moved/expired/
    # deleted, or project identity changed) fails with "No conversation
    # found". Signal it distinctly so the caller can drop the dead
    # session_id and retry fresh, rather than wedging every future turn.
    if session_id and "No conversation found" in stderr_tail:
        log("turn resume failed — session not found; will retry fresh")
        return {"subtype": "resume_failed"}
    if result is None:
        log(f"turn crash — no result event; stderr {len(stderr_tail)} chars")
        return {"subtype": "crash"}
    return result
