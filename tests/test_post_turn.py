"""post_turn_hook — handing each completed turn to the host project.

A host that embeds a mouth usually keeps its own record of the
conversation: a database behind its web UI, metrics, a token ledger. It
cannot get that from pre_turn_hook, because the reply does not exist yet.

Two properties matter more than the feature itself:

  · it runs AFTER the answer is sent, and can never cost the owner a
    reply — a broken bookkeeping hook is a bookkeeping problem
  · the envelope carries the runtime's usage block UNTRANSLATED, so a host
    can account for tokens without a mapping layer between us. Mapping
    layers are how a dashboard silently reads zero forever.
"""
import json
import os
import tempfile
import unittest
from unittest import mock

import helpers
import gidoon as core

D = helpers.DAEMON

USAGE = {"input_tokens": 7, "output_tokens": 42,
         "cache_read_input_tokens": 900,
         "cache_creation_input_tokens": 33166}


class PostTurnHook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.sink = os.path.join(self.dir, "envelope.json")

    def run_turn(self, hook, prompt="what's up", reply="not much",
                 subtype=None, **cfg_extra):
        cfg = helpers.write_config(self.dir, post_turn_hook=f'"{hook}"',
                                   **cfg_extra)
        tg = helpers.FakeTg()
        daemon = helpers.make_daemon(cfg, tg)

        def fake_stream(p, sid, on_event, **kw):
            if subtype:
                return {"subtype": subtype}
            return {"type": "result", "result": reply, "session_id": "s-1",
                    "num_turns": 1, "usage": USAGE}

        with mock.patch.object(core, "stream_claude", fake_stream):
            daemon.handle_message(helpers.text_msg(prompt))
        return tg

    def envelope(self):
        with open(self.sink, encoding="utf-8") as f:
            return json.load(f)

    def test_hook_receives_both_sides_on_stdin(self):
        self.run_turn(f"cat > {self.sink}")
        env = self.envelope()
        self.assertEqual(env["prompt"], "what's up")
        self.assertEqual(env["reply"], "not much")
        self.assertEqual(env["instance"], "testbot")
        self.assertEqual(env["exit"], 0)
        self.assertEqual(env["session_id"], "s-1")
        self.assertIsInstance(env["duration_ms"], int)

    def test_the_envelope_carries_the_runtimes_usage_verbatim(self):
        """The host records tokens from this and nothing else. Renaming a
        counter on the way out would make its ledger silently wrong."""
        self.run_turn(f"cat > {self.sink}")
        self.assertEqual(self.envelope()["usage"], USAGE)

    def test_it_is_one_line_of_json(self):
        """Hosts append these to logs and pipe them through line tools."""
        self.run_turn(f"cat > {self.sink}")
        with open(self.sink, encoding="utf-8") as f:
            self.assertEqual(len(f.read().strip().splitlines()), 1)

    def test_a_failing_hook_does_not_cost_the_reply(self):
        tg = self.run_turn("exit 9")
        self.assertIn("not much", [t for _, t in tg.sent])

    def test_a_missing_hook_does_not_cost_the_reply(self):
        tg = self.run_turn("/nonexistent/hook")
        self.assertIn("not much", [t for _, t in tg.sent])

    def test_a_hanging_hook_does_not_cost_the_reply(self):
        with mock.patch.object(core, "DEFAULT_HOOK_TIMEOUT_SECS", 0.5):
            tg = self.run_turn("sleep 30")
        self.assertIn("not much", [t for _, t in tg.sent])

    def test_hook_stdout_is_ignored_not_sent(self):
        """It is a recorder, not a second voice."""
        tg = self.run_turn("echo I-SHOULD-NOT-APPEAR")
        self.assertNotIn("I-SHOULD-NOT-APPEAR",
                         " ".join(t for _, t in tg.sent))

    def test_no_hook_configured_is_fine(self):
        self.assertIsNone(helpers.write_config(self.dir)["post_turn_hook"])


class IncompleteTurns(unittest.TestCase):
    """A host's record has to be complete, or its history quietly loses
    the turns that went wrong — the ones most worth seeing."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.sink = os.path.join(self.dir, "envelope.json")

    def run_subtype(self, subtype):
        cfg = helpers.write_config(
            self.dir, post_turn_hook=f'"cat > {self.sink}"')
        tg = helpers.FakeTg()
        daemon = helpers.make_daemon(cfg, tg)
        with mock.patch.object(core, "stream_claude",
                               lambda *a, **k: {"subtype": subtype}):
            daemon.handle_message(helpers.text_msg("hi"))
        with open(self.sink, encoding="utf-8") as f:
            return json.load(f)

    def test_a_timed_out_turn_is_still_recorded(self):
        env = self.run_subtype("timeout")
        self.assertEqual(env["exit"], "timeout")
        self.assertEqual(env["prompt"], "hi")
        self.assertEqual(env["usage"], {})   # nothing was generated

    def test_a_crashed_turn_is_still_recorded(self):
        env = self.run_subtype("crash")
        self.assertEqual(env["exit"], "crash")
        self.assertEqual(env["prompt"], "hi")
