"""Markdown → Telegram HTML (parse_mode=HTML), and the splitter that keeps
each rendered part under Telegram's 4096-character limit.

The converter is best effort by design: anything it can't pair stays
literal text, and its output must always be HTML Telegram accepts. The
`assertValidTelegramHtml` check below is the stand-in for Telegram's own
parser — balanced, properly nested, only the tags we emit, nothing inside
<pre>/<code>, no nested blockquote."""
import html
import re
import unittest
from html.parser import HTMLParser

import helpers  # noqa: F401  (sys.path setup)
import gidoon_render as r

T = r.to_telegram_html

ALLOWED = {"b", "i", "s", "code", "pre", "a", "blockquote"}


class _Checker(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED:
            self.errors.append(f"unexpected <{tag}>")
        if any(t in ("code",) for t in self.stack):
            self.errors.append(f"<{tag}> inside <code>")
        if "pre" in self.stack and tag != "code":
            self.errors.append(f"<{tag}> inside <pre>")
        if tag == "blockquote" and "blockquote" in self.stack:
            self.errors.append("nested blockquote")
        if tag == "a" and not dict(attrs).get("href"):
            self.errors.append("<a> without href")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"misnested </{tag}> (open: {self.stack})")
            return
        self.stack.pop()

    def handle_data(self, data):
        if "<" in data or ">" in data:
            self.errors.append(f"raw angle bracket in {data!r}")


def check_html(s):
    # A bare & that isn't an entity is the other way to break the parser.
    bad_amp = re.search(r"&(?!(amp|lt|gt|quot);)", s)
    c = _Checker()
    c.feed(s)
    c.close()
    errors = c.errors + ([f"unclosed {c.stack}"] if c.stack else [])
    if bad_amp:
        errors.append(f"bare & at {bad_amp.start()}")
    return errors


class Base(unittest.TestCase):
    def assertValidTelegramHtml(self, s):
        self.assertEqual(check_html(s), [], s)

    def conv(self, text, expected):
        out = T(text)
        self.assertEqual(out, expected)
        self.assertValidTelegramHtml(out)


class Escaping(Base):
    def test_angle_brackets_and_ampersand_are_escaped(self):
        self.conv("a < b && c > d", "a &lt; b &amp;&amp; c &gt; d")

    def test_html_in_the_source_is_shown_not_obeyed(self):
        self.conv("<b>not bold</b>", "&lt;b&gt;not bold&lt;/b&gt;")

    def test_plain_text_is_unchanged(self):
        self.conv("Just a sentence. 3 + 4 = 7", "Just a sentence. 3 + 4 = 7")

    def test_empty(self):
        self.conv("", "")


class Inline(Base):
    def test_bold_both_spellings(self):
        self.conv("**a** and __b__", "<b>a</b> and <b>b</b>")

    def test_italic_both_spellings(self):
        self.conv("*a* and _b_", "<i>a</i> and <i>b</i>")

    def test_strike(self):
        self.conv("~~gone~~", "<s>gone</s>")

    def test_inline_code_is_escaped_and_untouched(self):
        self.conv("run `a_b *c* <d>` now",
                  "run <code>a_b *c* &lt;d&gt;</code> now")

    def test_bold_holding_italic_nests_properly(self):
        self.conv("**bold *and italic***",
                  "<b>bold <i>and italic</i></b>")

    def test_crossed_markers_never_cross_tags(self):
        out = T("**a *b** c*")
        self.assertValidTelegramHtml(out)

    def test_bold_holding_code(self):
        self.conv("**see `x`**", "<b>see <code>x</code></b>")


class WordBoundaries(Base):
    def test_snake_case_stays_literal(self):
        self.conv("call get_user_name now", "call get_user_name now")

    def test_arithmetic_stays_literal(self):
        self.conv("2*3*4 = 24", "2*3*4 = 24")

    def test_file_names_stay_literal(self):
        self.conv("edit my_file_name.py and __init__.py",
                  "edit my_file_name.py and __init__.py")

    def test_a_spaced_star_is_not_italic(self):
        self.conv("a * b * c", "a * b * c")


