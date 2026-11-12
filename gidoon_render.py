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
CHOICES_FENCE = re.compile(
    r"(?:^|\n)\s*```choices[ \t]*\r?\n(.*?)\r?\n```\s*\Z", re.S)


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
    # A choice is one line: any whitespace inside it (an escaped newline
    # included) collapses to a single space.
    return text[:m.start()].rstrip(), [" ".join(c.split()) for c in choices]


def render_choices(text):
    """Telegram's rendering: numbered plain entries, then the commands
    on one line."""
    body, choices = split_choices(text)
    if not choices:
        return body
    plain = [c for c in choices if not c.startswith("/")]
    commands = [c for c in choices if c.startswith("/")]
    lines = []
    if plain:
        lines += [f"{i + 1}. {c}" for i, c in enumerate(plain)]
        lines.append("Reply with a number, or in your own words.")
    if commands:
        lines.append("Or send: " + " · ".join(commands))
    return (body + "\n\n" if body else "") + "\n".join(lines)


# ── Buttons ─────────────────────────────────────────────────────────────────
#
# On Telegram a choices block becomes inline buttons, one per row. A
# button's text goes through button_label — the one place a button's text
# is styled, so every button in a host looks alike. The tap brings back the
# option's OWN words (not the styled label), so a host matching replies
# against the options it offered sees exactly what a typed reply would be.

# Mid-label words that stay lowercase. Not on/off/up/in/out: on a button
# they are nearly always a verb's particle ("Turn On", "Log In").
_SMALL_WORDS = {"a", "an", "the", "or", "nor", "but", "of", "to", "for",
                "by", "at", "as", "via", "vs"}

TAP_LINE = "_Tap one, or reply in your own words._"

# A phone shows about this many characters on a one-per-row button before
# cutting it with "…" — past it, the options are listed in full as well.
BUTTON_FULL_TEXT = 40

# Telegram's own limit on a button's callback_data, in bytes.
CALLBACK_MAX = 64


def _cap(word):
    """Capitalise a word's first letter — unless it carries capitals of its
    own (URL, iPhone), a digit (v2), or doesn't start with a letter (/clear,
    ~/path): those are already exactly what someone meant."""
    if not word[:1].isalpha() or any(ch.isdigit() for ch in word) \
            or any(ch.isupper() for ch in word[1:]):
        return word
    return "-".join(p[:1].upper() + p[1:] for p in word.split("-"))


def button_label(text):
    """A button's text, mostly title case: every word capitalised except
    small words mid-label, and a standalone "and" as "&"
    ("Save and send" → "Save & Send")."""
    words = (text or "").split()
    last = len(words) - 1
    out = []
    for i, word in enumerate(words):
        bare = word.lower().strip(",.;:!?")
        middle = 0 < i < last
        if middle and word.lower() == "and":
            out.append("&")
        elif middle and bare in _SMALL_WORDS and \
                not any(ch.isupper() for ch in word[1:]):
            out.append(word.lower())
        else:
            out.append(_cap(word))
    return " ".join(out)


def _callback_data(choice, index):
    data = "c:" + choice
    return data if len(data.encode()) <= CALLBACK_MAX else f"i:{index}"


def telegram_choices(text):
    """(the text to send, the inline keyboard or None). Plain options become
    buttons; commands stay a line of text (a command is something to send,
    not an answer)."""
    body, choices = split_choices(text)
    if not choices:
        return body, None
    plain = [c for c in choices if not c.startswith("/")]
    commands = [c for c in choices if c.startswith("/")]
    lines, keyboard = [], None
    if plain:
        keyboard = [[{"text": button_label(c),
                      "callback_data": _callback_data(c, i)}]
                    for i, c in enumerate(plain)]
        if any(len(button_label(c)) > BUTTON_FULL_TEXT for c in plain):
            lines += [f"- {c}" for c in plain]
        lines.append(TAP_LINE)
    if commands:
        lines.append("Or send: " + " · ".join(commands))
    return (body + "\n\n" if body else "") + "\n".join(lines), keyboard


def choice_from_callback(data, message):
    """The words a tapped button answers with, or None for a tap that isn't
    one of ours. `c:<words>` carries them whole; `i:<n>` (options too long
    for callback_data) falls back to that button's text on the message."""
    if not isinstance(data, str):
        return None
    if data.startswith("c:"):
        return data[2:] or None
    if data.startswith("i:"):
        try:
            n = int(data[2:])
        except ValueError:
            return None
        rows = ((message or {}).get("reply_markup") or {}) \
            .get("inline_keyboard") or []
        if 0 <= n < len(rows) and rows[n]:
            return rows[n][0].get("text") or None
    return None


