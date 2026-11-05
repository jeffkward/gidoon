"""Tg.send / Tg.edit render Markdown as Telegram HTML — and a message is
never lost to formatting: on the "can't parse entities" 400 the same call
is retried once with the ORIGINAL text and no parse_mode."""
import unittest
from unittest import mock

import helpers

D = helpers.DAEMON

PARSE_ERR = "Bad Request: can't parse entities: Unsupported start tag"


class RecordingTg(D.Tg):
    """The real Tg with call() replaced: records every API call and
    answers from a script, so send/edit's own logic is what's tested."""

    def __init__(self, fail_html=False, error=None):
        super().__init__("000:fake")
        self.calls = []
        self.fail_html = fail_html
        self.error = error
        self.logged = []
        self.log = self.logged.append

    def call(self, method, http_timeout=35, **params):
        self.calls.append((method, params))
        if self.error is not None:
            raise self.error
        if self.fail_html and params.get("parse_mode") == "HTML":
            raise D.TgError(400, PARSE_ERR)
        return {"message_id": 7}


class Send(unittest.TestCase):
    def test_markdown_goes_out_as_html(self):
        tg = RecordingTg()
        tg.send(42, "**hi** & bye")
        (method, params), = tg.calls
        self.assertEqual(method, "sendMessage")
        self.assertEqual(params["parse_mode"], "HTML")
        self.assertEqual(params["text"], "<b>hi</b> &amp; bye")

    def test_fmt_false_sends_plain_without_parse_mode(self):
        tg = RecordingTg()
        tg.send(42, "**hi**", fmt=False)
        (_, params), = tg.calls
        self.assertNotIn("parse_mode", params)
        self.assertEqual(params["text"], "**hi**")

    def test_a_parse_error_retries_once_with_the_original_text(self):
        tg = RecordingTg(fail_html=True)
        result = tg.send(42, "**hi** <there>")
        self.assertEqual(result, {"message_id": 7})
        self.assertEqual(len(tg.calls), 2)
        _, retry = tg.calls[1]
        self.assertEqual(retry["text"], "**hi** <there>")
        self.assertNotIn("parse_mode", retry)

    def test_the_fallback_is_logged_once_without_the_text(self):
        tg = RecordingTg(fail_html=True)
        tg.send(42, "secret **words**")
        self.assertEqual(len(tg.logged), 1)
        self.assertNotIn("secret", tg.logged[0])
        self.assertIn("plain", tg.logged[0])

    def test_other_errors_are_not_retried(self):
        tg = RecordingTg(error=D.TgError(429, "Too Many Requests"))
        with self.assertRaises(D.TgError):
            tg.send(42, "**hi**")
        self.assertEqual(len(tg.calls), 1)

    def test_a_400_that_is_not_a_parse_error_is_not_retried(self):
        tg = RecordingTg(error=D.TgError(400, "Bad Request: chat not found"))
        with self.assertRaises(D.TgError):
            tg.send(42, "**hi**")
        self.assertEqual(len(tg.calls), 1)

    def test_works_without_a_logger(self):
        tg = RecordingTg(fail_html=True)
        tg.log = None
        tg.send(42, "**hi**")
        self.assertEqual(len(tg.calls), 2)


class Edit(unittest.TestCase):
    def test_edit_renders_too(self):
        tg = RecordingTg()
        tg.edit(42, 9, "_done_")
        (method, params), = tg.calls
        self.assertEqual(method, "editMessageText")
        self.assertEqual(params["message_id"], 9)
        self.assertEqual(params["parse_mode"], "HTML")
        self.assertEqual(params["text"], "<i>done</i>")

    def test_edit_falls_back_plain(self):
        tg = RecordingTg(fail_html=True)
        tg.edit(42, 9, "_done_")
        _, retry = tg.calls[1]
        self.assertEqual(retry["text"], "_done_")
        self.assertNotIn("parse_mode", retry)

    def test_edit_fmt_false(self):
        tg = RecordingTg()
        tg.edit(42, 9, "a_b", fmt=False)
        (_, params), = tg.calls
        self.assertNotIn("parse_mode", params)


class ErrorCarriesDescription(unittest.TestCase):
    def test_api_error_payload_description_reaches_the_exception(self):
        tg = D.Tg("000:fake")
        payload = {"ok": False, "error_code": 400, "description": PARSE_ERR}
        fake_resp = mock.MagicMock()
        fake_resp.__enter__.return_value = fake_resp
        with mock.patch.object(D.urllib.request, "urlopen",
                               return_value=fake_resp), \
                mock.patch.object(D.json, "load", return_value=payload):
            with self.assertRaises(D.TgError) as ctx:
                tg.call("sendMessage", chat_id=1, text="x")
        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("can't parse entities", ctx.exception.description)


class SendLong(unittest.TestCase):
    def test_parts_are_split_markdown_then_each_sent_formatted(self):
        fake = helpers.FakeTg()
        code = "\n".join(f"x_{i} = {i}" for i in range(800))
        D.send_long(fake, 42, "Intro\n\n```py\n" + code + "\n```")
        self.assertGreater(len(fake.sent), 1)
        for _, text in fake.sent:
            self.assertLessEqual(len(D.core.to_telegram_html(text)), D.TG_MAX)
        self.assertTrue(all(f for f in fake.sent_fmt))
        for _, text in fake.sent[1:]:
            self.assertTrue(text.startswith("```py\n"), text[:20])

    def test_choices_then_conversion(self):
        fake = helpers.FakeTg()
        D.send_long(fake, 42, 'Pick:\n```choices\n["old_name", "/clear"]\n```')
        (_, text), = fake.sent
        self.assertEqual(text, "Pick:\n\n"
                               "_Tap one, or reply in your own words._\n"
                               "Or send: /clear")
        self.assertEqual(D.core.to_telegram_html(text),
                         "Pick:\n\n<i>Tap one, or reply in your own words."
                         "</i>\nOr send: /clear")
        # the snake_case option rides on its button untouched
        self.assertEqual(fake.sent_markup[0][0][0]["callback_data"],
                         "c:old_name")


class StatusMessageStaysPlain(unittest.TestCase):
    """The streamed status message is gidoon's own tool checklist — file
    names, globs, commands — not Markdown. It goes out plain."""

    def test_status_send_and_edits_are_plain(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = helpers.write_config(tmp.name)
        fake = helpers.FakeTg()
        d = helpers.make_daemon(cfg, fake)

        def fake_stream(text, sid, on_event, **kw):
            on_event({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Read",
                 "input": {"file_path": "/x/my_file_name.py"}}]}})
            return {"subtype": "success", "result": "**done**",
                    "session_id": "s1", "usage": {}}
        with mock.patch.object(D.core, "stream_claude", fake_stream), \
                mock.patch.object(D, "EDIT_INTERVAL", 0):
            d.handle_message(helpers.text_msg("hi"))
        # first send = status (plain); last send = the reply (formatted)
        self.assertFalse(fake.sent_fmt[0])
        self.assertTrue(fake.sent_fmt[-1])
        self.assertTrue(fake.edits)
        self.assertTrue(all(not f for f in fake.edit_fmt))


if __name__ == "__main__":
    unittest.main()
