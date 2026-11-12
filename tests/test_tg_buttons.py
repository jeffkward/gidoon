"""Buttons on the wire: a reply ending in a choices block goes out with an
inline keyboard, the poller asks for taps, and a tap from the owner's chat
answers the spinner, marks the message with what was picked, and runs as
a turn with the option's own words — exactly as if they'd been typed."""
import json
import tempfile
import unittest
from unittest import mock

import helpers

D = helpers.DAEMON
CHAT_ID = 42  # helpers.write_config's chat
CHOICES = 'Ready?\n\n```choices\n["Preview", "Save And Send", "/clear"]\n```'


class RecordingTg(D.Tg):
    def __init__(self, fail_html=False):
        super().__init__("000:fake")
        self.calls, self.fail_html, self.log = [], fail_html, None

    def call(self, method, http_timeout=35, **params):
        self.calls.append((method, params))
        if self.fail_html and params.get("parse_mode") == "HTML":
            raise D.TgError(400, "Bad Request: can't parse entities")
        return {"message_id": 7}


class Wire(unittest.TestCase):
    def test_send_carries_the_keyboard(self):
        tg = RecordingTg()
        kb = [[{"text": "Go", "callback_data": "c:go"}]]
        tg.send(42, "hi", markup=kb)
        (_, params), = tg.calls
        self.assertEqual(json.loads(params["reply_markup"]),
                         {"inline_keyboard": kb})

    def test_plain_fallback_keeps_the_keyboard(self):
        tg = RecordingTg(fail_html=True)
        tg.send(42, "hi", markup=[[{"text": "Go", "callback_data": "c:go"}]])
        _, retry = tg.calls[1]
        self.assertIn("reply_markup", retry)

    def test_no_markup_no_reply_markup(self):
        tg = RecordingTg()
        tg.send(42, "hi")
        self.assertNotIn("reply_markup", tg.calls[0][1])

    def test_the_poller_asks_for_taps(self):
        for long in (True, False):
            tg = RecordingTg()
            tg.get_updates(0, long=long)
            kinds = json.loads(tg.calls[0][1]["allowed_updates"])
            self.assertEqual(set(kinds), {"message", "callback_query"})

    def test_answer_callback_and_edit_markup(self):
        tg = RecordingTg()
        tg.answer_callback("cb1")
        tg.edit_markup(42, 9, [[{"text": "✓ Go", "callback_data": "done"}]])
        self.assertEqual(tg.calls[0], ("answerCallbackQuery",
                                       {"callback_query_id": "cb1"}))
        method, params = tg.calls[1]
        self.assertEqual(method, "editMessageReplyMarkup")
        self.assertEqual(params["message_id"], 9)


class SendLong(unittest.TestCase):
    def test_choices_go_out_as_buttons(self):
        fake = helpers.FakeTg()
        D.send_long(fake, 42, CHOICES)
        (_, text), = fake.sent
        self.assertIn("Tap one", text)
        self.assertIn("Or send: /clear", text)
        self.assertEqual([row[0]["text"] for row in fake.sent_markup[0]],
                         ["Preview", "Save & Send"])

    def test_keyboard_rides_on_the_last_part_only(self):
        fake = helpers.FakeTg()
        long = "\n\n".join("para %d " % i + "word " * 150 for i in range(12))
        D.send_long(fake, 42, long + "\n\n" + CHOICES)
        self.assertGreater(len(fake.sent), 1)
        self.assertTrue(all(m is None for m in fake.sent_markup[:-1]))
        self.assertIsNotNone(fake.sent_markup[-1])

    def test_no_choices_no_keyboard(self):
        fake = helpers.FakeTg()
        D.send_long(fake, 42, "just words")
        self.assertEqual(fake.sent_markup, [None])


def tap(data, chat_id=CHAT_ID, message_id=55, cb_id="cb1"):
    return {"update_id": 900, "callback_query": {
        "id": cb_id, "data": data, "from": {"id": chat_id},
        "message": {"message_id": message_id, "chat": {"id": chat_id},
                    "reply_markup": {"inline_keyboard": [
                        [{"text": "Save & Send",
                          "callback_data": "c:Save And Send"}]]}}}}


class Taps(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.fake = helpers.FakeTg()
        self.d = helpers.make_daemon(helpers.write_config(tmp.name), self.fake)
        patcher = mock.patch.object(self.d, "handle_message")
        self.turns = patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_tap_runs_a_turn_with_the_options_own_words(self):
        self.d.handle_update(tap("c:Save And Send"))
        (msg,), _ = self.turns.call_args
        self.assertEqual(msg["text"], "Save And Send")
        self.assertTrue(msg.get("tapped"))
        self.assertEqual(self.fake.callback_answers, ["cb1"])
        (chat, mid, kb), = self.fake.markup_edits
        self.assertEqual((chat, mid), (CHAT_ID, 55))
        self.assertEqual(kb, [[{"text": "✓ Save & Send",
                                "callback_data": "done"}]])
        self.assertEqual(self.d.state["offset"], 901)

    def test_a_tap_is_logged_with_its_timing_but_never_its_words(self):
        lines = []
        with mock.patch.object(self.d, "log", side_effect=lines.append):
            self.d.handle_update(tap("c:Save And Send"))
        self.assertTrue(any(l.startswith("tap received (message 55)")
                            for l in lines), lines)
        self.assertTrue(any(l.startswith("tap: buttons cleared in ")
                            and l.endswith(" ms") for l in lines), lines)
        self.assertFalse(any("Save" in l for l in lines), lines)

    def test_a_double_tap_runs_one_turn(self):
        self.d.handle_update(tap("c:Save And Send"))
        self.d.handle_update(tap("c:Save And Send", cb_id="cb2"))
        self.assertEqual(self.turns.call_count, 1)
        self.assertEqual(self.fake.callback_answers, ["cb1", "cb2"])

    def test_the_done_button_does_nothing(self):
        self.d.handle_update(tap("done"))
        self.turns.assert_not_called()
        self.assertEqual(self.fake.markup_edits, [])
        self.assertEqual(self.fake.callback_answers, ["cb1"])

    def test_a_tap_from_another_chat_is_ignored(self):
        self.d.handle_update(tap("c:Save And Send", chat_id=999))
        self.turns.assert_not_called()
        self.assertEqual(self.fake.markup_edits, [])

    def test_a_failed_turn_still_advances_the_offset(self):
        self.turns.side_effect = RuntimeError("boom")
        self.d.handle_update(tap("c:Save And Send"))
        self.assertEqual(self.d.state["offset"], 901)


class TappedTurn(unittest.TestCase):
    def test_a_tapped_turn_puts_no_reaction_on_the_bots_message(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fake = helpers.FakeTg()
        d = helpers.make_daemon(helpers.write_config(tmp.name), fake)

        def fake_stream(text, sid, on_event, **kw):
            return {"subtype": "success", "result": "ok",
                    "session_id": "s1", "usage": {}}
        with mock.patch.object(D.core, "stream_claude", fake_stream):
            d.handle_message({"message_id": 55, "text": "Preview",
                              "chat": {"id": CHAT_ID},
                              "tapped": True})
        self.assertEqual(fake.reactions, [])
        self.assertEqual(fake.sent[-1][1], "ok")


if __name__ == "__main__":
    unittest.main()
