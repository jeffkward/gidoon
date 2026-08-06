"""Security properties, pinned so a refactor can't quietly undo them.

The big one: a Telegram bot's username is public, so ANYONE can message it.
Everything in Allowlist exists to prove that only the configured chat is
ever answered, acknowledged, or allowed to start a claude turn."""
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import helpers
import gidoon as core

D = helpers.DAEMON

STRANGER = 999            # some other Telegram user
GROUP = -1001234567890    # a group the bot got added to
OWNER = 42                # the configured chat id in helpers.write_config


class SecurityCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def make(self, **cfg_overrides):
        cfg = helpers.write_config(self.dir, **cfg_overrides)
        tg = helpers.FakeTg()
        return helpers.make_daemon(cfg, tg), tg


class Allowlist(SecurityCase):
    """Nobody but the configured chat gets a turn, a reply, or a reaction."""

    def deliver(self, daemon, chat_id, update_id=1, text="hi"):
        """Push one update through the real entry point (handle_update),
        with stream_claude booby-trapped: a stranger must never reach it."""
        msg = {"message_id": 7, "text": text, "chat": {"id": chat_id}}

        def must_not_run(*a, **kw):
            raise AssertionError("a claude turn was started for a stranger")

        with mock.patch.object(core, "stream_claude", must_not_run):
            daemon.handle_update({"update_id": update_id, "message": msg})

    def assert_silent(self, tg):
        self.assertEqual(tg.sent, [], "sent a message to a stranger")
        self.assertEqual(tg.reactions, [], "reacted to a stranger")
        self.assertEqual(tg.edits, [], "edited anything for a stranger")
        self.assertEqual(tg.actions, [], "typed at a stranger")

    def test_another_private_chat_gets_nothing(self):
        daemon, tg = self.make()
        self.deliver(daemon, STRANGER)
        self.assert_silent(tg)

    def test_a_group_chat_gets_nothing(self):
        daemon, tg = self.make()
        self.deliver(daemon, GROUP)
        self.assert_silent(tg)

    def test_a_message_with_no_chat_gets_nothing(self):
        daemon, tg = self.make()
        with mock.patch.object(core, "stream_claude",
                               mock.Mock(side_effect=AssertionError)):
            daemon.handle_update({"update_id": 1,
                                  "message": {"message_id": 7, "text": "hi"}})
        self.assert_silent(tg)

    def test_the_owner_id_as_a_string_does_not_match(self):
        # Telegram sends ints. A payload carrying "42" must not be treated
        # as chat 42 — no loose comparison anywhere in the path.
        daemon, tg = self.make()
        self.deliver(daemon, str(OWNER))
        self.assert_silent(tg)

    def test_commands_from_a_stranger_do_nothing(self):
        # /new is handled inside handle_message — reachable only after the
        # allowlist, so a stranger can't reset the owner's conversation.
        daemon, tg = self.make()
        daemon.state["session_id"] = "keep-me"
        daemon.save_state()
        self.deliver(daemon, STRANGER, text="/new")
        self.assertEqual(daemon.state["session_id"], "keep-me")
        self.assert_silent(tg)

    def test_a_stranger_still_advances_the_offset(self):
        # Otherwise one stranger's message would wedge the poll loop
        # forever, re-delivering the same update.
        daemon, tg = self.make()
        self.deliver(daemon, STRANGER, update_id=5)
        self.assertEqual(daemon.state["offset"], 6)

    def test_replies_go_to_the_configured_chat_never_the_sender(self):
        # Defense in depth: even if a message somehow got through, every
        # send in the turn path is addressed to the configured chat id.
        daemon, tg = self.make()

        def fake_stream(prompt, session_id, on_event, **kw):
            return {"type": "result", "result": "answer",
                    "session_id": "s", "num_turns": 1}

        with mock.patch.object(core, "stream_claude", fake_stream):
            daemon.handle_message({"message_id": 7, "text": "hi",
                                   "chat": {"id": STRANGER}})
        self.assertTrue(tg.sent)
        for chat_id, _ in tg.sent:
            self.assertEqual(chat_id, OWNER)
        for chat_id, _, _ in tg.edits:
            self.assertEqual(chat_id, OWNER)


