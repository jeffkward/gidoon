"""command_hooks — slash commands the HOST project handles itself.

A host embedding a mouth has commands of its own, and it needs them handled
DETERMINISTICALLY rather than improvised by the model: "/cap" should read a
number off disk, not reason about what the number might be.

So: a message whose first word is /<name> runs that hook, the rest of the
text arrives on its stdin, and the hook's stdout becomes the reply. Empty
stdout is a deliberate silent command. Built-ins win — a hook cannot
shadow /clear or /start.

The per-hook `timeout` matters more than it looks. The default is fine for
reading a number off disk and fatal for anything that runs a model: a
command backed by a workflow can legitimately take ten minutes, and with
one shared timeout every invocation would be SIGKILLed mid-run and reply
"didn't finish" forever.
"""
import os
import tempfile
import unittest
from unittest import mock

import helpers
import gidoon as core

D = helpers.DAEMON

HOOKS = ('{ cap = { command = "printf \'spend: 4.20\'", '
         'description = "today\'s spend" }, '
         'echo = { command = "sed \'s/^/you said: /\'", '
         'description = "echo back" }, '
         'quiet = { command = "true", description = "says nothing" }, '
         'argv = { command = "printf \'arg=%s\' \\"$1\\"", '
         'description = "argv form" }, '
         'boom = { command = "echo partial; exit 3", '
         'description = "fails loudly" }, '
         'silentfail = { command = "exit 4", '
         'description = "fails quietly" } }')


class CommandHooks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def make(self, hooks=HOOKS, **extra):
        cfg = helpers.write_config(self.dir, command_hooks=hooks, **extra)
        tg = helpers.FakeTg()
        return helpers.make_daemon(cfg, tg), tg

    def send(self, daemon, text):
        def must_not_run(*a, **kw):
            raise AssertionError("a host command reached the model")
        with mock.patch.object(core, "stream_claude", must_not_run):
            daemon.handle_message(helpers.text_msg(text))

    def replies(self, tg):
        return [t for _, t in tg.sent]

    def test_hook_stdout_becomes_the_reply(self):
        daemon, tg = self.make()
        self.send(daemon, "/cap")
        self.assertIn("spend: 4.20", self.replies(tg))

    def test_arguments_arrive_on_stdin(self):
        daemon, tg = self.make()
        self.send(daemon, "/echo hello there")
        self.assertIn("you said: hello there", self.replies(tg))

    def test_arguments_also_arrive_as_dollar_one(self):
        daemon, tg = self.make()
        self.send(daemon, "/argv hello there")
        self.assertIn("arg=hello there", self.replies(tg))

    def test_empty_stdout_sends_nothing(self):
        """A deliberate silent command — not every command wants to talk."""
        daemon, tg = self.make()
        self.send(daemon, "/quiet")
        self.assertEqual(tg.sent, [])

    def test_a_failing_hook_still_relays_what_it_printed(self):
        daemon, tg = self.make()
        self.send(daemon, "/boom")
        self.assertIn("partial", self.replies(tg))

    def test_a_failing_hook_with_no_output_says_something(self):
        """Silence after an explicit command reads as a dead bot."""
        daemon, tg = self.make()
        self.send(daemon, "/silentfail")
        self.assertTrue(self.replies(tg), "no reply at all")

    def test_an_unknown_slash_command_is_just_a_prompt(self):
        cfg = helpers.write_config(self.dir, command_hooks=HOOKS)
        daemon = helpers.make_daemon(cfg, helpers.FakeTg())
        seen = {}

        def fake_stream(p, sid, on_event, **kw):
            seen["prompt"] = p
            return {"type": "result", "result": "ok", "session_id": "s"}

        with mock.patch.object(core, "stream_claude", fake_stream):
            daemon.handle_message(helpers.text_msg("/unknown thing"))
        self.assertEqual(seen["prompt"], "/unknown thing")

    def test_the_name_match_is_exact_not_a_prefix(self):
        """/capacity is not /cap."""
        cfg = helpers.write_config(self.dir, command_hooks=HOOKS)
        daemon = helpers.make_daemon(cfg, helpers.FakeTg())
        seen = {}

        def fake_stream(p, sid, on_event, **kw):
            seen["prompt"] = p
            return {"type": "result", "result": "ok", "session_id": "s"}

        with mock.patch.object(core, "stream_claude", fake_stream):
            daemon.handle_message(helpers.text_msg("/capacity please"))
        self.assertEqual(seen["prompt"], "/capacity please")


class BuiltInsWin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def test_clear_cannot_be_overridden(self):
        cfg = helpers.write_config(
            self.dir,
            command_hooks='{ clear = { command = "printf hijacked", '
                          'description = "no" } }')
        self.assertNotIn("clear", cfg["command_hooks"])

    def test_an_alias_cannot_be_overridden_either(self):
        """/new is still typeable, so a hook of that name would never be
        reached — dead config that looks live."""
        cfg = helpers.write_config(
            self.dir,
            command_hooks='{ new = { command = "printf hijacked", '
                          'description = "no" } }')
        self.assertNotIn("new", cfg["command_hooks"])

    def test_start_cannot_be_overridden(self):
        """/start is handled before dispatch, so a hook of that name would
        be dead config that looks live."""
        cfg = helpers.write_config(
            self.dir,
            command_hooks='{ start = { command = "printf hijacked", '
                          'description = "no" } }')
        self.assertNotIn("start", cfg["command_hooks"])

    def test_junk_names_are_dropped(self):
        cfg = helpers.write_config(
            self.dir,
            command_hooks='{ "Bad-Name" = { command = "true" }, '
                          '"9lives" = { command = "true" }, '
                          '"ok_one" = { command = "true" } }')
        self.assertEqual(sorted(cfg["command_hooks"]), ["ok_one"])

    def test_a_hook_with_no_command_is_dropped(self):
        cfg = helpers.write_config(
            self.dir, command_hooks='{ empty = { description = "x" } }')
        self.assertEqual(cfg["command_hooks"], {})

    def test_description_defaults_to_the_name(self):
        cfg = helpers.write_config(
            self.dir, command_hooks='{ thing = { command = "true" } }')
        self.assertEqual(cfg["command_hooks"]["thing"]["description"],
                         "/thing")


