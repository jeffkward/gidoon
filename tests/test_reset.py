"""Clearing context: /clear, and /start as Telegram's version of it.

`/clear` is the published command, named to match the runtime it drives.
`/new` stays as a hidden alias so nobody's muscle memory or existing config
breaks.

`/start` is not a command anyone types — Telegram sends it when a chat is
new or was deleted and reopened. Either way the transcript on the owner's
side is gone, so resuming one they can no longer see is the wrong default.
It is never published in the '/' menu.

What differs between them is only the REPLY, because the situation does.
Four states, and each says only what is true of it:

  /clear                    they typed it; they know what they did
  /start, first contact     nothing has ever happened — claim no clearing
  /start, had a session     their side went away; ours matched
  /start, already fresh     acknowledge the new chat, claim nothing

That last one matters: "nothing to clear" would draw attention to a
non-event. Saying less is more honest.
"""
import os
import tempfile
import unittest
from unittest import mock

import helpers
import gidoon as core

D = helpers.DAEMON


class Registry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def test_clear_is_the_published_command(self):
        self.assertIn("clear", core.COMMANDS)
        self.assertNotIn("new", core.COMMANDS)

    def test_new_is_a_recognized_alias(self):
        self.assertEqual(core.COMMAND_ALIASES.get("new"), "clear")

    def test_an_existing_config_saying_new_still_loads(self):
        """Every instance in the wild says commands = ["new"]. Publishing a
        new name must not turn their config into a startup error."""
        cfg = helpers.write_config(self.dir, commands='["new"]')
        self.assertEqual(cfg["commands"], ["clear"])

    def test_a_config_saying_clear_loads(self):
        cfg = helpers.write_config(self.dir, commands='["clear"]')
        self.assertEqual(cfg["commands"], ["clear"])

    def test_the_default_publishes_clear_and_help(self):
        self.assertEqual(helpers.write_config(self.dir)["commands"],
                         ["clear", "help"])

    def test_still_rejects_a_genuinely_unknown_command(self):
        with self.assertRaises(core.ConfigError):
            helpers.write_config(self.dir, commands='["teleport"]')

    def test_the_description_says_what_survives(self):
        """Telegram's own Clear Chat deletes messages. Ours does not, and
        the menu entry has to say so or the two read as the same thing."""
        self.assertIn("your messages stay", core.COMMANDS["clear"])

    def test_start_is_never_published(self):
        self.assertNotIn("start", core.COMMANDS)
        cfg = helpers.write_config(self.dir)
        tg = helpers.FakeTg()
        helpers.make_daemon(cfg, tg).register_commands()
        self.assertNotIn("start", [n.lstrip("/") for n, _ in tg.menus[-1]])


class ResetCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def make(self, emoji='"🦊"', **extra):
        cfg = helpers.write_config(self.dir, emoji=emoji, **extra)
        tg = helpers.FakeTg()
        return helpers.make_daemon(cfg, tg), tg

    def send(self, daemon, text):
        def must_not_run(*a, **kw):
            raise AssertionError("a reset command reached the model")
        with mock.patch.object(core, "stream_claude", must_not_run):
            daemon.handle_message(helpers.text_msg(text))

    def reply(self, tg):
        self.assertEqual(len(tg.sent), 1, f"expected one reply: {tg.sent}")
        return tg.sent[0][1]


class Clear(ResetCase):
    def test_clear_drops_the_session_on_disk_too(self):
        daemon, tg = self.make()
        daemon.state["session_id"] = "old-session"
        daemon.save_state()
        self.send(daemon, "/clear")
        self.assertIsNone(daemon.state["session_id"])
        self.assertIsNone(
            core.load_session(daemon.cfg["session_path"])["session_id"])

    def test_the_reply_asks_a_question_and_carries_the_face(self):
        daemon, tg = self.make()
        self.send(daemon, "/clear")
        reply = self.reply(tg)
        self.assertTrue(reply.startswith("🦊 "), reply)
        self.assertIn("Fresh conversation", reply)
        self.assertIn("What's up?", reply)

    def test_clear_says_nothing_about_telegram(self):
        """They typed the command — there is no other side to report on."""
        daemon, tg = self.make()
        self.send(daemon, "/clear")
        self.assertNotIn("Telegram", self.reply(tg))

    def test_new_still_works(self):
        daemon, tg = self.make()
        daemon.state["session_id"] = "old-session"
        self.send(daemon, "/new")
        self.assertIsNone(daemon.state["session_id"])
        self.assertIn("Fresh conversation", self.reply(tg))

    def test_an_instance_without_clear_sends_it_to_the_model(self):
        cfg = helpers.write_config(self.dir, commands='["help"]')
        daemon = helpers.make_daemon(cfg, helpers.FakeTg())
        seen = {}

        def fake_stream(p, sid, on_event, **kw):
            seen["prompt"] = p
            return {"type": "result", "result": "ok", "session_id": "s"}

        with mock.patch.object(core, "stream_claude", fake_stream):
            daemon.handle_message(helpers.text_msg("/clear"))
        self.assertEqual(seen["prompt"], "/clear")


