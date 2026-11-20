"""Range editing preserves content and never uses Selection; ambiguous writes are not retried."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pywintypes
from pc_control import richedit


class Document:
    def __init__(self, text):
        self.text = text
        self.write_error = None
        self.bad_readback = False
        self.writes = 0

    @property
    def Selection(self):
        raise AssertionError("must never access the user's selection")

    def Range(self, start, end):
        doc = self
        start, end = min(start, len(doc.text)), min(end, len(doc.text))
        class Span:
            @property
            def Text(self):
                return "wrong readback" if doc.bad_readback else doc.text[self.start:self.end]

            @Text.setter
            def Text(self, value):
                doc.writes += 1
                if doc.write_error:
                    raise doc.write_error
                doc.text = doc.text[:self.start] + value + doc.text[self.end:]
                self.end = self.start + len(value)
        span = Span()
        span.start, span.end = start, end
        return span


class RichEditTests(unittest.TestCase):
    def setUp(self):
        self.doc = Document("original ñ 漢字 🙂\r")
        self.patch = patch.object(richedit, "_document", return_value=self.doc)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_append_keeps_all_original_text_and_newlines(self):
        original = self.doc.text
        out = richedit.type_text(1, "more\r\nUnicode 🙂\n")
        self.assertTrue(out.startswith("ok"), out)
        self.assertEqual(self.doc.text, original + "more\rUnicode 🙂\r")
        self.assertEqual(self.doc.writes, 1)

    def test_replace_by_range(self):
        self.assertTrue(richedit.type_text(1, "new\ntext", True).startswith("ok"))
        self.assertEqual(self.doc.text, "new\rtext")

    def test_selection_tripwire_is_active(self):
        with self.assertRaises(AssertionError):
            _ = self.doc.Selection

    def test_missing_native_model_can_fall_back(self):
        with patch.object(richedit, "_document", return_value=None):
            self.assertIsNone(richedit.type_text(1, "text"))
        self.assertEqual(self.doc.writes, 0)

    def test_unsupported_range_can_fall_back_before_writing(self):
        with patch.object(self.doc, "Range", side_effect=AttributeError("no range")):
            self.assertIsNone(richedit.type_text(1, "text"))
        self.assertEqual(self.doc.writes, 0)

    def test_protected_range_refuses(self):
        self.doc.write_error = pywintypes.com_error(-2147024891, "protected", None, None)
        self.assertTrue(richedit.type_text(1, "text").startswith("did not type"))
        self.assertEqual(self.doc.writes, 1)

    def test_ambiguous_rpc_write_does_not_allow_retry_fallback(self):
        self.doc.write_error = pywintypes.com_error(-2147417848, "disconnected", None, None)
        out = richedit.type_text(1, "text")
        self.assertIn("UNCONFIRMED", out)
        self.assertIn("do not retry", out)
        self.assertEqual(self.doc.writes, 1)

    def test_mismatched_readback_is_unconfirmed(self):
        self.doc.bad_readback = True
        self.assertIn("UNCONFIRMED", richedit.type_text(1, "text"))
        self.assertEqual(self.doc.writes, 1)

    def test_existing_different_com_apartment_is_not_uninitialized(self):
        with patch("pythoncom.CoInitializeEx", side_effect=pywintypes.com_error(-2147417850, "mode", None, None)), \
             patch("pythoncom.CoUninitialize") as uninitialize:
            self.assertTrue(richedit.type_text(1, "text").startswith("ok"))
            uninitialize.assert_not_called()


if __name__ == "__main__":
    result = unittest.main(exit=False)
    if result.result.wasSuccessful():
        print("RichEdit range verification passed")
    sys.exit(not result.result.wasSuccessful())
