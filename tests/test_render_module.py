"""gidoon_render — the pure, vendorable half of the engine.

This module exists to be COPIED into a host project's import namespace, so
that project's own surface renders a turn exactly the way the daemon does.
That imposes rules nothing else in gidoon has to obey:

  · it may import `re` and `json` and nothing else — no I/O, no config, no policy,
    nothing that could reach out of the host project's process
  · it must import standalone, without gidoon.py
  · gidoon.py re-exports every name, so `import gidoon as core` is
    unaffected — and the re-exports must be the SAME OBJECTS, not copies,
    or the two halves can drift apart silently

Each of those is one test below.
"""
import ast
import os
import subprocess
import sys
import unittest

import helpers  # noqa: F401  (sys.path setup)
import gidoon as core
import gidoon_render

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.join(REPO, "gidoon_render.py")

# Everything the render layer owns — the exact set a host project imports.
EXPORTS = (
    "TOOL_EMOJI", "FALLBACK_TOOL_EMOJI", "TOOL_DISPLAY",
    "MCP_PROVIDER_EMOJI", "FALLBACK_MCP_EMOJI",
    "humanize_tool_name", "title_words", "format_tool_label",
    "collapse_tool_runs", "count_suffix", "collapse_tool_lines",
    "extract_text", "extract_tools",
    "CHOICES_FENCE", "split_choices", "render_choices",
)


class Purity(unittest.TestCase):
    def test_it_imports_nothing_but_re_and_json(self):
        """A vendored file that grows an import grows a dependency in
        every project that copied it; json is stdlib with no I/O, so it
        adds none."""
        tree = ast.parse(open(MODULE, encoding="utf-8").read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        self.assertEqual(imported, {"re", "json"})

    def test_it_imports_standalone_without_gidoon(self):
        """The vendored copy lands somewhere gidoon.py does not exist."""
        proc = subprocess.run(
            [sys.executable, "-c",
             "import gidoon_render as r; "
             "print(r.format_tool_label('TodoWrite')[1])"],
            cwd=REPO, capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "Updating Checklist")


class ReExports(unittest.TestCase):
    def test_gidoon_re_exports_every_name(self):
        for name in EXPORTS:
            self.assertTrue(hasattr(core, name), f"gidoon lost {name}")

    def test_the_re_exports_are_the_same_objects(self):
        """`is`, not `==`. A copy would let the daemon and a host's
        vendored copy render the same tool differently, and nothing
        would notice."""
        for name in EXPORTS:
            self.assertIs(getattr(core, name), getattr(gidoon_render, name),
                          f"{name} is a copy, not a re-export")


class BehaviourIsUnchanged(unittest.TestCase):
    """A move is only a move if the output is identical."""

    def test_a_builtin_tool(self):
        self.assertEqual(core.format_tool_label("Bash"), ("💻", "Bash"))

    def test_an_mcp_tool(self):
        self.assertEqual(
            core.format_tool_label("mcp__claude_ai_Gmail__list_labels"),
            ("📧", "Gmail: List Labels"))

    def test_a_skill(self):
        self.assertEqual(core.format_tool_label("Skill", {"skill": "brain"}),
                         ("🛠", "Skill: brain"))

    def test_collapse_still_counts_consecutive_runs(self):
        self.assertEqual(
            core.collapse_tool_lines([("💻", "Bash"), ("💻", "Bash"),
                                      ("📖", "Read")]),
            ["💻 Bash ×2", "📖 Read"])


REPLY = ("Here is the outline.\n\nChange anything, or say \"looks good\".\n\n"
         "```choices\n[\"Looks good\", \"Change a step\", \"/go\"]\n```")


class Choices(unittest.TestCase):
    def test_split_choices(self):
        text, choices = gidoon_render.split_choices(REPLY)
        self.assertEqual(choices, ["Looks good", "Change a step", "/go"])
        self.assertTrue(text.endswith('say "looks good".'))
        self.assertNotIn("```", text)

    def test_split_leaves_anything_else_alone(self):
        s = gidoon_render.split_choices
        self.assertEqual(s("plain"), ("plain", []))
        self.assertEqual(s(""), ("", []))
        self.assertEqual(s("```choices\n[1, 2]\n```"),
                         ("```choices\n[1, 2]\n```", []))
        self.assertEqual(s("```choices\nnot json\n```")[1], [])
        self.assertEqual(s('```choices\n{"a": 1}\n```')[1], [])
        self.assertEqual(s('a\n```choices\n["x"]\n```\nmore')[1], [])
        self.assertEqual(s("```choices\n[]\n```"), ("", []))

    def test_render_numbers_the_plain_ones_and_names_the_commands(self):
        out = gidoon_render.render_choices(REPLY)
        self.assertEqual(out, (
            'Here is the outline.\n\nChange anything, or say "looks good".'
            "\n\n1. Looks good\n2. Change a step\n"
            "Reply with a number, or in your own words.\nOr send: /go"))
        self.assertEqual(gidoon_render.render_choices("plain"), "plain")
        only = gidoon_render.render_choices(
            'x\n\n```choices\n["/a", "/b"]\n```')
        self.assertEqual(only, "x\n\nOr send: /a · /b")

    def test_gidoon_reexports_them(self):
        self.assertIs(core.split_choices, gidoon_render.split_choices)
        self.assertIs(core.render_choices, gidoon_render.render_choices)
