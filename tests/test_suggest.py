"""Instance-name suggestions offered by the installer: derived from the
project folder name and the bot's username, sanitized to the instance-name
rule (lowercase [a-z0-9-])."""
import unittest

import helpers  # noqa: F401  (sys.path setup)
import gidoon as core


class SanitizeInstanceName(unittest.TestCase):
    def test_lowercases(self):
        self.assertEqual(core.sanitize_instance_name("SkyCastle"), "skycastle")

    def test_spaces_and_punctuation_become_hyphens(self):
        self.assertEqual(core.sanitize_instance_name("My Project!"),
                         "my-project")

    def test_repeated_separators_collapse(self):
        self.assertEqual(core.sanitize_instance_name("a  --  b"), "a-b")

    def test_edges_trimmed(self):
        self.assertEqual(core.sanitize_instance_name("-_x_-"), "x")

    def test_unusable_input_is_empty(self):
        self.assertEqual(core.sanitize_instance_name("!!!"), "")
        self.assertEqual(core.sanitize_instance_name(""), "")

    def test_digits_and_hyphens_survive(self):
        self.assertEqual(core.sanitize_instance_name("app-2026"), "app-2026")


class StripBotSuffix(unittest.TestCase):
    def test_camel_case_bot_suffix(self):
        self.assertEqual(core.strip_bot_suffix("MyAwesomeBot"),
                         "MyAwesome")

    def test_underscore_bot_suffix(self):
        self.assertEqual(core.strip_bot_suffix("my_thing_bot"), "my_thing")

    def test_hyphen_bot_suffix(self):
        self.assertEqual(core.strip_bot_suffix("my-thing-bot"), "my-thing")

    def test_word_ending_in_bot_is_left_alone(self):
        # "robot" must not become "ro" — only a separator or a capital B
        # marks a real suffix.
        self.assertEqual(core.strip_bot_suffix("robot"), "robot")

    def test_bare_bot_is_left_alone(self):
        self.assertEqual(core.strip_bot_suffix("Bot"), "Bot")


class SuggestInstanceNames(unittest.TestCase):
    def test_folder_first_then_bot(self):
        self.assertEqual(
            core.suggest_instance_names("/Users/x/Documents/notes",
                                        "MyAwesomeBot"),
            ["notes", "myawesome"])

    def test_duplicates_collapse(self):
        self.assertEqual(
            core.suggest_instance_names("/x/SkyCastle", "SkyCastleBot"),
            ["skycastle"])

    def test_unusable_folder_name_is_dropped(self):
        self.assertEqual(core.suggest_instance_names("/x/!!!", "ThingBot"),
                         ["thing"])

    def test_trailing_slash_does_not_eat_the_folder_name(self):
        self.assertEqual(core.suggest_instance_names("/x/SkyCastle/", None),
                         ["skycastle"])

    def test_no_bot_username(self):
        self.assertEqual(core.suggest_instance_names("/x/notes", None), ["notes"])

    def test_nothing_usable_returns_empty(self):
        self.assertEqual(core.suggest_instance_names("/", None), [])


if __name__ == "__main__":
    unittest.main()
