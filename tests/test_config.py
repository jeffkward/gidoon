"""Config load/validation: required fields, defaults, the permission_mode
absent → no-flag contract, derived state paths."""
import os
import tempfile
import unittest

import helpers
import gidoon as core


class LoadConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def test_minimal_config_gets_defaults(self):
        cfg = helpers.write_config(self.dir)
        self.assertEqual(cfg["label"], "testbot")
        self.assertEqual(cfg["emoji"], "🗣")
        self.assertEqual(cfg["commands"], ["clear", "help"])
        self.assertIsNone(cfg["permission_mode"])
        self.assertEqual(cfg["allowed_tools"], [])
        self.assertIsNone(cfg["model"])
        self.assertIsNone(cfg["setting_sources"])
        self.assertIsNone(cfg["pre_turn_hook"])
        self.assertIs(cfg["log_token_usage"], True)
        self.assertEqual(cfg["timeout_secs"], 600)
        self.assertTrue(cfg["claude_bin"].endswith("/.local/bin/claude"))

    def test_derived_paths_keyed_by_config_stem(self):
        cfg = helpers.write_config(self.dir, name="mybot")
        self.assertEqual(cfg["name"], "mybot")
        self.assertEqual(cfg["session_path"],
                         os.path.join(self.dir, "mybot-session.json"))
        self.assertEqual(cfg["usage_path"],
                         os.path.join(self.dir, "mybot-usage.jsonl"))
        self.assertEqual(cfg["log_path"],
                         os.path.join(self.dir, "mybot.log"))

    def test_missing_required_field_raises(self):
        for missing in ("label", "env_file", "cwd"):
            path = os.path.join(self.dir, f"broken-{missing}.toml")
            fields = {"label": '"x"', "env_file": '"/tmp/x.env"',
                      "cwd": '"/tmp"'}
            del fields[missing]
            with open(path, "w", encoding="utf-8") as f:
                for k, v in fields.items():
                    f.write(f"{k} = {v}\n")
            with self.assertRaises(core.ConfigError):
                core.load_config(path, state_dir=self.dir)

    def test_empty_permission_mode_means_none(self):
        cfg = helpers.write_config(self.dir, permission_mode='""')
        self.assertIsNone(cfg["permission_mode"])

    def test_explicit_permission_mode_kept(self):
        cfg = helpers.write_config(self.dir,
                                   permission_mode='"bypassPermissions"')
        self.assertEqual(cfg["permission_mode"], "bypassPermissions")

    def test_setting_sources_absent_means_none(self):
        # No opinion by default — a turn loads whatever settings.json
        # layers the host project itself uses.
        cfg = helpers.write_config(self.dir)
        self.assertIsNone(cfg["setting_sources"])

    def test_empty_setting_sources_means_none(self):
        cfg = helpers.write_config(self.dir, setting_sources='""')
        self.assertIsNone(cfg["setting_sources"])

    def test_explicit_setting_sources_kept(self):
        cfg = helpers.write_config(self.dir, setting_sources='"project"')
        self.assertEqual(cfg["setting_sources"], "project")

    def test_overrides_applied(self):
        cfg = helpers.write_config(
            self.dir, emoji='"🐻"', model='"opus"', timeout_secs="120",
            allowed_tools='["WebSearch", "Read"]')
        self.assertEqual(cfg["emoji"], "🐻")
        self.assertEqual(cfg["model"], "opus")
        self.assertEqual(cfg["timeout_secs"], 120)
        self.assertEqual(cfg["allowed_tools"], ["WebSearch", "Read"])

    def test_system_prompt_defaults_to_builtin(self):
        cfg = helpers.write_config(self.dir)
        self.assertEqual(cfg["system_prompt"], core.DEFAULT_SYSTEM_PROMPT)
        self.assertTrue(core.DEFAULT_SYSTEM_PROMPT.strip())

    def test_system_prompt_override_replaces_builtin(self):
        cfg = helpers.write_config(
            self.dir, system_prompt='"You are a pirate."')
        self.assertEqual(cfg["system_prompt"], "You are a pirate.")

    def test_empty_system_prompt_means_builtin(self):
        cfg = helpers.write_config(self.dir, system_prompt='""')
        self.assertEqual(cfg["system_prompt"], core.DEFAULT_SYSTEM_PROMPT)

    def test_unknown_key_raises(self):
        with self.assertRaises(core.ConfigError):
            helpers.write_config(self.dir, permision_mode='"typo"')

    def test_unknown_command_raises(self):
        with self.assertRaises(core.ConfigError):
            helpers.write_config(self.dir, commands='["new", "teleport"]')

    def test_missing_file_raises(self):
        with self.assertRaises(core.ConfigError):
            core.load_config(os.path.join(self.dir, "nope.toml"),
                             state_dir=self.dir)

    def test_invalid_toml_raises(self):
        path = os.path.join(self.dir, "bad.toml")
        with open(path, "w", encoding="utf-8") as f:
            f.write("label = not quoted\n")
        with self.assertRaises(core.ConfigError):
            core.load_config(path, state_dir=self.dir)

    def test_allowed_tools_must_be_string_list(self):
        with self.assertRaises(core.ConfigError):
            helpers.write_config(self.dir, allowed_tools='[1, 2]')


class ReadEnv(unittest.TestCase):
    def test_parses_kv_skips_comments_and_blanks(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env",
                                         delete=False) as f:
            f.write("# comment\n\nGIDOON_BOT_TOKEN=abc:def\n"
                    "GIDOON_CHAT_ID = 42 \nnot a kv line\n")
            path = f.name
        self.addCleanup(os.unlink, path)
        env = core.read_env(path)
        self.assertEqual(env, {"GIDOON_BOT_TOKEN": "abc:def",
                               "GIDOON_CHAT_ID": "42"})


if __name__ == "__main__":
    unittest.main()
