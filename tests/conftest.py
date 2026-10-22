from array import array
import math
from pathlib import Path
import wave

import pytest


def write_wav(path: Path, seconds=0.25, silent=False, rate=16000, channels=1):
    values = array("h", [0 if silent else int(12000 * math.sin(2 * math.pi * 440 * i / rate))
                         for i in range(int(seconds * rate) * channels)])
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(values.tobytes())
    return path


@pytest.fixture
def audio(tmp_path):
    return write_wav(tmp_path / "tone.wav")