class Start(ResetCase):
    def test_first_contact_claims_no_clearing(self):
        """offset 0 and no session: nothing has ever happened here, so
        'context cleared' would be a small lie."""
        daemon, tg = self.make()
        self.assertEqual(daemon.state["offset"], 0)
        self.send(daemon, "/start")
        reply = self.reply(tg)
        self.assertIn("Hello!", reply)
        self.assertNotIn("cleared", reply.lower())

    def test_a_live_conversation_is_cleared_and_reported(self):
        daemon, tg = self.make()
        daemon.state["offset"] = 354689073
        daemon.state["session_id"] = "live-session"
        daemon.save_state()
        self.send(daemon, "/start")
        self.assertIsNone(daemon.state["session_id"])
        reply = self.reply(tg)
        self.assertIn("Fresh Telegram chat", reply)
        self.assertIn("on this end", reply)

    def test_an_already_fresh_chat_is_acknowledged_and_nothing_claimed(self):
        """A second START tap, or cleared-then-cleared. Mentioning that
        there was nothing to clear draws attention to a non-event."""
        daemon, tg = self.make()
        daemon.state["offset"] = 354689073
        daemon.save_state()
        self.send(daemon, "/start")
        reply = self.reply(tg)
        self.assertIn("Fresh Telegram chat", reply)
        self.assertNotIn("cleared", reply.lower())
        self.assertNotIn("nothing", reply.lower())

    def test_start_resets_even_when_clear_is_disabled(self):
        """/start is not a command the owner chose to have — it is Telegram
        saying the transcript is gone. Resuming one they cannot see is the
        wrong default whatever the command list says."""
        daemon, tg = self.make(commands='["help"]')
        daemon.state["offset"] = 99
        daemon.state["session_id"] = "live-session"
        daemon.save_state()
        self.send(daemon, "/start")
        self.assertIsNone(daemon.state["session_id"])
        self.assertTrue(tg.sent)

    def test_start_never_reaches_the_model_and_takes_no_turn(self):
        daemon, tg = self.make()
        self.send(daemon, "/start")
        self.assertEqual(tg.reactions, [])
        self.assertEqual(tg.actions, [])

    def test_start_is_case_insensitive_like_every_other_command(self):
        daemon, tg = self.make()
        daemon.state["session_id"] = "live-session"
        self.send(daemon, "/START")
        self.assertIsNone(daemon.state["session_id"])

    def test_a_deep_link_payload_still_counts_as_start(self):
        """t.me/<bot>?start=xyz arrives as "/start xyz". It is still
        Telegram's handshake, and the transcript is still gone."""
        daemon, tg = self.make()
        daemon.state["offset"] = 99
        daemon.state["session_id"] = "live-session"
        self.send(daemon, "/start abc123")
        self.assertIsNone(daemon.state["session_id"])
        self.assertIn("Fresh Telegram chat", self.reply(tg))

    def test_clear_with_trailing_words_is_a_prompt_not_a_command(self):
        """Asymmetric with /start on purpose: /start carries a payload from
        Telegram, but a stray word after /clear means the owner is talking,
        and guessing wrong would silently drop their context."""
        cfg = helpers.write_config(self.dir)
        daemon = helpers.make_daemon(cfg, helpers.FakeTg())
        daemon.state["session_id"] = "live-session"
        seen = {}

        def fake_stream(p, sid, on_event, **kw):
            seen["prompt"] = p
            return {"type": "result", "result": "ok", "session_id": "s"}

        with mock.patch.object(core, "stream_claude", fake_stream):
            daemon.handle_message(helpers.text_msg("/clear the decks"))
        self.assertEqual(seen["prompt"], "/clear the decks")

    def test_the_poll_offset_survives_a_reset(self):
        """The offset is how we avoid replaying the backlog. Losing it to a
        /start would re-deliver every pending update."""
        daemon, tg = self.make()
        daemon.state["offset"] = 354689073
        daemon.save_state()
        self.send(daemon, "/start")
        self.assertEqual(
            core.load_session(daemon.cfg["session_path"])["offset"],
            354689073)


class LeadingFace(unittest.TestCase):
    """The status message has always led with the face (`🦊💭`, `🦊 ✅`), so
    the incidental strings should too — it reads as a speaker tag rather
    than a signature."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def test_the_non_text_decline_leads_with_the_face(self):
        cfg = helpers.write_config(self.dir, emoji='"🦊"')
        tg = helpers.FakeTg()
        daemon = helpers.make_daemon(cfg, tg)
        daemon.handle_message({"message_id": 1, "chat": {"id": 42},
                               "voice": {}})
        self.assertTrue(tg.sent[0][1].startswith("🦊 "), tg.sent[0][1])
