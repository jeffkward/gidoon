"""The `gidoon send` subcommand: outbound sendMessage through an instance's
bot — the bell any job on the machine can ring. Never polls (see the
one-poller law), so it can't collide with the running daemon."""
import io
import os
import tempfile
import unittest
from unittest import mock

import helpers
import gidoon as core

D = helpers.DAEMON


class ResolveInstanceConfig(unittest.TestCase):
    def test_bare_name_resolves_into_config_dir(self):
        self.assertEqual(core.resolve_instance_config("assistant"),
                         os.path.join(core.CONFIG_DIR, "assistant.toml"))

    def test_path_with_separator_passes_through(self):
        self.assertEqual(core.resolve_instance_config("/tmp/x.toml"),
                         "/tmp/x.toml")

    def test_toml_suffix_passes_through(self):
        self.assertEqual(core.resolve_instance_config("local.toml"),
                         "local.toml")


class SendCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        helpers.write_config(self.dir)
        self.toml = os.path.join(self.dir, "testbot.toml")

    def send(self, argv, stdin=None):
        fake = helpers.FakeTg()
        patches = [mock.patch.object(D, "Tg", return_value=fake)]
        if stdin is not None:
            patches.append(mock.patch("sys.stdin", io.StringIO(stdin)))
        with patches[0]:
            if stdin is not None:
                with patches[1]:
                    D.send_main(argv)
            else:
                D.send_main(argv)
        return fake

    def test_message_argument_is_delivered_to_the_instance_chat(self):
        fake = self.send([self.toml, "backup finished"])
        self.assertEqual(fake.sent, [(42, "backup finished")])

    def test_no_message_argument_reads_stdin(self):
        fake = self.send([self.toml], stdin="piped report\n")
        self.assertEqual(fake.sent, [(42, "piped report")])

    def test_long_message_is_split(self):
        fake = self.send([self.toml, "x" * 5000])
        self.assertEqual([len(t) for _, t in fake.sent], [4096, 904])

    def test_empty_message_exits_nonzero(self):
        with self.assertRaises(SystemExit):
            self.send([self.toml, ""])

    def test_main_dispatches_send_subcommand(self):
        fake = helpers.FakeTg()
        with mock.patch.object(D, "Tg", return_value=fake):
            D.main(["send", self.toml, "hi"])
        self.assertEqual(fake.sent, [(42, "hi")])


if __name__ == "__main__":
    unittest.main()
