import io
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from lib.voice_decode import decode_silk_to_wav  # noqa: E402


class VoiceDecodeTest(unittest.TestCase):
    def test_silk_to_wav(self):
        try:
            import pysilk
        except ImportError:
            self.skipTest('pysilk/silk-python not installed')

        pcm = b'\x00\x00' * 24000  # 1s silence at 24kHz
        silk = io.BytesIO()
        pysilk.encode(io.BytesIO(pcm), silk, 24000, 24000, tencent=True)
        blob = silk.getvalue()
        self.assertTrue(blob.startswith(b'\x02#!SILK_V3'))

        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / 'voice.wav')
            self.assertTrue(decode_silk_to_wav(blob, out))
            with wave.open(out, 'rb') as handle:
                self.assertEqual(handle.getnchannels(), 1)
                self.assertEqual(handle.getframerate(), 24000)
                self.assertGreater(handle.getnframes(), 0)

    def test_invalid_blob(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(decode_silk_to_wav(b'not silk', str(Path(tmp) / 'x.wav')))


if __name__ == '__main__':
    unittest.main()
