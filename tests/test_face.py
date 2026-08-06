"""is_usable_face — the installer's guard on a pasted face. Its job is to
stop input that would break the generated TOML or look like nothing in a
status line, without trying to be a full emoji validator."""
import unittest

import helpers  # noqa: F401  (sys.path setup)
import gidoon as core


class UsableFace(unittest.TestCase):
    def test_a_plain_emoji(self):
        self.assertTrue(core.is_usable_face("🐻"))

    def test_a_zwj_sequence(self):
        self.assertTrue(core.is_usable_face("🐻‍❄️"))

    def test_a_flag(self):
        self.assertTrue(core.is_usable_face("🇨🇦"))

    def test_surrounding_whitespace_is_ignored(self):
        self.assertTrue(core.is_usable_face("  🐻 "))

    def test_empty(self):
        self.assertFalse(core.is_usable_face(""))
        self.assertFalse(core.is_usable_face("   "))
        self.assertFalse(core.is_usable_face(None))

    def test_words_are_not_faces(self):
        self.assertFalse(core.is_usable_face("hello"))
        self.assertFalse(core.is_usable_face("x"))
        self.assertFalse(core.is_usable_face("123"))

    def test_toml_breaking_characters(self):
        # These would land inside emoji = "…" and produce invalid TOML.
        self.assertFalse(core.is_usable_face('"'))
        self.assertFalse(core.is_usable_face("\\"))
        self.assertFalse(core.is_usable_face('🐻"'))
        self.assertFalse(core.is_usable_face("🐻\\n"))

    def test_a_whole_paragraph_pasted_by_accident(self):
        self.assertFalse(core.is_usable_face("🐻" * 12))
        self.assertFalse(core.is_usable_face("the quick brown fox"))

    def test_newline_never_passes(self):
        self.assertFalse(core.is_usable_face("🐻\n🐻"))


if __name__ == "__main__":
    unittest.main()