class Links(Base):
    def test_https_link(self):
        self.conv("see [the docs](https://example.com/a)",
                  'see <a href="https://example.com/a">the docs</a>')

    def test_tg_link(self):
        self.conv("[me](tg://user?id=1)", '<a href="tg://user?id=1">me</a>')

    def test_url_with_ampersand_and_underscores(self):
        self.conv("[q](https://x.com/a_b_c?x=1&y=_z_)",
                  '<a href="https://x.com/a_b_c?x=1&amp;y=_z_">q</a>')

    def test_other_schemes_stay_literal(self):
        self.conv("[x](javascript:alert(1))", "[x](javascript:alert(1))")
        self.conv("[x](../people/a.md)", "[x](../people/a.md)")

    def test_bare_url_underscores_are_not_italic(self):
        self.conv("go to https://x.com/_private_/a_b",
                  "go to https://x.com/_private_/a_b")

    def test_link_text_is_escaped(self):
        self.conv("[a < b](https://e.com)", '<a href="https://e.com">a &lt; b</a>')

    def test_quote_in_url_cannot_break_the_attribute(self):
        out = T('[x](https://e.com/"onclick)')
        self.assertValidTelegramHtml(out)
        self.assertNotIn('"onclick"', out)


class Blocks(Base):
    def test_fenced_block_with_language(self):
        self.conv("```python\nx = a_b * 2  # <ok>\n```",
                  '<pre><code class="language-python">'
                  "x = a_b * 2  # &lt;ok&gt;</code></pre>")

    def test_fenced_block_without_language(self):
        self.conv("```\n**not bold**\n```", "<pre>**not bold**</pre>")

    def test_half_open_fence_stays_literal(self):
        self.conv("```python\nprint(1)", "```python\nprint(1)")

    def test_blockquote_run_is_one_quote(self):
        self.conv("> one\n> **two**\nafter",
                  "<blockquote>one\n<b>two</b></blockquote>\nafter")

    def test_two_quote_runs_are_two_quotes(self):
        self.conv("> a\n\n> b",
                  "<blockquote>a</blockquote>\n\n<blockquote>b</blockquote>")

    def test_a_plus_quote_run_is_one_expandable_quote(self):
        # Telegram shows the first lines and opens the rest on a tap.
        self.conv(">+ one\n>+\n>+ **two**\nafter",
                  "<blockquote expandable>one\n\n<b>two</b></blockquote>"
                  "\nafter")

    def test_expandable_and_plain_quotes_stay_separate(self):
        self.conv("> plain\n>+ more",
                  "<blockquote>plain</blockquote>\n"
                  "<blockquote expandable>more</blockquote>")

    def test_an_empty_expandable_run_stays_literal(self):
        self.conv(">+\n>+", "&gt;+\n&gt;+")

    def test_an_expandable_quote_is_valid_telegram_html(self):
        self.assertValidTelegramHtml(T(">+ a < b & *c*\n>+ `d`"))

    def test_splitting_keeps_an_expandable_run_with_blank_lines_whole(self):
        text = "intro\n\n" + "\n>+\n".join(f">+ item {k}" for k in range(5))
        parts = r.split_markdown(text, 4096)
        self.assertEqual(len(parts), 1)
        self.assertEqual(T(parts[0]).count("<blockquote expandable>"), 1)

    def test_headings_become_bold(self):
        self.conv("# Title\n### Sub *x*",
                  "<b>Title</b>\n<b>Sub <i>x</i></b>")

    def test_bold_inside_a_heading_does_not_double_wrap(self):
        out = T("## **Already bold**")
        self.assertValidTelegramHtml(out)
        self.assertEqual(out, "<b>Already bold</b>")

    def test_hashtag_is_not_a_heading(self):
        self.conv("#general is busy", "#general is busy")

    def test_bullets(self):
        self.conv("- one\n* two\n  - nested",
                  "• one\n• two\n  • nested")

    def test_a_divider_is_not_a_bullet(self):
        self.conv("* * *\n- - -\n---", "* * *\n- - -\n---")

    def test_numbered_lists_unchanged(self):
        self.conv("1. one\n2. two", "1. one\n2. two")

    def test_table_goes_in_pre(self):
        table = "| a | b |\n|---|---|\n| 1 | x_y |"
        self.conv("intro\n" + table + "\nafter",
                  "intro\n<pre>" + table + "</pre>\nafter")

    def test_a_lone_pipe_line_is_not_a_table(self):
        self.conv("| just one |", "| just one |")


class Unbalanced(Base):
    def test_lone_markers_stay_literal(self):
        for s in ("a ** b", "**open", "~~half", "`tick", "_lead", "end*",
                  "[text](https://e.com", "> "):
            with self.subTest(s=s):
                out = T(s)
                self.assertValidTelegramHtml(out)
                self.assertEqual(html.unescape(out), s)

    def test_mid_stream_fragments_are_always_valid(self):
        """Every prefix of a realistic reply renders as valid HTML —
        the property a half-written message depends on."""
        reply = ("# Plan\n\n**Bold** with `code`, _it_ and a "
                 "[link](https://e.com/a_b?x=1&y=2).\n\n- one\n- two\n\n"
                 "```py\nprint('<x>')\n```\n> quoted *thing*\n"
                 "| a | b |\n|---|---|\n| 1 | 2 |\n")
        for i in range(len(reply) + 1):
            out = T(reply[:i])
            self.assertEqual(check_html(out), [], (i, reply[:i], out))

    def test_nul_bytes_cannot_forge_a_placeholder(self):
        out = T("a \x000\x00 b **c**")
        self.assertValidTelegramHtml(out)


class Choices(Base):
    def test_rendered_choices_survive_conversion_unchanged(self):
        text = ('Which one?\n\n```choices\n'
                '["Keep the old_name", "Use both (2 & 3)", "/set_mode", "/clear"]\n```')
        rendered = r.render_choices(text)
        self.assertIn("1. Keep the old_name", rendered)
        self.assertEqual(html.unescape(T(rendered)), rendered)
        self.assertNotIn("<", T(rendered))  # no tags: nothing reformatted

    def test_numbered_choice_list_is_untouched(self):
        rendered = r.render_choices('Pick:\n```choices\n["a", "b"]\n```')
        self.assertEqual(T(rendered), rendered)


class Split(Base):
    LIMIT = 4096

    def parts(self, text, limit=LIMIT):
        ps = r.split_markdown(text, limit)
        for p in ps:
            self.assertLessEqual(len(p), limit)
            self.assertLessEqual(len(T(p)), limit)
            self.assertValidTelegramHtml(T(p))
        return ps

    def test_short_text_is_one_part(self):
        self.assertEqual(self.parts("hello"), ["hello"])

    def test_splits_at_paragraph_boundaries(self):
        paras = [("p%d " % i) + "word " * 150 for i in range(12)]
        ps = self.parts("\n\n".join(p.strip() for p in paras))
        self.assertGreater(len(ps), 1)
        for p in ps:
            self.assertTrue(p.startswith("p"), p[:20])

    def test_a_code_block_across_the_split_is_closed_and_reopened(self):
        code = "\n".join(f"line_{i} = {i}  # <{i}>" for i in range(400))
        text = "Intro **bold**.\n\n```python\n" + code + "\n```\n\nOutro."
        ps = self.parts(text)
        self.assertGreater(len(ps), 2)
        joined = "".join(T(p) for p in ps)
        # every code line survives exactly once, inside a code block
        for i in range(400):
            self.assertEqual(joined.count(f"line_{i} = {i}  # &lt;{i}&gt;"), 1)
        fenced = [p for p in ps if "line_" in p]
        for p in fenced:
            self.assertTrue(p.startswith("```python\n"), p[:30])
            self.assertTrue(p.endswith("\n```"), p[-30:])

    def test_one_huge_line_is_hard_split(self):
        ps = self.parts("x" * 10000)
        self.assertEqual("".join(ps), "x" * 10000)

    def test_escaping_growth_is_accounted_for(self):
        """'&' renders as '&amp;' — 5x. A part that fits as Markdown can
        overflow once rendered; the splitter has to measure the HTML."""
        ps = self.parts("&" * 5000)
        self.assertEqual("".join(ps), "&" * 5000)

    def test_small_limit(self):
        self.parts("**a** b\n\n" * 50, limit=64)


if __name__ == "__main__":
    unittest.main()
