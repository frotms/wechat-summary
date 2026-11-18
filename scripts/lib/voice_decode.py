# -*- coding: utf-8 -*-
"""SILK v3 -> WAV decoding shared by 4.1/4.2 voice maps."""
from __future__ import annotations

import io
import os
import wave


def decode_silk_to_wav(blob: bytes, out_path: str) -> bool:
    """SILK v3 bytes -> 24kHz mono 16-bit WAV. False when undecodable."""
    silk = blob[1:] if blob[:1] == b"\x02" else blob
    if not silk.startswith(b"#!SILK_V3"):
        return False
    pcm = b""
    try:
        import pysilk
        out = io.BytesIO()
        pysilk.decode(io.BytesIO(silk), out, 24000)
        pcm = out.getvalue()
    except Exception:
        try:
            import pilk
            import tempfile
            tmp = tempfile.mkdtemp(prefix="silk_")
            silk_path = os.path.join(tmp, "a.silk")
            pcm_path = os.path.join(tmp, "a.pcm")
            with open(silk_path, "wb") as handle:
                handle.write(silk)
            pilk.decode(silk_path, pcm_path, 24000)
            with open(pcm_path, "rb") as handle:
                pcm = handle.read()
        except Exception:
            return False
    if not pcm:
        return False
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    try:
        with wave.open(out_path, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(24000)
            handle.writeframes(pcm)
        return True
    except Exception:
        return False
