"""The reset seam: telling the host project that context is gone.

A host project usually keeps its own record of the conversation — a
database behind a web UI, say — and that record outlives any single
runtime session. So when the context is cleared, the host has a row of
messages the model no longer remembers, and no way to know it.

That matters most when the owner uses two surfaces. They clear on their
phone, open the web UI on a desk, and see the whole conversation sitting
there looking answerable. Nothing marks where memory actually begins.

`reset_hook` closes that. It fires whenever context is cleared, carries
what happened, and — like `post_turn_hook` — is fire-and-forget: the
reset has ALREADY happened by the time it runs, so a broken hook must
never turn a successful clear into an error the owner sees.

The other direction is the same shared file read the other way. The
session file has two writers when a host project has its own chat surface,
and a cached copy from daemon start is stale the moment the other one
writes. Re-reading it under the turn lock is what makes a clear performed
elsewhere actually stick.
"""
import json
import os
import tempfile
import unittest
from unittest import mock

import helpers
import gidoon as core

D = helpers.DAEMON


def _sink(tmpdir, name="hook-in.json"):
    """A hook that just records the envelope it was handed."""
    path = os.path.join(tmpdir, name)
    return path, f'cat > {path}'


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class Fires(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def _daemon(self, **over):
        cfg = helpers.write_config(self.dir, **over)
        self.tg = helpers.FakeTg()
        return helpers.make_daemon(cfg, self.tg)

    def test_clearing_context_tells_the_host(self):
        path, hook = _sink(self.dir)
        daemon = self._daemon(reset_hook=f'"{hook}"')
        daemon.state["session_id"] = "s-old"
        daemon.reset_context("clear")
        self.assertTrue(os.path.exists(path), "reset_hook never ran")

    def test_the_envelope_names_the_instance_and_what_happened(self):
        path, hook = _sink(self.dir)
        daemon = self._daemon(reset_hook=f'"{hook}"')
        daemon.state["session_id"] = "s-old"
        daemon.reset_context("clear")
        env = _read_json(path)
        self.assertEqual(env["instance"], "testbot")
        self.assertEqual(env["source"], "clear")

    def test_it_carries_the_session_it_just_ended(self):
        """The id is already dead — that is the point. The host is being
        told which conversation to draw the line under, and after the
        reset there is no other way to name it."""
        path, hook = _sink(self.dir)
        daemon = self._daemon(reset_hook=f'"{hook}"')
        daemon.state["session_id"] = "s-old"
        daemon.reset_context("clear")
        env = _read_json(path)
        self.assertEqual(env["session_id"], "s-old")

    def test_it_says_whether_anything_was_actually_forgotten(self):
        """A clear with no session behind it forgot nothing. The host may
        want to skip its bookkeeping entirely — so it gets told, rather
        than having to infer it from a null id."""
        path, hook = _sink(self.dir)
        daemon = self._daemon(reset_hook=f'"{hook}"')
        daemon.state["session_id"] = "s-old"
        daemon.reset_context("clear")
        self.assertIs(
            _read_json(path)["had_session"], True)

        os.remove(path)
        daemon.state["session_id"] = None
        daemon.reset_context("clear")
        self.assertIs(
            _read_json(path)["had_session"], False)

    def test_telegrams_own_handshake_fires_it_too(self):
        """/start clears context exactly as /clear does, so the host's
        record needs the same mark. gidoon reports the source and lets the
        host decide; deciding here would bake one host's policy in."""
        path, hook = _sink(self.dir)
        daemon = self._daemon(reset_hook=f'"{hook}"')
        daemon.state["session_id"] = "s-old"
        daemon.reset_context("start")
        env = _read_json(path)
        self.assertEqual(env["source"], "start")

    def test_the_hook_runs_after_the_session_is_already_gone(self):
        """Ordering, not decoration: a hook that reads the session file
        must see the cleared state. Otherwise a host that mirrors the file
        would copy the id back."""
        path = os.path.join(self.dir, "seen.json")
        daemon = self._daemon(
            reset_hook=f'"cat {os.path.join(self.dir, "testbot-session.json")}'
                       f' > {path}"')
        daemon.state["session_id"] = "s-old"
        daemon.reset_context("clear")
        self.assertIsNone(
            _read_json(path)["session_id"])


class NeverCosts(unittest.TestCase):
    """The reset already happened. Nothing here may take it back."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def _reset(self, **over):
        cfg = helpers.write_config(self.dir, **over)
        tg = helpers.FakeTg()
        daemon = helpers.make_daemon(cfg, tg)
        daemon.state["session_id"] = "s-old"
        daemon.reset_context("clear")
        return daemon, tg

    def test_no_hook_configured_is_the_normal_case(self):
        daemon, tg = self._reset()
        self.assertTrue(any("Fresh" in t for _, t in tg.sent), tg.sent)

    def test_a_hook_that_fails_still_leaves_the_owner_confirmed(self):
        daemon, tg = self._reset(reset_hook='"exit 3"')
        self.assertTrue(any("Fresh" in t for _, t in tg.sent), tg.sent)
        self.assertIsNone(daemon.state["session_id"])

    def test_a_hook_that_does_not_exist_is_survivable(self):
        daemon, tg = self._reset(reset_hook='"no-such-command-anywhere"')
        self.assertTrue(any("Fresh" in t for _, t in tg.sent), tg.sent)

    def test_a_hanging_hook_cannot_wedge_the_daemon(self):
        with mock.patch.object(core, "DEFAULT_HOOK_TIMEOUT_SECS", 0.3):
            daemon, tg = self._reset(reset_hook='"sleep 30"')
        self.assertTrue(any("Fresh" in t for _, t in tg.sent), tg.sent)

    def test_the_failure_is_logged_rather_than_swallowed(self):
        cfg = helpers.write_config(self.dir, reset_hook='"exit 3"')
        daemon = helpers.make_daemon(cfg, helpers.FakeTg())
        daemon.reset_context("clear")
        log = _read_text(cfg["log_path"])
        self.assertIn("reset_hook", log)


class SharedSessionFile(unittest.TestCase):
    """Two writers, one file.

    When the host project has its own chat surface, it clears context by
    nulling session_id in this same file. gidoon loads that file once at
    startup, so without a re-read its cached id outlives the clear — and
    the next save_state writes the dead id back, silently undoing it.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def _clear_externally(self, cfg, **extra):
        """What the host's web UI does: null the session, keep the rest."""
        path = cfg["session_path"]
        state = _read_json(path)
        state["session_id"] = None
        state.update(extra)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f)

    def test_a_clear_performed_elsewhere_is_honoured(self):
        cfg = helpers.write_config(self.dir)
        core.save_session(cfg["session_path"],
                          {"offset": 7, "session_id": "s-old"})
        daemon = helpers.make_daemon(cfg, helpers.FakeTg())
        self._clear_externally(cfg)

        resumed = []

        def fake_stream(prompt, sid, on_event, **kw):
            resumed.append(sid)
            return {"type": "result", "result": "ok", "session_id": "s-new"}

        with mock.patch.object(core, "stream_claude", fake_stream):
            daemon.handle_message(helpers.text_msg("hi"))
        self.assertEqual(resumed, [None],
                         "resumed a session the owner cleared elsewhere")

    def test_it_does_not_rewind_our_own_place_in_the_update_stream(self):
        """Only gidoon writes offset, and its in-memory value can be ahead
        of disk. Adopting a whole foreign dict would replay updates and
        answer the same message twice."""
        cfg = helpers.write_config(self.dir)
        core.save_session(cfg["session_path"],
                          {"offset": 7, "session_id": "s-old"})
        daemon = helpers.make_daemon(cfg, helpers.FakeTg())
        daemon.state["offset"] = 99
        self._clear_externally(cfg, offset=7)

        def fake_stream(prompt, sid, on_event, **kw):
            return {"type": "result", "result": "ok", "session_id": "s-new"}

        with mock.patch.object(core, "stream_claude", fake_stream):
            daemon.handle_message(helpers.text_msg("hi"))
        self.assertEqual(daemon.state["offset"], 99)

    def test_a_session_started_elsewhere_is_picked_up(self):
        """The same read, the useful direction: the host answered a turn on
        its own surface, so continuing THAT conversation is what 'same
        conversation on both surfaces' means."""
        cfg = helpers.write_config(self.dir)
        core.save_session(cfg["session_path"],
                          {"offset": 1, "session_id": None})
        daemon = helpers.make_daemon(cfg, helpers.FakeTg())
        state = _read_json(cfg["session_path"])
        state["session_id"] = "s-from-the-web-ui"
        with open(cfg["session_path"], "w", encoding="utf-8") as f:
            json.dump(state, f)

        resumed = []

        def fake_stream(prompt, sid, on_event, **kw):
            resumed.append(sid)
            return {"type": "result", "result": "ok", "session_id": "s-x"}

        with mock.patch.object(core, "stream_claude", fake_stream):
            daemon.handle_message(helpers.text_msg("hi"))
        self.assertEqual(resumed, ["s-from-the-web-ui"])

    def test_a_missing_or_corrupt_file_leaves_us_as_we_were(self):
        """The re-read must not be a new way to lose a live session."""
        cfg = helpers.write_config(self.dir)
        daemon = helpers.make_daemon(cfg, helpers.FakeTg())
        daemon.state["session_id"] = "s-live"
        with open(cfg["session_path"], "w", encoding="utf-8") as f:
            f.write("{not json")

        resumed = []

        def fake_stream(prompt, sid, on_event, **kw):
            resumed.append(sid)
            return {"type": "result", "result": "ok", "session_id": "s-live"}

        with mock.patch.object(core, "stream_claude", fake_stream):
            daemon.handle_message(helpers.text_msg("hi"))
        self.assertEqual(resumed, ["s-live"])


class Config(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def test_reset_hook_defaults_to_absent(self):
        cfg = helpers.write_config(self.dir)
        self.assertIsNone(cfg["reset_hook"])

    def test_it_is_an_accepted_key(self):
        """An unknown key is rejected outright, so this is the test that
        the config layer knows the seam exists at all."""
        cfg = helpers.write_config(self.dir, reset_hook='"true"')
        self.assertEqual(cfg["reset_hook"], "true")

    def test_an_empty_string_reads_as_absent(self):
        cfg = helpers.write_config(self.dir, reset_hook='""')
        self.assertIsNone(cfg["reset_hook"])


if __name__ == "__main__":
    unittest.main()