# ── Markdown → Telegram HTML ────────────────────────────────────────────────
#
# Agents write Markdown; Telegram shows it raw unless a message carries a
# parse_mode. HTML, not MarkdownV2: HTML needs only & < > escaped, where
# MarkdownV2 needs eighteen characters escaped everywhere except where it
# needs different ones — one miss and the whole message is rejected.
#
# Best effort, never lossy: whatever can't be paired (a lone **, a fence
# that never closes, a half-written link mid-stream) stays literal text,
# and the output is always HTML Telegram accepts — balanced, properly
# nested, nothing inside <pre>/<code>, no nested blockquote. The caller
# still retries plain on a parse error, but that is the backstop, not the
# plan.

_FENCE_OPEN = re.compile(r"^\s*```([\w+#.-]*)\s*$")
_FENCE_CLOSE = re.compile(r"^\s*```\s*$")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_BULLET = re.compile(r"^(\s*)[-*]\s+(?=\S)")
_DIVIDER = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")   # ---, * * *
_QUOTE = re.compile(r"^>\s?(.*)$")
# `>+` lines: an expandable quote — Telegram shows its first lines and
# opens the rest on a tap. Not Markdown; a plain `>` quote is unchanged.
_EXPAND = re.compile(r"^>\+\s?(.*)$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")

_CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`)(?=\S)(.+?)(?<=\S)(?<!`)\1(?!`)")
_LINK = re.compile(r"\[([^\]\n]+)\]\(((?:https?|tg)://[^\s)]+)\)")
_BARE_URL = re.compile(r"(?:https?|tg)://[^\s<>]+")
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
# A closing marker followed by `.word` is a file name (`__init__.py`).
_BOLD_STAR = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*(?!\*)")
_BOLD_UNDER = re.compile(r"(?<![\w_])__(?=\S)(.+?)(?<=\S)__(?![\w_]|\.\w)")
_ITAL_STAR = re.compile(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])")
_ITAL_UNDER = re.compile(
    r"(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_]|\.\w)")
_SLOT = re.compile("\x00(\\d+)\x00")


def _esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inline(text, bold=True):
    """One line of Markdown → HTML. Code spans, links and bare URLs are
    lifted out first as opaque slots, so no later rule can see inside
    them (snake_case in a URL, *stars* in code). Each emphasis rule then
    turns its matches into slots too, so a later rule's match can wrap a
    finished tag but never cut through one — that is what keeps crossed
    markers (`**a *b** c*`) from producing mis-nested HTML. bold=False
    (inside a heading, already bold) unwraps bold markers instead."""
    slots = []

    def slot(html_):
        slots.append(html_)
        return f"\x00{len(slots) - 1}\x00"

    text = text.replace("\x00", "")
    text = _CODE_SPAN.sub(lambda m: slot(f"<code>{_esc(m.group(2))}</code>"),
                          text)

    def link(m):
        href = _esc(m.group(2)).replace('"', "&quot;")
        return slot(f'<a href="{href}">{_esc(m.group(1))}</a>')
    text = _LINK.sub(link, text)
    text = _BARE_URL.sub(lambda m: slot(_esc(m.group(0))), text)
    text = _esc(text)

    b = "b" if bold else None
    rules = ((_BOLD_STAR, b), (_BOLD_UNDER, b), (_STRIKE, "s"),
             (_ITAL_STAR, "i"), (_ITAL_UNDER, "i"))

    def emphasis(text, rules):
        # A match's inside gets the rules AFTER this one — bold may hold
        # italic, never the reverse — and then becomes one opaque slot.
        for k, (pat, tag) in enumerate(rules):
            def wrap(m, tag=tag, rest=rules[k + 1:]):
                inner = emphasis(m.group(1), rest)
                return slot(f"<{tag}>{inner}</{tag}>" if tag else inner)
            text = pat.sub(wrap, text)
        return text
    text = emphasis(text, rules)

    while _SLOT.search(text):
        text = _SLOT.sub(lambda m: slots[int(m.group(1))], text)
    return text


def to_telegram_html(text):
    """Markdown → Telegram HTML (parse_mode=HTML). Pure, best effort."""
    lines = (text or "").split("\n")
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        fence = _FENCE_OPEN.match(line)
        if fence:
            end = next((j for j in range(i + 1, len(lines))
                        if _FENCE_CLOSE.match(lines[j])), None)
            if end is not None:
                body = _esc("\n".join(lines[i + 1:end]))
                lang = fence.group(1)
                out.append(f'<pre><code class="language-{lang}">{body}'
                           f"</code></pre>" if lang else f"<pre>{body}</pre>")
                i = end + 1
                continue
            out.append(_inline(line))     # a fence that never closes: text
            i += 1
            continue
        if _TABLE_ROW.match(line):
            j = i
            while j < len(lines) and _TABLE_ROW.match(lines[j]):
                j += 1
            if j - i >= 2:
                out.append("<pre>" + _esc("\n".join(lines[i:j])) + "</pre>")
                i = j
                continue
        if _EXPAND.match(line):
            j = i
            while j < len(lines) and _EXPAND.match(lines[j]):
                j += 1
            body = [_EXPAND.match(l).group(1) for l in lines[i:j]]
            if any(b.strip() for b in body):
                out.append("<blockquote expandable>"
                           + "\n".join(_inline(b) for b in body)
                           + "</blockquote>")
            else:
                out.extend(_esc(l) for l in lines[i:j])
            i = j
            continue
        if _QUOTE.match(line):
            j = i
            while j < len(lines) and _QUOTE.match(lines[j]) \
                    and not _EXPAND.match(lines[j]):
                j += 1
            body = [_QUOTE.match(l).group(1) for l in lines[i:j]]
            if any(b.strip() for b in body):
                out.append("<blockquote>"
                           + "\n".join(_inline(b) for b in body)
                           + "</blockquote>")
            else:
                out.extend(_esc(l) for l in lines[i:j])
            i = j
            continue
        heading = _HEADING.match(line)
        if heading:
            out.append(f"<b>{_inline(heading.group(1), bold=False)}</b>")
        elif _DIVIDER.match(line):
            out.append(_esc(line))
        else:
            out.append(_inline(_BULLET.sub(r"\1• ", line)))
        i += 1
    return "\n".join(out)


def _md_blocks(text):
    """Paragraphs (split on blank lines), with each closed fenced block
    kept whole even when it holds blank lines."""
    lines, blocks, cur, i = text.split("\n"), [], [], 0
    while i < len(lines):
        line = lines[i]
        if _FENCE_OPEN.match(line):
            end = next((j for j in range(i + 1, len(lines))
                        if _FENCE_CLOSE.match(lines[j])), None)
            if end is not None:
                if cur:
                    blocks.append("\n".join(cur))
                    cur = []
                blocks.append("\n".join(lines[i:end + 1]))
                i = end + 1
                continue
        if line.strip():
            cur.append(line)
        elif cur:
            blocks.append("\n".join(cur))
            cur = []
        i += 1
    if cur:
        blocks.append("\n".join(cur))
    return blocks


def split_markdown(text, limit=4096):
    """Markdown → parts that each fit `limit` both as Markdown AND once
    rendered (escaping grows text: & is five characters in HTML). Splits
    between paragraphs; never inside a fenced block — one too long to send
    whole is cut between lines and each piece re-fenced; a single line too
    long for any part is hard-cut, at a space when there is one."""
    def fits(s):
        return len(s) <= limit and len(to_telegram_html(s)) <= limit

    def hard_cut(line, prefix="", suffix=""):
        pieces = []
        while line:
            n = min(len(line), limit)
            while n > 1 and not fits(prefix + line[:n] + suffix):
                n = max(1, n * 3 // 4)
            space = line.rfind(" ", 0, n)
            if n < len(line) and space > n // 2:
                n = space + 1
            pieces.append(prefix + line[:n] + suffix)
            line = line[n:]
        return pieces

    def pack(lines, prefix="", suffix=""):
        pieces, cur = [], None
        for line in lines:
            cand = line if cur is None else cur + "\n" + line
            if fits(prefix + cand + suffix):
                cur = cand
                continue
            if cur is not None:
                pieces.append(prefix + cur + suffix)
            if fits(prefix + line + suffix):
                cur = line
            else:
                pieces.extend(hard_cut(line, prefix, suffix))
                cur = None
        if cur is not None:
            pieces.append(prefix + cur + suffix)
        return pieces

    parts, cur = [], None
    for block in _md_blocks(text or ""):
        cand = block if cur is None else cur + "\n\n" + block
        if fits(cand):
            cur = cand
            continue
        if cur is not None:
            parts.append(cur)
            cur = None
        if fits(block):
            cur = block
            continue
        blines = block.split("\n")
        if _FENCE_OPEN.match(blines[0]) and len(blines) >= 2 \
                and _FENCE_CLOSE.match(blines[-1]):
            parts.extend(pack(blines[1:-1], blines[0].strip() + "\n",
                              "\n```"))
        else:
            parts.extend(pack(blines))
    if cur is not None:
        parts.append(cur)
    return parts or [""]
