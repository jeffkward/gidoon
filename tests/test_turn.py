"""Cmd construction + the stream_claude loop against a stub claude binary.
The stub's behavior is keyed off the prompt text, which also proves the
prompt travels over stdin (never argv)."""
import os
import stat
import tempfile
import unittest

import helpers  # noqa: F401
import gidoon as core

STUB = """#!/usr/bin/env python3
import json, sys, time
prompt = sys.stdin.read()
if prompt == "sleep":
    time.sleep(30)
    sys.exit(0)
if prompt == "noresult":
    print(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "hi"}]}}), flush=True)
    sys.exit(1)
if prompt == "resumefail":
    sys.stderr.write("No conversation found with session ID: xyz\\n")
    sys.exit(1)
print(json.dumps({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "name": "Bash", "input": {}}]}}), flush=True)
print(json.dumps({"type": "result", "result": "echo:" + prompt,
                  "session_id": "stub-session", "num_turns": 1,
                  "total_cost_usd": 0.001}), flush=True)
"""


class BuildCmd(unittest.TestCase):
    def test_minimal_no_optional_flags(self):
        cmd = core.build_cmd("/x/claude")
        self.assertEqual(cmd, ["/x/claude", "-p", "--output-format",
                               "stream-json", "--verbose"])
        self.assertNotIn("--permission-mode", cmd)
        self.assertNotIn("--allowedTools", cmd)
        self.assertNotIn("--model", cmd)
        self.assertNotIn("--resume", cmd)

    def test_resume(self):
        cmd = core.build_cmd("/x/claude", session_id="abc123")
        self.assertEqual(cmd[:4], ["/x/claude", "-p", "--resume", "abc123"])

    def test_permission_mode_flag_only_when_set(self):
        cmd = core.build_cmd("/x/claude", permission_mode="bypassPermissions")
        i = cmd.index("--permission-mode")
        self.assertEqual(cmd[i + 1], "bypassPermissions")
        self.assertNotIn("--permission-mode",
                         core.build_cmd("/x/claude", permission_mode=None))

    def test_allowed_tools_joined_only_when_nonempty(self):
        cmd = core.build_cmd("/x/claude", allowed_tools=["A", "B"])
        i = cmd.index("--allowedTools")
        self.assertEqual(cmd[i + 1], "A,B")
        self.assertNotIn("--allowedTools",
                         core.build_cmd("/x/claude", allowed_tools=[]))

    def test_model_flag_only_when_set(self):
        cmd = core.build_cmd("/x/claude", model="opus")
        i = cmd.index("--model")
        self.assertEqual(cmd[i + 1], "opus")
        self.assertNotIn("--model", core.build_cmd("/x/claude", model=None))

    def test_system_prompt_flag_only_when_set(self):
        cmd = core.build_cmd("/x/claude", system_prompt="be brief")
        i = cmd.index("--append-system-prompt")
        self.assertEqual(cmd[i + 1], "be brief")
        self.assertNotIn("--append-system-prompt",
                         core.build_cmd("/x/claude", system_prompt=None))

    def test_prompt_never_in_argv(self):
        # build_cmd doesn't even accept a prompt — the argv is fully
        # determined by flags; the prompt goes over stdin in stream_claude.
        cmd = core.build_cmd("/x/claude", session_id="s",
                             permission_mode="plan",
                             allowed_tools=["A"], model="m")
        for arg in cmd:
            self.assertTrue(arg.startswith("-") or arg in
                            ("/x/claude", "s", "stream-json", "plan", "A",
                             "m"))


class StreamClaude(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stub = os.path.join(self.tmp.name, "claude-stub")
        with open(self.stub, "w", encoding="utf-8") as f:
            f.write(STUB)
        os.chmod(self.stub, os.stat(self.stub).st_mode | stat.S_IEXEC)
        self.events = []

    def run_turn(self, prompt, session_id=None, timeout_secs=15):
        return core.stream_claude(
            prompt, session_id, self.events.append, cwd=self.tmp.name,
            timeout_secs=timeout_secs, claude_bin=self.stub)

    def test_normal_turn_returns_result_and_dispatches_events(self):
        result = self.run_turn("hello")
        self.assertEqual(result.get("result"), "echo:hello")
        self.assertEqual(result.get("session_id"), "stub-session")
        types = [e.get("type") for e in self.events]
        self.assertIn("assistant", types)
        self.assertIn("result", types)

    def test_prompt_arrives_via_stdin_even_if_flag_shaped(self):
        # A message starting with '-' must reach the child as data, not be
        # parsed as a CLI flag — proven by it round-tripping through stdin.
        result = self.run_turn("--dangerously-do-things")
        self.assertEqual(result.get("result"), "echo:--dangerously-do-things")

    def test_resume_failed_marker(self):
        result = self.run_turn("resumefail", session_id="dead-session")
        self.assertEqual(result, {"subtype": "resume_failed"})

    def test_no_result_without_session_is_crash(self):
        # Same stderr signature but NO session_id being resumed → crash,
        # not resume_failed.
        result = self.run_turn("noresult")
        self.assertEqual(result, {"subtype": "crash"})

    def test_timeout_marker(self):
        result = self.run_turn("sleep", timeout_secs=1)
        self.assertEqual(result, {"subtype": "timeout"})


if __name__ == "__main__":
    unittest.main()