class UntrustedTextNeverReachesArgv(SecurityCase):
    """The inbound message is data. It travels on stdin, never argv."""

    def test_build_cmd_carries_no_message_text(self):
        cmd = core.build_cmd("/x/claude", session_id="s",
                             system_prompt="owner text")
        self.assertNotIn("--dangerously-skip-permissions", cmd)
        self.assertTrue(all(isinstance(a, str) for a in cmd))

    def test_a_message_that_looks_like_a_flag_goes_over_stdin(self):
        """Run a stub 'claude' that reports its argv and its stdin, and
        confirm a flag-shaped message lands in stdin only."""
        stub = os.path.join(self.dir, "claude-stub")
        with open(stub, "w") as f:
            f.write(
                "#!/bin/sh\n"
                'printf \'{"type":"result","subtype":"success",'
                '"result":"ok","session_id":"s"}\\n\'\n'
                'cat > "$(dirname "$0")/stdin.txt"\n'
                'printf "%s\\n" "$@" > "$(dirname "$0")/argv.txt"\n')
        os.chmod(stub, 0o755)
        hostile = "--dangerously-skip-permissions --model evil"
        core.stream_claude(hostile, None, lambda e: None, cwd=self.dir,
                           timeout_secs=30, claude_bin=stub)
        with open(os.path.join(self.dir, "argv.txt")) as f:
            argv = f.read()
        with open(os.path.join(self.dir, "stdin.txt")) as f:
            stdin = f.read()
        self.assertNotIn("dangerously", argv)
        self.assertIn("dangerously", stdin)


class HookIsolation(SecurityCase):
    """pre_turn_hook runs a shell command from the OWNER's config; the
    inbound text reaches it on stdin, so message content can never become
    shell syntax."""

    def test_shell_metacharacters_in_a_message_do_not_execute(self):
        marker = os.path.join(self.dir, "pwned")
        hook = f"cat > {os.path.join(self.dir, 'hook-stdin.txt')}"
        daemon, tg = self.make(pre_turn_hook=f'"{hook}"')
        hostile = f"hi; touch {marker}"

        def fake_stream(prompt, session_id, on_event, **kw):
            return {"type": "result", "result": "ok", "session_id": "s",
                    "num_turns": 1}

        with mock.patch.object(core, "stream_claude", fake_stream):
            daemon.handle_message(helpers.text_msg(hostile))
        self.assertFalse(os.path.exists(marker),
                         "message text was executed by the shell")
        with open(os.path.join(self.dir, "hook-stdin.txt")) as f:
            self.assertIn("touch", f.read())  # arrived as DATA


class SecretsStayOut(SecurityCase):
    def test_api_errors_never_carry_the_token(self):
        # The request URL embeds the bot token; an exception that escaped
        # with it attached would put the token in logs and tracebacks.
        err = D.TgError(409)
        self.assertNotIn("bot", str(err).lower().replace("bot token", ""))
        for text in (str(err), repr(err)):
            self.assertNotIn("api.telegram.org", text)
            self.assertNotIn("000:fake", text)

    def test_logs_record_shape_not_content(self):
        daemon, tg = self.make()
        secret = "my bank password is hunter2"

        def fake_stream(prompt, session_id, on_event, **kw):
            return {"type": "result", "result": "sure thing",
                    "session_id": "s", "num_turns": 1}

        with mock.patch.object(core, "stream_claude", fake_stream):
            daemon.handle_message(helpers.text_msg(secret))
        with open(daemon.cfg["log_path"], encoding="utf-8") as f:
            log = f.read()
        self.assertNotIn("hunter2", log)
        self.assertNotIn("sure thing", log)
        self.assertIn("turn done", log)

    def test_costs_receipt_holds_no_message_content(self):
        daemon, tg = self.make()

        def fake_stream(prompt, session_id, on_event, **kw):
            return {"type": "result", "result": "secret answer",
                    "session_id": "s", "num_turns": 1}

        with mock.patch.object(core, "stream_claude", fake_stream):
            daemon.handle_message(helpers.text_msg("secret question"))
        with open(daemon.cfg["costs_path"], encoding="utf-8") as f:
            self.assertNotIn("secret", f.read())


class InstanceNameIsNotAPath(unittest.TestCase):
    """`uninstall` turns a name into files to delete — the name must not be
    able to point outside the config directory."""

    def test_plain_names_are_valid(self):
        for name in ("work", "my-bot", "a1", "notes2026"):
            self.assertTrue(core.is_valid_instance_name(name), name)

    def test_traversal_and_separators_are_rejected(self):
        for name in ("../../etc/passwd", "a/b", "..", ".", "/abs",
                     "with space", "Caps", "semi;colon", "", None,
                     "star*", "dot.toml"):
            self.assertFalse(core.is_valid_instance_name(name), repr(name))

    def test_uninstall_refuses_a_traversing_name(self):
        with self.assertRaises(SystemExit):
            D.uninstall_main(["../../tmp/x", "--yes"])


if __name__ == "__main__":
    unittest.main()
