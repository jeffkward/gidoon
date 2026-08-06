"""Session save/load round-trip and the costs receipt line shape."""
import json
import os
import tempfile
import unittest

import helpers  # noqa: F401
import gidoon as core


class Session(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "x-session.json")

    def test_missing_file_gives_fresh_state(self):
        self.assertEqual(core.load_session(self.path),
                         {"offset": 0, "session_id": None})

    def test_round_trip(self):
        state = {"offset": 42, "session_id": "abc-123"}
        core.save_session(self.path, state)
        self.assertEqual(core.load_session(self.path), state)

    def test_corrupt_file_gives_fresh_state(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(core.load_session(self.path),
                         {"offset": 0, "session_id": None})

    def test_save_is_atomic_no_tmp_left_behind(self):
        core.save_session(self.path, {"offset": 1, "session_id": None})
        self.assertFalse(os.path.exists(self.path + ".tmp"))


class Costs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "x-costs.jsonl")

    def read_lines(self):
        with open(self.path, encoding="utf-8") as f:
            return [json.loads(line) for line in f]

    def test_line_shape(self):
        result = {"num_turns": 3, "total_cost_usd": 0.0421,
                  "session_id": "s", "result": "secret content",
                  "usage": {"input_tokens": 9, "output_tokens": 728,
                            "cache_creation_input_tokens": 12181,
                            "cache_read_input_tokens": 17900,
                            "service_tier": "standard"}}
        core.append_cost(self.path, result, 1234, 0)
        (line,) = self.read_lines()
        self.assertEqual(set(line),
                         {"ts", "duration_ms", "num_turns", "input_tokens",
                          "output_tokens", "cache_creation_input_tokens",
                          "cache_read_input_tokens", "exit"})
        self.assertEqual(line["duration_ms"], 1234)
        self.assertEqual(line["num_turns"], 3)
        self.assertEqual(line["input_tokens"], 9)
        self.assertEqual(line["output_tokens"], 728)
        self.assertEqual(line["cache_creation_input_tokens"], 12181)
        self.assertEqual(line["cache_read_input_tokens"], 17900)
        self.assertEqual(line["exit"], 0)
        # A receipt, never a transcript: no message content leaks in.
        self.assertNotIn("secret content", json.dumps(line))

    def test_no_usd_recorded_even_when_the_result_reports_it(self):
        # Dollars depend on the reader's plan (subscription vs API), so the
        # receipt counts tokens and stays out of that argument.
        core.append_cost(self.path, {"total_cost_usd": 1.366392}, 1, 0)
        (line,) = self.read_lines()
        self.assertNotIn("total_cost_usd", line)
        self.assertNotIn("1.366392", json.dumps(line))

    def test_timeout_line(self):
        core.append_cost(self.path, {}, 600000, "timeout")
        (line,) = self.read_lines()
        self.assertEqual(line["exit"], "timeout")
        self.assertIsNone(line["num_turns"])
        self.assertIsNone(line["input_tokens"])
        self.assertIsNone(line["output_tokens"])

    def test_appends(self):
        core.append_cost(self.path, {}, 1, 0)
        core.append_cost(self.path, {}, 2, 1)
        self.assertEqual([l["duration_ms"] for l in self.read_lines()],
                         [1, 2])


if __name__ == "__main__":
    unittest.main()
