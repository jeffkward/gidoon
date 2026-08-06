"""Tests for the pure rendering helpers (collapse_tool_runs / count_suffix /
collapse_tool_lines / format_tool_label / humanize_tool_name / title_words).
The collapse helpers are characterization tests ported verbatim from the
source; format_tool_label's cases also pin behavior added since (provider
emojis, method title-casing, TOOL_DISPLAY overrides)."""
import unittest

import helpers  # noqa: F401  (sys.path setup)
import gidoon as core


class CollapseToolRuns(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(core.collapse_tool_runs([]), [])

    def test_single(self):
        self.assertEqual(core.collapse_tool_runs([("💻", "Bash")]),
                         [("💻", "Bash", 1)])

    def test_consecutive_repeats_merge(self):
        completed = [("💻", "Bash"), ("💻", "Bash"), ("💻", "Bash")]
        self.assertEqual(core.collapse_tool_runs(completed),
                         [("💻", "Bash", 3)])

    def test_non_consecutive_repeat_stays_separate(self):
        completed = [("💻", "Bash"), ("📖", "Read"), ("💻", "Bash")]
        self.assertEqual(core.collapse_tool_runs(completed),
                         [("💻", "Bash", 1), ("📖", "Read", 1),
                          ("💻", "Bash", 1)])

    def test_order_preserved(self):
        completed = [("🔎", "Web Search"), ("🔎", "Web Search"),
                     ("📖", "Read"), ("📖", "Read"), ("📖", "Read"),
                     ("✏️", "Edit")]
        self.assertEqual(core.collapse_tool_runs(completed),
                         [("🔎", "Web Search", 2), ("📖", "Read", 3),
                          ("✏️", "Edit", 1)])


class CountSuffix(unittest.TestCase):
    def test_one_is_empty(self):
        self.assertEqual(core.count_suffix(1), "")

    def test_many(self):
        self.assertEqual(core.count_suffix(2), " ×2")
        self.assertEqual(core.count_suffix(17), " ×17")


class CollapseToolLines(unittest.TestCase):
    def test_lines_render_with_counts(self):
        completed = [("💻", "Bash"), ("💻", "Bash"), ("🧰", "Tool Search")]
        self.assertEqual(core.collapse_tool_lines(completed),
                         ["💻 Bash ×2", "🧰 Tool Search"])

    def test_empty(self):
        self.assertEqual(core.collapse_tool_lines([]), [])


class FormatToolLabel(unittest.TestCase):
    def test_known_tool(self):
        self.assertEqual(core.format_tool_label("Bash"), ("💻", "Bash"))

    def test_camel_case_humanized(self):
        self.assertEqual(core.format_tool_label("WebSearch"),
                         ("🔎", "Web Search"))

    def test_unknown_tool_fallback_emoji(self):
        self.assertEqual(core.format_tool_label("FooBar"),
                         ("⚙️", "Foo Bar"))

    def test_skill(self):
        self.assertEqual(core.format_tool_label("Skill", {"skill": "harvest"}),
                         ("🛠", "Skill: harvest"))

    def test_mcp_provider_method(self):
        self.assertEqual(
            core.format_tool_label("mcp__claude_ai_Gmail__list_labels"),
            ("📧", "Gmail: List Labels"))

    def test_mcp_malformed_falls_back(self):
        self.assertEqual(core.format_tool_label("mcp__weird"),
                         ("⚙️", "mcp__weird"))

    def test_mcp_method_title_cased(self):
        self.assertEqual(
            core.format_tool_label("mcp__claude_ai_Google_Calendar__list_events"),
            ("📅", "Google Calendar: List Events"))

    def test_mcp_unknown_provider_falls_back_to_plug(self):
        self.assertEqual(
            core.format_tool_label("mcp__somesrv__do_thing"),
            ("🔌", "Somesrv: Do Thing"))

    def test_mcp_lowercase_provider_capitalized(self):
        self.assertEqual(
            core.format_tool_label("mcp__playwright__browser_click"),
            ("🌐", "Playwright: Browser Click"))

    def test_mcp_slack(self):
        self.assertEqual(
            core.format_tool_label("mcp__claude_ai_Slack__slack_send_message"),
            ("#️⃣", "Slack: Slack Send Message"))

    def test_mcp_telegram_not_an_airplane(self):
        self.assertEqual(
            core.format_tool_label("mcp__telegram__send_message"),
            ("💬", "Telegram: Send Message"))

    def test_mcp_camel_case_method_word_preserved(self):
        # A non-snake_case method word (e.g. camelCase) must not be mangled
        # by title-casing — only all-lowercase words get capitalized.
        self.assertEqual(
            core.format_tool_label("mcp__somesrv__getUpdates"),
            ("🔌", "Somesrv: getUpdates"))

    def test_tool_search_has_toolbox_emoji(self):
        self.assertEqual(core.format_tool_label("ToolSearch"),
                         ("🧰", "Tool Search"))

    def test_todo_write_humanizes_to_updating_checklist(self):
        self.assertEqual(core.format_tool_label("TodoWrite"),
                         ("📝", "Updating Checklist"))

    def test_task_humanizes_to_subagent_handoff(self):
        self.assertEqual(core.format_tool_label("Task"),
                         ("🤖", "Handed Task to Sub-agent"))

    def test_ask_user_question_humanizes(self):
        self.assertEqual(core.format_tool_label("AskUserQuestion"),
                         ("❓", "Asking You a Question"))

    def test_glob_humanizes_to_finding_files(self):
        self.assertEqual(core.format_tool_label("Glob"),
                         ("🗂", "Finding Files"))

    def test_grep_humanizes_to_searching_text(self):
        self.assertEqual(core.format_tool_label("Grep"),
                         ("🔍", "Searching Text"))


if __name__ == "__main__":
    unittest.main()
