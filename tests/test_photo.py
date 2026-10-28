"""An inbound photo (or an image sent as a file) becomes a turn: the daemon
saves it under the instance's media folder and the prompt names the path,
with a Read grant for that one file added to that one turn."""
import os
import tempfile
import unittest
from unittest import mock

import helpers
import gidoon as core

D = helpers.DAEMON
GOOD = {"type": "result", "subtype": "success", "result": "a sign",
        "session_id": "s1", "usage": {}}


class Photo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def make(self, **cfg):
        daemon_cfg = helpers.write_config(self.dir, **cfg)
        tg = helpers.FakeTg()
        tg.files = {"big": b"JPEGDATA", "small": b"x"}
        daemon = helpers.make_daemon(daemon_cfg, tg)
        daemon.media_dir = os.path.join(self.dir, "media")
        return daemon, tg

    def run_turn(self, daemon, msg):
        seen = {}

        def fake_stream(prompt, session_id, on_event, **kw):
            seen["prompt"] = prompt
            seen["tools"] = kw["allowed_tools"]
            return dict(GOOD)

        with mock.patch.object(core, "stream_claude", fake_stream):
            daemon.handle_message(msg)
        return seen

    def photo_msg(self, caption=None):
        msg = {"message_id": 7, "chat": {"id": 42},
               "photo": [{"file_id": "small", "width": 90},
                         {"file_id": "big", "width": 1280}]}
        if caption is not None:
            msg["caption"] = caption
        return msg

    def saved(self):
        names = os.listdir(os.path.join(self.dir, "media"))
        self.assertEqual(len(names), 1)
        return os.path.join(self.dir, "media", names[0])

    def test_photo_saves_largest_and_names_it(self):
        daemon, tg = self.make(allowed_tools='["Glob"]')
        seen = self.run_turn(daemon, self.photo_msg("translate this"))
        path = self.saved()
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"JPEGDATA")
        self.assertIn(path, seen["prompt"])
        self.assertIn("Read tool", seen["prompt"])
        self.assertTrue(seen["prompt"].rstrip().endswith("translate this"))
        self.assertEqual(seen["tools"], ["Glob", "Read(/" + path + ")"])
        self.assertEqual(tg.sent[-1][1], "a sign")

    def test_no_caption_still_runs(self):
        daemon, tg = self.make(allowed_tools='["Glob"]')
        seen = self.run_turn(daemon, self.photo_msg())
        self.assertIn(self.saved(), seen["prompt"])
        self.assertIn("No caption", seen["prompt"])

    def test_grant_is_for_one_turn_only(self):
        daemon, tg = self.make(allowed_tools='["Glob"]')
        self.run_turn(daemon, self.photo_msg())
        seen = self.run_turn(daemon, helpers.text_msg("and now?"))
        self.assertEqual(seen["tools"], ["Glob"])

    def test_uncapped_config_gets_no_grant(self):
        daemon, tg = self.make()
        seen = self.run_turn(daemon, self.photo_msg())
        self.assertEqual(seen["tools"], [])

    def test_image_document(self):
        daemon, tg = self.make(allowed_tools='["Glob"]')
        msg = {"message_id": 8, "chat": {"id": 42}, "caption": "menu",
               "document": {"file_id": "big", "mime_type": "image/png",
                            "file_name": "IMG_1.PNG"}}
        seen = self.run_turn(daemon, msg)
        self.assertTrue(self.saved().endswith(".png"))
        self.assertIn("menu", seen["prompt"])

    def test_non_image_document_declined(self):
        daemon, tg = self.make()
        daemon.handle_message({"message_id": 9, "chat": {"id": 42},
                               "document": {"file_id": "big",
                                            "mime_type": "application/pdf"}})
        self.assertIn("Text only", tg.sent[-1][1])

    def test_fetch_failure_says_so_and_runs_nothing(self):
        daemon, tg = self.make()
        msg = self.photo_msg()
        msg["photo"] = [{"file_id": "gone"}]
        with mock.patch.object(core, "stream_claude") as stream:
            daemon.handle_message(msg)
        stream.assert_not_called()
        self.assertIn("couldn't fetch", tg.sent[-1][1].lower())


if __name__ == "__main__":
    unittest.main()
