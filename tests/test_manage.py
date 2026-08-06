"""Instance management helpers behind `gidoon uninstall` / `gidoon update`:
enumerating instances, the files that belong to one, and deciding which
launchd jobs belong to a given clone of the repo."""
import os
import plistlib
import tempfile
import unittest

import helpers  # noqa: F401  (sys.path setup)
import gidoon as core


class ListInstances(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def touch(self, name):
        open(os.path.join(self.dir, name), "w").close()

    def test_names_come_from_toml_files_sorted(self):
        self.touch("work.toml")
        self.touch("assistant.toml")
        self.assertEqual(core.list_instances(self.dir), ["assistant", "work"])

    def test_other_files_ignored(self):
        self.touch("work.toml")
        self.touch("work.env")
        self.touch("work-costs.jsonl")
        self.touch("notes.txt")
        self.assertEqual(core.list_instances(self.dir), ["work"])

    def test_empty_dir(self):
        self.assertEqual(core.list_instances(self.dir), [])

    def test_missing_dir(self):
        self.assertEqual(core.list_instances(
            os.path.join(self.dir, "nope")), [])


class InstanceFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def touch(self, name):
        path = os.path.join(self.dir, name)
        open(path, "w").close()
        return path

    def test_only_existing_files_returned(self):
        toml = self.touch("work.toml")
        env = self.touch("work.env")
        self.assertEqual(core.instance_files("work", self.dir), [toml, env])

    def test_all_five_kinds(self):
        made = [self.touch(n) for n in ("work.toml", "work.env",
                                        "work-session.json",
                                        "work-costs.jsonl", "work.log")]
        self.assertEqual(sorted(core.instance_files("work", self.dir)),
                         sorted(made))

    def test_other_instances_untouched(self):
        mine = self.touch("work.toml")
        self.touch("other.toml")
        self.touch("other-costs.jsonl")
        self.assertEqual(core.instance_files("work", self.dir), [mine])

    def test_prefix_collision_is_not_a_match(self):
        # "work" must not claim "workshop"'s files.
        mine = self.touch("work.toml")
        self.touch("workshop.toml")
        self.touch("workshop-costs.jsonl")
        self.assertEqual(core.instance_files("work", self.dir), [mine])

    def test_nothing_there(self):
        self.assertEqual(core.instance_files("ghost", self.dir), [])


def _plist(program_args):
    return plistlib.dumps({"Label": "com.gidoon.x",
                           "ProgramArguments": program_args})


class PlistTargetsRepo(unittest.TestCase):
    def test_matches_when_daemon_path_is_inside_the_repo(self):
        data = _plist(["/usr/bin/python3", "/Users/x/.gidoon/bin/gidoon",
                       "--config", "/Users/x/.config/gidoon/a.toml"])
        self.assertTrue(core.plist_targets_repo(data, "/Users/x/.gidoon"))

    def test_no_match_for_a_different_clone(self):
        data = _plist(["/usr/bin/python3", "/Users/x/code/gidoon/bin/gidoon",
                       "--config", "/Users/x/.config/gidoon/a.toml"])
        self.assertFalse(core.plist_targets_repo(data, "/Users/x/.gidoon"))

    def test_trailing_slash_on_repo_dir_is_fine(self):
        data = _plist(["/usr/bin/python3", "/Users/x/.gidoon/bin/gidoon"])
        self.assertTrue(core.plist_targets_repo(data, "/Users/x/.gidoon/"))

    def test_sibling_directory_sharing_a_prefix_is_not_a_match(self):
        # "/Users/x/.gidoon-old" must not match "/Users/x/.gidoon".
        data = _plist(["/usr/bin/python3",
                       "/Users/x/.gidoon-old/bin/gidoon"])
        self.assertFalse(core.plist_targets_repo(data, "/Users/x/.gidoon"))

    def test_garbage_plist_is_false_not_an_exception(self):
        self.assertFalse(core.plist_targets_repo(b"not a plist", "/Users/x"))

    def test_plist_without_program_arguments(self):
        self.assertFalse(core.plist_targets_repo(
            plistlib.dumps({"Label": "com.gidoon.x"}), "/Users/x"))


if __name__ == "__main__":
    unittest.main()
