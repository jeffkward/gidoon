"""log_token_usage: whether this instance keeps its own token log.

ON by default, because a standalone mouth is the only thing keeping books.
A HOST project that embeds gidoon records turns itself (post_turn_hook)
and turns this off — otherwise every turn lands in two ledgers and one of
them rots.
"""
import json
import os
import tempfile
import unittest
from unittest import mock

import helpers
import gidoon as core


class ConfigFlag(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def test_absent_means_on(self):
        """A standalone instance keeps books without being asked."""
        self.assertIs(helpers.write_config(self.dir)["log_token_usage"],
                      True)

    def test_can_be_turned_off(self):
        cfg = helpers.write_config(self.dir, log_token_usage="false")
        self.assertIs(cfg["log_token_usage"], False)

    def test_must_be_a_boolean(self):
        with self.assertRaises(core.ConfigError):
            helpers.write_config(self.dir, log_token_usage='"sometimes"')


class WhatTheDaemonWrites(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def turn(self, **overrides):
        cfg = helpers.write_config(self.dir, **overrides)
        tg = helpers.FakeTg()
        daemon = helpers.make_daemon(cfg, tg)

        def fake_stream(p, sid, on_event, **kw):
            return {"type": "result", "result": "ok", "session_id": "s-1",
                    "num_turns": 1,
                    "usage": {"input_tokens": 7, "output_tokens": 42,
                              "cache_read_input_tokens": 900,
                              "cache_creation_input_tokens": 0}}

        with mock.patch.object(core, "stream_claude", fake_stream):
            daemon.handle_message(helpers.text_msg("hi"))
        return daemon, tg

    def test_on_by_default_writes_the_four_counters(self):
        daemon, tg = self.turn()
        with open(daemon.cfg["usage_path"], encoding="utf-8") as f:
            (line,) = [json.loads(l) for l in f]
        self.assertEqual(line["input_tokens"], 7)
        self.assertEqual(line["output_tokens"], 42)
        self.assertEqual(line["cache_read_input_tokens"], 900)
        self.assertEqual(line["cache_creation_input_tokens"], 0)

    def test_off_writes_no_file_at_all(self):
        """Not an empty file — no file. The host owns the books."""
        daemon, tg = self.turn(log_token_usage="false")
        self.assertFalse(os.path.exists(daemon.cfg["usage_path"]))

    def test_off_still_answers_normally(self):
        """Bookkeeping is not the point of the turn."""
        daemon, tg = self.turn(log_token_usage="false")
        self.assertIn("ok", [t for _, t in tg.sent])


class LegacyFiles(unittest.TestCase):
    """The log was <name>-costs.jsonl before 2026-08-07. `uninstall` must
    still clean those up, or renaming the file quietly orphans one per
    instance on every machine that ran the old version."""

    def test_uninstall_still_finds_the_old_costs_file(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for name in ("work.toml", "work-costs.jsonl", "work-usage.jsonl"):
            open(os.path.join(tmp.name, name), "w").close()
        found = {os.path.basename(p)
                 for p in core.instance_files("work", conf_dir=tmp.name)}
        self.assertIn("work-costs.jsonl", found)
        self.assertIn("work-usage.jsonl", found)
