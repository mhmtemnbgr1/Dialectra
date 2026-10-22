"""Arayüz metinleri testleri: iki dilde de aynı anahtarlar ve yer tutucular olmalı."""

import string
import unittest

from app import i18n


def placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


class StringsTest(unittest.TestCase):
    def test_same_keys(self):
        self.assertEqual(set(i18n.STRINGS["tr"]), set(i18n.STRINGS["en"]))

    def test_same_placeholders(self):
        for key, text in i18n.STRINGS["tr"].items():
            self.assertEqual(placeholders(text), placeholders(i18n.STRINGS["en"][key]), key)

    def test_switch_language(self):
        try:
            i18n.set_language("en")
            self.assertEqual(i18n.t("start"), "Start")
            self.assertIn("timeout", i18n.t("fatal_rejected", error="timeout"))
            i18n.set_language("xx")  # bilinmeyen dil Türkçeye düşer
            self.assertEqual(i18n.t("start"), "Başlat")
        finally:
            i18n.set_language("tr")


if __name__ == "__main__":
    unittest.main()
