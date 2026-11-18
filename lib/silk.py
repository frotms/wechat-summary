#!/usr/bin/env python
"""Optional SILK -> WAV decoding for WeChat voice messages.

Uses the `pilk` package (GPL-3.0) if installed.  The skill still works without
it: voice messages are exported as raw `.silk` files, just not playable.

This module is a thin wrapper so the main exporter never imports pilk unless
audio conversion is requested and available.
"""

import os


def available():
    try:
        import pilk  # noqa: F401
        return True
    except Exception:
        return False


def to_wav(silk_path, wav_path, rate=24000):
    """Decode one SILK file to mono 16-bit WAV.

    Returns (ok, detail).  Never raises; the caller can fall back to .silk.
    """
    try:
        import pilk
    except Exception as exc:  # noqa: BLE001
        return False, f"pilk not installed: {exc}"
    try:
        os.makedirs(os.path.dirname(os.path.abspath(wav_path)) or ".", exist_ok=True)
        pilk.silk_to_wav(silk_path, wav_path, rate=int(rate))
        return True, os.path.getsize(wav_path)
    except Exception as exc:  # noqa: BLE001
        try:
            if os.path.isfile(wav_path):
                os.remove(wav_path)
        except OSError:
            pass
        return False, str(exc)


def cleanup(path):
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass
