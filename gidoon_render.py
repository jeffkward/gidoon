"""gidoon_render — how a turn's activity is rendered. Pure, and vendorable.

This module is meant to be COPIED into a host project — a project that
embeds a gidoon mouth and has its own surface (a web UI, a TUI) showing the
same turn. Keeping a pinned copy and importing it there means both surfaces
render an identical tool checklist from one source instead of two that
slowly disagree.

That means three rules, each pinned by tests/test_render_module.py:

  · imports `re` and `json` and NOTHING else — both stdlib, no I/O, no
    config, no policy. A new import here becomes a new dependency in every
    project that vendored it; these two add none.
  · imports standalone; gidoon.py must not be needed.
  · gidoon.py re-exports every name below, so `import gidoon as core` keeps
    working and both halves stay the same objects.

Named for where it lives AFTER being vendored, not where it was born: a
plain `render.py` dropped onto a host project's sys.path is a collision
waiting to happen.
"""
import json
import re


def extract_text(event):
    """Concatenated text blocks from an assistant event ('' if none)."""
    if event.get("type") != "assistant":
        return ""
    content = (event.get("message") or {}).get("content") or []
    return "".join(b.get("text", "") for b in content
                   if isinstance(b, dict) and b.get("type") == "text")


def extract_tools(event):
    """(name, input) pairs from an assistant event's tool_use blocks. The
    complete `assistant` event carrying a tool_use block arrives right as
    the model commits to the call — before the tool actually runs, so no
    --include-partial-messages is needed for tools to appear live."""
    if event.get("type") != "assistant":
        return []
    content = (event.get("message") or {}).get("content") or []
    return [(b.get("name", "?"), b.get("input") or {}) for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use"]


TOOL_EMOJI = {
    "WebSearch": "🔎",
    "WebFetch": "👀",
    "Read": "📖",
    "Write": "✍️",
    "Edit": "✏️",
    "Bash": "💻",
    "Grep": "🔍",
    "Glob": "🗂",
    "Task": "🤖",
    "TodoWrite": "📝",
    "Skill": "🛠",
    "ToolSearch": "🧰",
    "NotebookEdit": "📓",
    "AskUserQuestion": "❓",
    "EnterPlanMode": "🗺",
    "ExitPlanMode": "🗺",
}
FALLBACK_TOOL_EMOJI = "⚙️"

# Display overrides for built-in tools whose CamelCase name humanizes badly
# ("Todo Write") — the override says what the tool is doing instead.
TOOL_DISPLAY = {
    "TodoWrite": "Updating Checklist",
    "Task": "Handed Task to Sub-agent",
    "AskUserQuestion": "Asking You a Question",
    "Glob": "Finding Files",
    "Grep": "Searching Text",
}

# Keyed on the lowercased humanized provider (after the claude_ai_ strip and
# underscore -> space), so "claude_ai_Google_Calendar" and a bare
# "google_calendar" server both match "google calendar".
MCP_PROVIDER_EMOJI = {
    "google calendar": "📅",
    "gmail": "📧",
    "google drive": "📁",
    "slack": "#️⃣",
    "atlassian": "📋",
    "github": "🐙",
    "notion": "📔",
    "figma": "🎨",
    "playwright": "🌐",
    "telegram": "💬",
    "context7": "📚",
}
FALLBACK_MCP_EMOJI = "🔌"


def humanize_tool_name(name):
    """CamelCase -> spaced Title: "WebSearch" -> "Web Search". A single-word
    name (e.g. "Bash") passes through unchanged."""
    return re.sub(r"(?<!^)(?=[A-Z])", " ", name)


def title_words(text):
    """Capitalize each all-lowercase word ("list events" -> "List Events");
    words that already carry a capital (camelCase methods, acronyms, proper
    names like "Gmail") pass through untouched so title-casing never mangles
    them."""
    return " ".join(w.capitalize() if w.islower() else w
                    for w in text.split(" "))


def format_tool_label(name, input_data=None):
    """(emoji, display_name) for a tool_use block.

    - Skill tool -> ("🛠", "Skill: <name>") — input field confirmed via a
      live spike in the source to be plain `input.skill`; input.command /
      input.name are fallbacks in case that shape ever changes.
    - MCP tools (name starts "mcp__") -> ("<provider emoji>",
      "<Provider>: <Method>"), splitting on "__"; a leading "claude_ai_"
      provider prefix is stripped, underscores become spaces, and both
      halves are title-cased via title_words. The emoji comes from
      MCP_PROVIDER_EMOJI (matched on the lowercased provider) or 🔌.
      Falls back to (⚙️, raw name) if the shape doesn't have at least
      3 parts.
    - Everything else -> (emoji from TOOL_EMOJI or ⚙️, TOOL_DISPLAY
      override or humanized name).
    """
    input_data = input_data or {}
    if name == "Skill":
        skill = input_data.get("skill") or input_data.get("command") \
            or input_data.get("name") or "?"
        return ("🛠", f"Skill: {skill}")
    if isinstance(name, str) and name.startswith("mcp__"):
        parts = name.split("__")
        if len(parts) >= 3:
            provider = parts[-2]
            method = parts[-1]
            if provider.startswith("claude_ai_"):
                provider = provider[len("claude_ai_"):]
            provider = title_words(provider.replace("_", " "))
            method = title_words(method.replace("_", " "))
            emoji = MCP_PROVIDER_EMOJI.get(provider.lower(),
                                           FALLBACK_MCP_EMOJI)
            return (emoji, f"{provider}: {method}")
        return (FALLBACK_TOOL_EMOJI, name)
    display = TOOL_DISPLAY.get(name) or humanize_tool_name(name)
    return (TOOL_EMOJI.get(name, FALLBACK_TOOL_EMOJI), display)


def collapse_tool_runs(completed):
    """Merge consecutive identical (emoji, display) tools into
    (emoji, display, count) runs — so a tool called N times in a row is one
    line, not N duplicates. Consecutive-only: a repeat separated by a
    different tool stays its own run, preserving the real sequence."""
    runs = []
    for emoji, display in completed:
        if runs and runs[-1][0] == emoji and runs[-1][1] == display:
            runs[-1] = (emoji, display, runs[-1][2] + 1)
        else:
            runs.append((emoji, display, 1))
    return runs


def count_suffix(count):
    return f" ×{count}" if count > 1 else ""


def collapse_tool_lines(completed):
    """Display lines for a completed-tools sequence, exactly as the status
    message renders them: one line per collapsed run, "×N" suffix on
    repeats. ["💻 Bash ×2", "🧰 Tool Search", …]. Pure."""
    return [f"{emoji} {display}{count_suffix(count)}"
            for emoji, display, count in collapse_tool_runs(completed)]


# ── choices: a reply may end with a fenced ```choices block ─────────────────
# A JSON list of short strings. A host project can draw them as buttons;
# Telegram draws a numbered list (the owner replies with a number or in
# their own words). An entry starting with "/" is a command the owner must
# send as typed, so it is never numbered. Render-only: nothing here acts.
CHOICES_FENCE = re.compile(r"(?:^|\n)\s*```choices[ \t]*\n(.*?)\n```[ \t]*\Z",
                           re.S)


def split_choices(text):
    """(the text without its choices block, the choices) — or (text, [])
    when there is no well-formed block at the very end."""
    text = text or ""
    m = CHOICES_FENCE.search(text)
    if not m:
        return text, []
    try:
        choices = json.loads(m.group(1))
    except ValueError:
        return text, []
    if not isinstance(choices, list) or not all(
            isinstance(c, str) and c.strip() for c in choices):
        return text, []
    return text[:m.start()].rstrip(), [c.strip() for c in choices]


def render_choices(text):
    """Telegram's rendering: numbered plain entries, then the commands
    on one line."""
    body, choices = split_choices(text)
    if not choices:
        return text
    plain = [c for c in choices if not c.startswith("/")]
    commands = [c for c in choices if c.startswith("/")]
    lines = []
    if plain:
        lines += [f"{i + 1}. {c}" for i, c in enumerate(plain)]
        lines.append("Reply with a number, or in your own words.")
    if commands:
        lines.append("Or send: " + " · ".join(commands))
    return (body + "\n\n" if body else "") + "\n".join(lines)
