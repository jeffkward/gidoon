"""The management subcommands: `gidoon uninstall` (per instance or --all),
`gidoon update` (pull + restart this clone's jobs), and the usage text shown
for a bare `gidoon` / `--help`. launchctl and git are stubbed; nothing here
touches the real machine."""
import io
import os
import plistlib
import tempfile
import unittest
from unittest import mock

import helpers
import gidoon as core

D = helpers.DAEMON


class CommandCase(unittest.TestCase):
    """A fake ~/.config/gidoon + ~/Library/LaunchAgents, with launchctl
    calls recorded instead of run."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conf = os.path.join(self.tmp.name, "conf")
        self.agents = os.path.join(self.tmp.name, "LaunchAgents")
        os.makedirs(self.conf)
        os.makedirs(self.agents)
        self.launchctl = []
        patches = [
            mock.patch.object(core, "CONFIG_DIR", self.conf),
            mock.patch.object(D, "LAUNCH_AGENTS", self.agents),
            mock.patch.object(D, "_launchctl",
                              lambda *a: self.launchctl.append(a) or 0),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def install(self, name, repo="/repo/gidoon"):
        """Fake an installed instance: every file it owns + a plist."""
        for suffix in core.INSTANCE_SUFFIXES:
            with open(os.path.join(self.conf, name + suffix), "w") as f:
                f.write("x")
        plist = os.path.join(self.agents, f"com.gidoon.{name}.plist")
        with open(plist, "wb") as f:
            plistlib.dump({"Label": f"com.gidoon.{name}",
                           "ProgramArguments": ["/usr/bin/python3",
                                                f"{repo}/bin/gidoon",
                                                "--config",
                                                f"{self.conf}/{name}.toml"]},
                          f)
        return plist


class Uninstall(CommandCase):
    def test_removes_files_plist_and_boots_out_the_job(self):
        plist = self.install("work")
        D.uninstall_main(["work", "--yes"])
        self.assertEqual(core.instance_files("work", self.conf), [])
        self.assertFalse(os.path.exists(plist))
        self.assertTrue(any("bootout" in a for a in self.launchctl))
        self.assertTrue(any("com.gidoon.work" in " ".join(a)
                            for a in self.launchctl))

    def test_leaves_other_instances_alone(self):
        self.install("work")
        other_plist = self.install("play")
        D.uninstall_main(["work", "--yes"])
        self.assertEqual(len(core.instance_files("play", self.conf)),
                         len(core.INSTANCE_SUFFIXES))
        self.assertTrue(os.path.exists(other_plist))

    def test_unknown_instance_exits_nonzero(self):
        with self.assertRaises(SystemExit):
            D.uninstall_main(["ghost", "--yes"])

    def test_all_removes_every_instance(self):
        self.install("work")
        self.install("play")
        D.uninstall_main(["--all", "--yes"])
        self.assertEqual(core.list_instances(self.conf), [])
        self.assertEqual(os.listdir(self.agents), [])

    def test_all_with_nothing_installed_exits_nonzero(self):
        with self.assertRaises(SystemExit):
            D.uninstall_main(["--all", "--yes"])

    def test_requires_an_instance_or_all(self):
        with self.assertRaises(SystemExit):
            D.uninstall_main([])

    def test_declining_the_prompt_removes_nothing(self):
        plist = self.install("work")
        with mock.patch("sys.stdin", io.StringIO("n\n")):
            D.uninstall_main(["work"])
        self.assertEqual(len(core.instance_files("work", self.conf)),
                         len(core.INSTANCE_SUFFIXES))
        self.assertTrue(os.path.exists(plist))
        self.assertEqual(self.launchctl, [])

    def test_confirming_the_prompt_removes(self):
        self.install("work")
        with mock.patch("sys.stdin", io.StringIO("y\n")):
            D.uninstall_main(["work"])
        self.assertEqual(core.instance_files("work", self.conf), [])


class UninstallSymlink(CommandCase):
    """The `gidoon` command symlink belongs to the CLONE, not to any one
    instance: it only goes away when the last instance does."""

    def setUp(self):
        super().setUp()
        self.local_bin = os.path.join(self.tmp.name, "bin")
        os.makedirs(self.local_bin)
        self.link = os.path.join(self.local_bin, "gidoon")
        p = mock.patch.object(D, "LOCAL_BIN", self.local_bin)
        p.start()
        self.addCleanup(p.stop)

    def link_to(self, target):
        os.symlink(target, self.link)

    def test_removed_when_the_last_instance_goes(self):
        self.install("work")
        self.link_to(os.path.join(D.REPO_ROOT, "bin", "gidoon"))
        D.uninstall_main(["work", "--yes"])
        self.assertFalse(os.path.lexists(self.link))

    def test_kept_while_another_instance_remains(self):
        self.install("work")
        self.install("play")
        self.link_to(os.path.join(D.REPO_ROOT, "bin", "gidoon"))
        D.uninstall_main(["work", "--yes"])
        self.assertTrue(os.path.lexists(self.link))

    def test_another_clones_symlink_is_left_alone(self):
        self.install("work")
        self.link_to("/some/other/clone/bin/gidoon")
        D.uninstall_main(["work", "--yes"])
        self.assertTrue(os.path.lexists(self.link))

    def test_a_real_file_named_gidoon_is_never_deleted(self):
        self.install("work")
        with open(self.link, "w") as f:
            f.write("someone else's binary")
        D.uninstall_main(["work", "--yes"])
        self.assertTrue(os.path.exists(self.link))

    def test_no_symlink_is_not_an_error(self):
        self.install("work")
        D.uninstall_main(["work", "--yes"])
        self.assertEqual(core.list_instances(self.conf), [])


class Update(CommandCase):
    def test_pulls_then_restarts_only_this_clones_jobs(self):
        self.install("mine", repo=D.REPO_ROOT)
        self.install("theirs", repo="/somewhere/else/gidoon")
        calls = []
        with mock.patch.object(D, "_git", lambda *a: calls.append(a) or 0):
            D.update_main([])
        self.assertTrue(any("pull" in a for a in calls))
        restarted = [a for a in self.launchctl if "kickstart" in a]
        joined = " ".join(" ".join(a) for a in restarted)
        self.assertIn("com.gidoon.mine", joined)
        self.assertNotIn("com.gidoon.theirs", joined)

    def test_failed_pull_stops_before_restarting_anything(self):
        self.install("mine", repo=D.REPO_ROOT)
        with mock.patch.object(D, "_git", lambda *a: 1):
            with self.assertRaises(SystemExit):
                D.update_main([])
        self.assertEqual(self.launchctl, [])


class Usage(unittest.TestCase):
    def run_main(self, argv):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            try:
                D.main(argv)
            except SystemExit:
                pass
        return out.getvalue()

    def test_bare_invocation_lists_every_command(self):
        text = self.run_main([])
        for word in ("send", "uninstall", "update", "--config"):
            self.assertIn(word, text)

    def test_help_flag_shows_the_same_usage(self):
        self.assertEqual(self.run_main(["--help"]), self.run_main([]))

    def test_unknown_command_is_reported(self):
        out = io.StringIO()
        err = io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            with self.assertRaises(SystemExit):
                D.main(["frobnicate"])
        self.assertIn("frobnicate", out.getvalue() + err.getvalue())


if __name__ == "__main__":
    unittest.main()