class PerHookTimeout(unittest.TestCase):
    """The amendment that matters: one shared timeout kills any command
    backed by a model run."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def run_hook(self, spec, default_timeout):
        cfg = helpers.write_config(self.dir, command_hooks=spec)
        tg = helpers.FakeTg()
        daemon = helpers.make_daemon(cfg, tg)
        with mock.patch.object(core, "DEFAULT_HOOK_TIMEOUT_SECS",
                               default_timeout):
            daemon.handle_message(helpers.text_msg("/slow"))
        return [t for _, t in tg.sent]

    def test_defaults_to_the_shared_timeout(self):
        self.assertEqual(core.DEFAULT_HOOK_TIMEOUT_SECS, 30)
        cfg = helpers.write_config(
            self.dir, command_hooks='{ x = { command = "true" } }')
        self.assertEqual(cfg["command_hooks"]["x"]["timeout"], 30)

    def test_a_long_hook_timeout_survives_a_short_default(self):
        """A workflow-backed command must outlive the default."""
        replies = self.run_hook(
            '{ slow = { command = "sleep 0.4; printf done", timeout = 5 } }',
            default_timeout=0.2)
        self.assertIn("done", replies)

    def test_a_short_hook_timeout_is_enforced(self):
        replies = self.run_hook(
            '{ slow = { command = "sleep 5; printf done", timeout = 0.3 } }',
            default_timeout=30)
        self.assertNotIn("done", replies)
        self.assertTrue(replies, "a killed hook must still say something")

    def test_a_junk_timeout_falls_back_to_the_default(self):
        cfg = helpers.write_config(
            self.dir,
            command_hooks='{ x = { command = "true", timeout = "soon" }, '
                          'y = { command = "true", timeout = -5 } }')
        self.assertEqual(cfg["command_hooks"]["x"]["timeout"], 30)
        self.assertEqual(cfg["command_hooks"]["y"]["timeout"], 30)


class Menu(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def test_menu_includes_builtins_and_hooks(self):
        cfg = helpers.write_config(self.dir, command_hooks=HOOKS)
        tg = helpers.FakeTg()
        daemon = helpers.make_daemon(cfg, tg)
        daemon.register_commands()
        # FakeTg records the argument verbatim: [(name, description), ...].
        # The real Tg does the JSON encoding itself, further down.
        names = [n.lstrip("/") for n, _ in tg.menus[-1]]
        self.assertIn("clear", names)
        self.assertIn("cap", names)
        self.assertIn("echo", names)

    def test_every_menu_entry_carries_a_description(self):
        cfg = helpers.write_config(self.dir, command_hooks=HOOKS)
        tg = helpers.FakeTg()
        helpers.make_daemon(cfg, tg).register_commands()
        self.assertTrue(all(desc for _, desc in tg.menus[-1]))


class Help(unittest.TestCase):
    """A bot that can list its own commands is the right default, and
    rendering that list is pure config — no host code to write."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def ask(self, **extra):
        cfg = helpers.write_config(self.dir, **extra)
        tg = helpers.FakeTg()
        daemon = helpers.make_daemon(cfg, tg)
        daemon.handle_message(helpers.text_msg("/help"))
        return cfg, [t for _, t in tg.sent]

    def test_help_is_on_by_default_for_a_new_instance(self):
        cfg, replies = self.ask()
        self.assertIn("help", cfg["commands"])
        self.assertTrue(replies)

    def test_help_lists_builtins_and_hook_commands(self):
        _, replies = self.ask(command_hooks=HOOKS)
        text = "\n".join(replies)
        for expected in ("/clear", "/help", "/cap", "/echo"):
            self.assertIn(expected, text)

    def test_help_shows_the_descriptions(self):
        _, replies = self.ask(command_hooks=HOOKS)
        self.assertIn("today's spend", "\n".join(replies))

    def test_an_instance_that_disables_help_treats_it_as_ordinary_text(self):
        """Not swallowed — an instance without /help enabled has no special
        meaning for it, so it reaches the model like any other unrecognized
        slash command. Consistent beats clever."""
        cfg = helpers.write_config(self.dir, commands='["new"]')
        daemon = helpers.make_daemon(cfg, helpers.FakeTg())
        seen = {}

        def fake_stream(p, sid, on_event, **kw):
            seen["prompt"] = p
            return {"type": "result", "result": "ok", "session_id": "s"}

        with mock.patch.object(core, "stream_claude", fake_stream):
            daemon.handle_message(helpers.text_msg("/help"))
        self.assertEqual(seen["prompt"], "/help")

    def test_help_never_reaches_the_model(self):
        cfg = helpers.write_config(self.dir)
        daemon = helpers.make_daemon(cfg, helpers.FakeTg())

        def must_not_run(*a, **kw):
            raise AssertionError("/help reached the model")

        with mock.patch.object(core, "stream_claude", must_not_run):
            daemon.handle_message(helpers.text_msg("/help"))
