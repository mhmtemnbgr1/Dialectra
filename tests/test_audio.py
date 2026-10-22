"""Ses işleme ve hata sınıflandırma testleri (ağ ya da ses cihazı gerekmez).

    python -m unittest discover tests
"""

import unittest

import numpy as np

from app.live_translate import (
    SILENCE_PEAK, Player, _fatal_reason, _peak, detect_language, is_en_or_tr,
)


def pcm(*samples: int) -> bytes:
    return np.array(samples, dtype=np.int16).tobytes()


class PeakTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(_peak(b""), 0)

    def test_negative_extreme(self):
        self.assertEqual(_peak(pcm(10, -32768, 5)), 32768)

    def test_odd_byte_ignored(self):
        self.assertEqual(_peak(pcm(100) + b"\x7f"), 100)


class PlayerMixTest(unittest.TestCase):
    def setUp(self):
        self.player = Player(enabled=False)
        self.player.enabled = True  # hoparlör açmadan kuyruğu ve karıştırmayı kullan

    def mix(self, n_samples: int) -> np.ndarray:
        out = bytearray(n_samples * 2)
        self.player._callback(out, n_samples, None, None)
        return np.frombuffer(bytes(out), dtype=np.int16)

    def test_silence_when_empty(self):
        self.assertTrue((self.mix(4) == 0).all())

    def test_two_sources_are_summed_and_clipped(self):
        self.player.play("tr", pcm(30000, 1000, -30000, 0))
        self.player.play("en", pcm(30000, 2000, -30000, 0))
        self.assertEqual(self.mix(4).tolist(), [32767, 3000, -32768, 0])

    def test_short_source_is_padded(self):
        self.player.play("tr", pcm(5000, 5000))
        self.assertEqual(self.mix(4).tolist(), [5000, 5000, 0, 0])

    def test_loud_audio_marks_source_speaking(self):
        self.player.play("tr", pcm(SILENCE_PEAK * 2) * 10)
        self.mix(10)
        self.assertTrue(self.player.is_speaking("tr"))
        self.assertFalse(self.player.is_speaking("en"))

    def test_disabled_player_drops_audio(self):
        player = Player(enabled=False)
        player.play("tr", pcm(5000))
        self.assertIsNone(player.stream)
        self.assertEqual(player._bufs, {})


class DetectLanguageTest(unittest.TestCase):
    def test_turkish(self):
        self.assertEqual(detect_language("Peki bunu nasıl inşa ettiler? Sanırım doğru."), "tr")

    def test_turkish_without_special_letters(self):
        self.assertEqual(detect_language("bence bu daha iyi ama"), "tr")

    def test_english(self):
        self.assertEqual(detect_language("So how did they build this? I think it is right."), "en")

    def test_misrecognized_as_other_language_counts_as_turkish(self):
        text = "Đấy đấy đấy. Hồi nãy chị bảo là đấy thật ra cũng không phải thế."
        self.assertFalse(is_en_or_tr(text))
        self.assertEqual(detect_language(text), "tr")

    def test_turkish_and_english_alphabets_are_valid(self):
        self.assertTrue(is_en_or_tr("Şöyle güzel bir gün, İstanbul'da hâlâ yağmur"))
        self.assertTrue(is_en_or_tr("That's it, I'm not sure."))

    def test_unknown(self):
        self.assertIsNone(detect_language(""))
        self.assertIsNone(detect_language("Dialectra"))


class FatalReasonTest(unittest.TestCase):
    def test_bad_key(self):
        self.assertIn("anahtar", _fatal_reason(Exception("API key not valid")))

    def test_quota(self):
        self.assertIn("kota", _fatal_reason(Exception("RESOURCE_EXHAUSTED")))

    def test_network_error_is_retried(self):
        self.assertIsNone(_fatal_reason(ConnectionResetError("connection reset")))


if __name__ == "__main__":
    unittest.main()
