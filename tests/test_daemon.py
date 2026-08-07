"""Daemon behavior against the FakeTg transport: allowlist filtering,
/new and /start handling, pre_turn_hook decline, the two-message turn UX
(status + answer) with stream_claude mocked, self-heal retry, and the
status renderer's characterized quirks."""
import json
import os
import stat
import tempfile
import unittest
from unittest import mock

import helpers
import gidoon as core

D = helpers.DAEMON


class DaemonCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def make(self, **cfg_overrides):
        cfg = helpers.write_config(self.dir, **cfg_overrides)
        tg = helpers.FakeTg()
        return helpers.make_daemon(cfg, tg), tg


class Allowlist(DaemonCase):
    def test_non_allowlisted_chat_is_ignored_but_offset_advances(self):
        daemon, tg = self.make()
        update = {"update_id": 5,
                  "message": helpers.text_msg("hi", chat_id=999)}
        daemon.handle_update(update)
        self.assertEqual(tg.sent, [])
        self.assertEqual(tg.reactions, [])
        self.assertEqual(daemon.state["offset"], 6)
        # persisted
        self.assertEqual(core.load_session(daemon.cfg["session_path"])
                         ["offset"], 6)

    def test_update_without_message_advances_offset(self):
        daemon, tg = self.make()
        daemon.handle_update({"update_id": 9})
        self.assertEqual(tg.sent, [])
        self.assertEqual(daemon.state["offset"], 10)


class Commands(DaemonCase):
    def test_new_resets_session(self):
        daemon, tg = self.make()
        daemon.state["session_id"] = "old-session"
        daemon.save_state()
        daemon.handle_message(helpers.text_msg("/new"))
        self.assertIsNone(daemon.state["session_id"])
        self.assertIsNone(core.load_session(daemon.cfg["session_path"])
                          ["session_id"])
        self.assertEqual(len(tg.sent), 1)
        self.assertIn("Fresh conversation", tg.sent[0][1])

    def test_start_is_ignored_entirely(self):
        daemon, tg = self.make()
        daemon.handle_message(helpers.text_msg("/start"))
        self.assertEqual(tg.sent, [])
        self.assertEqual(tg.reactions, [])

    def test_non_text_declined(self):
        daemon, tg = self.make()
        daemon.handle_message({"message_id": 1, "chat": {"id": 42},
                               "voice": {}})
        self.assertEqual(len(tg.sent), 1)
        self.assertIn("Text only", tg.sent[0][1])


class PreTurnHook(DaemonCase):
    def hook_script(self, body):
        path = os.path.join(self.dir, "hook.sh")
        with open(path, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n" + body)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        return path

    def test_false_hook_declines_with_default_message(self):
        daemon, tg = self.make(pre_turn_hook='"/usr/bin/false"')
        daemon.handle_message(helpers.text_msg("hello"))
        answers = [t for _, t in tg.sent]
        self.assertEqual(len(answers), 1)
        self.assertIn("declined", answers[0])

    def test_hook_stdout_becomes_the_reply_and_gets_stdin(self):
        path = self.hook_script(
            'read line\necho "capped: $line"\nexit 1\n')
        daemon, tg = self.make(pre_turn_hook=f'"{path}"')
        daemon.handle_message(helpers.text_msg("expensive ask"))
        self.assertEqual(tg.sent, [(42, "capped: expensive ask")])

    def test_zero_exit_hook_allows_the_turn(self):
        path = self.hook_script("exit 0\n")
        daemon, tg = self.make(pre_turn_hook=f'"{path}"')
        with mock.patch.object(core, "stream_claude",
                               return_value={"type": "result",
                                             "result": "ran"}):
            daemon.handle_message(helpers.text_msg("go"))
        self.assertIn("ran", [t for _, t in tg.sent])

    def test_broken_hook_fails_open(self):
        daemon, tg = self.make(
            pre_turn_hook='"/nonexistent/hook definitely-missing"')
        with mock.patch.object(core, "stream_claude",
                               return_value={"type": "result",
                                             "result": "ran"}):
            daemon.handle_message(helpers.text_msg("go"))
        self.assertIn("ran", [t for _, t in tg.sent])


class TurnFlow(DaemonCase):
    GOOD = {"type": "result", "result": "the answer",
            "session_id": "sess-9", "num_turns": 2,
            "total_cost_usd": 0.03,
            "usage": {"input_tokens": 11, "output_tokens": 402,
                      "cache_creation_input_tokens": 900,
                      "cache_read_input_tokens": 12000}}

    def test_two_message_ux_status_then_answer(self):
        daemon, tg = self.make(emoji='"🐻"')

        def fake_stream(prompt, session_id, on_event, **kw):
            on_event({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {}}]}})
            return dict(self.GOOD)

        with mock.patch.object(core, "stream_claude", fake_stream):
            daemon.handle_message(helpers.text_msg("do a thing"))

        # ack reaction fired
        self.assertEqual(len(tg.reactions), 1)
        # message 1: the status message, first render is the thinking face
        self.assertEqual(tg.sent[0][1], "🐻💭")
        # final status edit shows the completed tool + done face
        self.assertEqual(tg.edits[-1][2], "💻 Bash\n🐻 ✅")
        # message 2: the answer, fresh message (not an edit)
        self.assertEqual(tg.sent[1][1], "the answer")
        # session persisted
        self.assertEqual(daemon.state["session_id"], "sess-9")
        # token usage log
        with open(daemon.cfg["usage_path"], encoding="utf-8") as f:
            (line,) = [json.loads(l) for l in f]
        self.assertEqual(line["exit"], 0)
        self.assertEqual(line["num_turns"], 2)
        # tokens, not dollars — the result's USD figure never reaches disk
        self.assertEqual(line["input_tokens"], 11)
        self.assertEqual(line["output_tokens"], 402)
        self.assertEqual(line["cache_read_input_tokens"], 12000)
        self.assertNotIn("total_cost_usd", line)

    def test_resume_failed_drops_session_and_retries_fresh(self):
        daemon, tg = self.make()
        daemon.state["session_id"] = "dead"
        daemon.save_state()
        calls = []

        def fake_stream(prompt, session_id, on_event, **kw):
            calls.append(session_id)
            if session_id:
                return {"subtype": "resume_failed"}
            return dict(self.GOOD)

        with mock.patch.object(core, "stream_claude", fake_stream):
            daemon.handle_message(helpers.text_msg("hi"))
        self.assertEqual(calls, ["dead", None])
        self.assertEqual(daemon.state["session_id"], "sess-9")
        self.assertIn("the answer", [t for _, t in tg.sent])

    def test_timeout_reports_and_receipts(self):
        daemon, tg = self.make(timeout_secs="120")
        with mock.patch.object(core, "stream_claude",
                               return_value={"subtype": "timeout"}):
            daemon.handle_message(helpers.text_msg("hi"))
        self.assertIn("2-minute limit", tg.sent[-1][1])
        self.assertIn("⚠️", tg.edits[-1][2])
        with open(daemon.cfg["usage_path"], encoding="utf-8") as f:
            (line,) = [json.loads(l) for l in f]
        self.assertEqual(line["exit"], "timeout")

    def test_crash_reports(self):
        daemon, tg = self.make()
        with mock.patch.object(core, "stream_claude",
                               return_value={"subtype": "crash"}):
            daemon.handle_message(helpers.text_msg("hi"))
        self.assertIn("went wrong", tg.sent[-1][1])

    def test_config_posture_reaches_stream_claude(self):
        daemon, tg = self.make(
            permission_mode='"bypassPermissions"', model='"opus"',
            allowed_tools='["WebSearch"]')
        seen = {}

        def fake_stream(prompt, session_id, on_event, **kw):
            seen.update(kw)
            return dict(self.GOOD)

        with mock.patch.object(core, "stream_claude", fake_stream):
            daemon.handle_message(helpers.text_msg("hi"))
        self.assertEqual(seen["permission_mode"], "bypassPermissions")
        self.assertEqual(seen["model"], "opus")
        self.assertEqual(seen["allowed_tools"], ["WebSearch"])
        self.assertEqual(seen["cwd"], daemon.cfg["cwd"])
        self.assertEqual(seen["system_prompt"], core.DEFAULT_SYSTEM_PROMPT)


