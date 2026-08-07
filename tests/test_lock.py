"""turn_lock — serializing turns with another process on the same session.

A host project may run its own turns against the same claude session this
daemon resumes (a web UI, a CLI). Two `claude -p --resume` processes on one
session at the same time corrupts the conversation, so both sides have to
contend on something.

The algorithm is a CONTRACT with that other process, not an implementation
detail: **atomic mkdir on a shared path, and a lock is stale after 900
seconds.** Any process in any language can join by obeying those two rules.
Change either one and turns can overlap, which is the failure this exists
to prevent — so both are pinned below.

(The poll interval is NOT contractual: how often a waiter retries is its
own business.)
"""
import os
import tempfile
import time
import unittest
from unittest import mock

import helpers
import gidoon as core

D = helpers.DAEMON


class TheContract(unittest.TestCase):
    """The two numbers and one syscall the other side must agree on."""

    def test_stale_threshold_is_fifteen_minutes(self):
        self.assertEqual(core.DEFAULT_STALE_LOCK_SECS, 900)

    def test_the_lock_is_a_directory(self):
        """mkdir, because it is atomic on APFS and ext4 with no library,
        no daemon, and no language in common required."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, ".turn-lock")
        core.acquire_turn_lock(path)
        self.assertTrue(os.path.isdir(path))
        core.release_turn_lock(path)


class TurnLock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lock = os.path.join(self.tmp.name, ".turn-lock")

    def test_acquire_then_release_round_trips(self):
        core.acquire_turn_lock(self.lock)
        self.assertTrue(os.path.isdir(self.lock))
        core.release_turn_lock(self.lock)
        self.assertFalse(os.path.exists(self.lock))

    def test_a_second_holder_times_out(self):
        core.acquire_turn_lock(self.lock)
        with self.assertRaises(TimeoutError):
            core.acquire_turn_lock(self.lock, timeout=0.5)

    def test_a_stale_lock_is_broken(self):
        """The other side crashed mid-turn. Without this the mouth is mute
        until someone notices and removes a directory by hand."""
        os.mkdir(self.lock)
        old = time.time() - (core.DEFAULT_STALE_LOCK_SECS + 60)
        os.utime(self.lock, (old, old))
        core.acquire_turn_lock(self.lock, timeout=2)
        self.assertTrue(os.path.isdir(self.lock))

    def test_a_lock_just_under_the_threshold_is_respected(self):
        """The other half of the stale rule — break too eagerly and two
        turns run at once, which is the whole thing we are avoiding."""
        os.mkdir(self.lock)
        recent = time.time() - (core.DEFAULT_STALE_LOCK_SECS - 60)
        os.utime(self.lock, (recent, recent))
        with self.assertRaises(TimeoutError):
            core.acquire_turn_lock(self.lock, timeout=0.5)

    def test_release_of_a_lock_we_do_not_hold_is_quiet(self):
        core.release_turn_lock(self.lock)          # never existed

    def test_context_manager_releases_even_on_error(self):
        with self.assertRaises(ValueError):
            with core.turn_lock(self.lock):
                raise ValueError("boom")
        self.assertFalse(os.path.exists(self.lock))

    def test_context_manager_is_a_noop_when_unconfigured(self):
        """The single-owner case — no host project — pays nothing."""
        for unset in (None, ""):
            with core.turn_lock(unset):
                pass
            self.assertFalse(os.path.exists(self.lock))

    def test_parent_directories_are_created(self):
        deep = os.path.join(self.tmp.name, "a", "b", ".turn-lock")
        with core.turn_lock(deep):
            self.assertTrue(os.path.isdir(deep))

    def test_the_default_timeout_is_patchable(self):
        """Bound at call time, not def time, so a test can shorten the
        wait without the daemon needing a config key for it."""
        core.acquire_turn_lock(self.lock)
        with mock.patch.object(core, "DEFAULT_LOCK_TIMEOUT_SECS", 0.5):
            started = time.time()
            with self.assertRaises(TimeoutError):
                core.acquire_turn_lock(self.lock)
            self.assertLess(time.time() - started, 10)


class ConfigCarriesTheLock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def test_absent_means_none(self):
        self.assertIsNone(helpers.write_config(self.dir)["turn_lock"])

    def test_set_and_expanded(self):
        cfg = helpers.write_config(self.dir, turn_lock='"~/x/.turn-lock"')
        self.assertEqual(cfg["turn_lock"],
                         os.path.expanduser("~/x/.turn-lock"))


class DaemonContention(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def test_a_busy_lock_declines_without_running_claude(self):
        lock = os.path.join(self.dir, ".turn-lock")
        os.mkdir(lock)                              # someone else holds it
        cfg = helpers.write_config(self.dir, turn_lock=f'"{lock}"')
        tg = helpers.FakeTg()
        daemon = helpers.make_daemon(cfg, tg)

        def must_not_run(*a, **kw):
            raise AssertionError("ran a turn while the lock was held")

        with mock.patch.object(core, "stream_claude", must_not_run), \
                mock.patch.object(core, "DEFAULT_LOCK_TIMEOUT_SECS", 0.5):
            daemon.handle_message(helpers.text_msg("hi"))
        self.assertTrue(any("moment" in t for _, t in tg.sent),
                        f"no wait-and-retry reply in {tg.sent}")

    def test_an_unconfigured_instance_takes_no_lock(self):
        cfg = helpers.write_config(self.dir)
        tg = helpers.FakeTg()
        daemon = helpers.make_daemon(cfg, tg)

        def fake_stream(p, sid, on_event, **kw):
            return {"type": "result", "result": "ok", "session_id": "s-1"}

        with mock.patch.object(core, "stream_claude", fake_stream):
            daemon.handle_message(helpers.text_msg("hi"))
        self.assertIn("ok", [t for _, t in tg.sent])
        self.assertFalse(os.path.exists(
            os.path.join(self.dir, ".turn-lock")))
