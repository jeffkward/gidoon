"""Silent sends, link previews, and the two renderer additions (task-list
boxes, real dividers)."""
import json
import os
import tempfile
import unittest
from unittest import mock

import helpers
import gidoon
import gidoon_render as r

D = helpers.DAEMON


class TgParams(unittest.TestCase):
    """What Tg.send and Tg.edit actually put on the wire."""

    def calls(self, fn, link_previews=False):
        tg = D.Tg("000:fake")
        tg.link_previews = link_previews
        seen = []
        with mock.patch.object(tg, "call",
                               side_effect=lambda m, **p: seen.append((m, p))
                               or {"message_id": 1}):
            fn(tg)
        return seen

    def test_previews_are_off_by_default_on_a_send(self):
        (_, p), = self.calls(lambda tg: tg.send(42, "see https://x.org"))
        self.assertEqual(json.loads(p["link_preview_options"]),
                         {"is_disabled": True})
        self.assertNotIn("disable_notification", p)

    def test_previews_are_off_on_an_edit_too(self):
        (_, p), = self.calls(lambda tg: tg.edit(42, 7, "https://x.org"))
        self.assertIn("link_preview_options", p)

    def test_an_instance_can_keep_previews(self):
        (_, p), = self.calls(lambda tg: tg.send(42, "x"), link_previews=True)
        self.assertNotIn("link_preview_options", p)

    def test_a_silent_send_does_not_buzz(self):
        (_, p), = self.calls(lambda tg: tg.send(42, "x", silent=True))
        self.assertEqual(p["disable_notification"], "true")


class SendCommandOptions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def send(self, argv, **cfg):
        helpers.write_config(self.tmp.name, **cfg)
        toml = os.path.join(self.tmp.name, "testbot.toml")
        fake = helpers.FakeTg()
        with mock.patch.object(D, "Tg", return_value=fake):
            D.send_main([toml] + argv)
        return fake

    def test_silent_flag_reaches_every_part(self):
        fake = self.send(["--silent", "x" * 5000])
        self.assertEqual(fake.sent_silent, [True, True])

    def test_without_the_flag_a_send_buzzes(self):
        self.assertEqual(self.send(["hi"]).sent_silent, [False])

    def test_the_instance_setting_reaches_the_client(self):
        fake = self.send(["hi"], link_previews="true")
        self.assertTrue(fake.link_previews)


class ConfigKey(unittest.TestCase):
    def test_link_previews_defaults_off_and_must_be_a_bool(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(helpers.write_config(d)["link_previews"])
            self.assertTrue(helpers.write_config(
                d, link_previews="true")["link_previews"])
            with self.assertRaises(gidoon.ConfigError):
                helpers.write_config(d, link_previews='"yes"')


class Render(unittest.TestCase):
    def test_task_lists_become_boxes(self):
        self.assertEqual(r.to_telegram_html("- [ ] todo\n- [x] done\n* [X] also"),
                         "☐ todo\n☑️ done\n☑️ also")

    def test_an_indented_task_keeps_its_indent(self):
        self.assertEqual(r.to_telegram_html("  - [ ] sub"), "  ☐ sub")

    def test_a_divider_becomes_a_line(self):
        self.assertEqual(r.to_telegram_html("a\n---\nb\n* * *"),
                         "a\n──────────\nb\n──────────")

    def test_brackets_mid_line_are_left_alone(self):
        self.assertEqual(r.to_telegram_html("- see [ ] here"),
                         "• see [ ] here")


if __name__ == "__main__":
    unittest.main()