class SendLong(unittest.TestCase):
    def test_splits_at_4096(self):
        tg = helpers.FakeTg()
        D.send_long(tg, 42, "x" * 5000)
        self.assertEqual([len(t) for _, t in tg.sent], [4096, 904])

    def test_empty_becomes_placeholder(self):
        tg = helpers.FakeTg()
        D.send_long(tg, 42, "")
        self.assertEqual(tg.sent, [(42, "(empty reply)")])


class RenderStatus(unittest.TestCase):
    def test_running_absorbs_matching_last_run(self):
        # Bash just completed twice and Bash is running again → ONE face
        # line with the combined count, no near-duplicate stack.
        text = D.render_status("🐻", [("💻", "Bash"), ("💻", "Bash")],
                               "RUNNING", ("💻", "Bash"))
        self.assertEqual(text, "🐻💻 Bash… ×3")

    def test_different_running_tool_keeps_completed_lines(self):
        text = D.render_status("🐻", [("💻", "Bash")], "RUNNING",
                               ("📖", "Read"))
        self.assertEqual(text, "💻 Bash\n🐻📖 Read…")

    def test_overflow_middle_truncates_keeping_first_and_face(self):
        completed = [("💻", f"cmd-{i:04d}" + "x" * 120) for i in range(60)]
        text = D.render_status("🐻", completed, "DONE", None)
        self.assertLessEqual(len(text), D.TG_MAX)
        lines = text.split("\n")
        self.assertIn("cmd-0000", lines[0])          # first kept
        self.assertEqual(lines[-1], "🐻 ✅")          # face kept in full
        self.assertTrue(any("more) …" in l for l in lines))


class DrainBacklog(DaemonCase):
    def test_fresh_instance_skips_pending_updates(self):
        cfg = helpers.write_config(self.dir)
        tg = helpers.FakeTg(pending_updates=[
            {"update_id": 7, "message": helpers.text_msg("stale")},
            {"update_id": 8, "message": helpers.text_msg("staler")}])
        daemon = helpers.make_daemon(cfg, tg)
        daemon.drain_backlog()
        self.assertEqual(daemon.state["offset"], 9)
        self.assertEqual(tg.sent, [])  # nothing replayed
        self.assertEqual(tg.get_updates_calls, [(0, False)])

    def test_existing_instance_does_not_drain(self):
        cfg = helpers.write_config(self.dir)
        core.save_session(cfg["session_path"],
                          {"offset": 3, "session_id": None})
        tg = helpers.FakeTg()
        daemon = helpers.make_daemon(cfg, tg)
        daemon.drain_backlog()
        self.assertEqual(tg.get_updates_calls, [])


if __name__ == "__main__":
    unittest.main()
