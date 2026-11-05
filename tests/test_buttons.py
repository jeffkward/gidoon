"""A choices block becomes tappable buttons on Telegram: one per row, the
option's words on it (through `button_label`), and a tap that
comes back as exactly the words the agent offered — so a host that matches
replies against its options sees the same text a typed reply would give."""
import json
import unittest

import helpers  # noqa: F401  (sys.path setup)
import gidoon_render as r

L = r.button_label


class ButtonLabel(unittest.TestCase):
    def check(self, raw, expected):
        self.assertEqual(L(raw), expected)

    def test_each_word_capitalised(self):
        self.check("try again", "Try Again")
        self.check("edit the draft", "Edit the Draft")
        self.check("Preview", "Preview")

    def test_and_becomes_ampersand(self):
        self.check("Save And Send", "Save & Send")
        self.check("rock and roll", "Rock & Roll")

    def test_punctuation_kept(self):
        self.check("Save, send later", "Save, Send Later")

    def test_small_words_stay_small_in_the_middle(self):
        self.check("save to library", "Save to Library")
        self.check("Pick One Of The Three", "Pick One of the Three")

    def test_first_and_last_words_always_capitalised(self):
        self.check("to do", "To Do")
        self.check("and then", "And Then")
        self.check("the end of", "The End Of")

    def test_particles_are_capitalised(self):
        self.check("turn on", "Turn On")
        self.check("log in now", "Log In Now")
        self.check("set it up", "Set It Up")

    def test_words_with_their_own_capitals_are_left_alone(self):
        self.check("open the iPhone app", "Open the iPhone App")
        self.check("send the URL", "Send the URL")

    def test_hyphens_and_apostrophes(self):
        self.check("follow-up later", "Follow-Up Later")
        self.check("don't send", "Don't Send")

    def test_commands_paths_and_digits_untouched(self):
        self.check("/clear", "/clear")
        self.check("open ~/code/app", "Open ~/code/app")
        self.check("try v2 first", "Try v2 First")

    def test_whitespace_collapses(self):
        self.check("  try\n  again ", "Try Again")
        self.check("", "")


def block(*choices):
    return "```choices\n" + json.dumps(list(choices)) + "\n```"


class TelegramChoices(unittest.TestCase):
    def test_no_block_means_no_keyboard(self):
        self.assertEqual(r.telegram_choices("just text"), ("just text", None))

    def test_one_button_per_row_with_labels(self):
        body, kb = r.telegram_choices(
            "Drafted it.\n\n" + block("Preview", "Save And Send"))
        self.assertEqual(kb, [
            [{"text": "Preview", "callback_data": "c:Preview"}],
            [{"text": "Save & Send",
              "callback_data": "c:Save And Send"}],
        ])
        self.assertTrue(body.startswith("Drafted it.\n\n"))
        self.assertTrue(body.endswith("_Tap one, or reply in your own words._"))

    def test_commands_are_not_buttons(self):
        body, kb = r.telegram_choices("Hi\n\n" + block("Go", "/clear", "/help"))
        self.assertEqual([row[0]["text"] for row in kb], ["Go"])
        self.assertIn("Or send: /clear · /help", body)

    def test_only_commands_means_no_keyboard(self):
        body, kb = r.telegram_choices("Hi\n\n" + block("/clear"))
        self.assertIsNone(kb)
        self.assertEqual(body, "Hi\n\nOr send: /clear")

    def test_callback_data_always_fits_64_bytes(self):
        long = "Keep the old name for now and rename it properly next week ✅ please"
        _, kb = r.telegram_choices(block("short", long, "é" * 40))
        for row in kb:
            self.assertLessEqual(len(row[0]["callback_data"].encode()), 64)
        self.assertEqual(kb[0][0]["callback_data"], "c:short")
        self.assertEqual(kb[1][0]["callback_data"], "i:1")

    def test_long_options_are_also_listed_in_full(self):
        long = "Keep the old name for now and rename it next week"
        body, kb = r.telegram_choices("Q?\n\n" + block("Yes", long))
        self.assertIn("- Yes\n- " + long, body)
        self.assertEqual(len(kb), 2)

    def test_short_options_are_not_listed(self):
        body, _ = r.telegram_choices("Q?\n\n" + block("Yes", "No"))
        self.assertEqual(body, "Q?\n\n_Tap one, or reply in your own words._")

    def test_render_choices_is_unchanged(self):
        # the text form, for hosts that draw choices themselves
        self.assertEqual(r.render_choices("Q\n\n" + block("a", "/x")),
                         "Q\n\n1. a\nReply with a number, or in your own words."
                         "\nOr send: /x")


class ChoiceFromCallback(unittest.TestCase):
    MSG = {"reply_markup": {"inline_keyboard": [
        [{"text": "Short", "callback_data": "c:short"}],
        [{"text": "A Very Long Label", "callback_data": "i:1"}]]}}

    def test_words_carried_in_the_data(self):
        self.assertEqual(r.choice_from_callback("c:Save And Send",
                                                self.MSG),
                         "Save And Send")

    def test_index_falls_back_to_the_button_text(self):
        self.assertEqual(r.choice_from_callback("i:1", self.MSG),
                         "A Very Long Label")

    def test_nonsense_is_none(self):
        for data in ("", "c:", "i:9", "i:x", "done", None):
            with self.subTest(data=data):
                self.assertIsNone(r.choice_from_callback(data, self.MSG))
        self.assertIsNone(r.choice_from_callback("i:0", {}))


if __name__ == "__main__":
    unittest.main()
