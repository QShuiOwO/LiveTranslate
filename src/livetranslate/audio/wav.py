from array import array
import math
from pathlib import Path
import sys
import wave

from ..errors import LiveTranslateError
from ..results import AudioInfo


def inspect_wav(path: Path, silence_dbfs: float = -60.0) -> AudioInfo:
    """Validate bounded PCM16 mono/16 kHz input and detect empty, silent or truncated data."""
    try:
        with wave.open(str(path), "rb") as w:
            rate, channels, width, frames = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
            if rate != 16000 or channels != 1 or width != 2 or w.getcomptype() != "NONE":
                raise LiveTranslateError("audio_format", "Expected 16 kHz mono PCM16 WAV; convert with ffmpeg -ar 16000 -ac 1 -c:a pcm_s16le")
            if not frames:
                raise LiveTranslateError("audio_empty", "WAV contains no audio frames")
            if frames > 16000 * 300:
                raise LiveTranslateError("audio_too_long", "M1 accepts at most 300 seconds per file")
            count, energy, peak = 0, 0, 0
            while chunk := w.readframes(65536):
                samples = array("h")
                samples.frombytes(chunk)
                if sys.byteorder != "little":
                    samples.byteswap()
                count += len(samples)
                energy += sum(x * x for x in samples)
                peak = max(peak, max((abs(x) for x in samples), default=0))
            if count != frames:
                raise LiveTranslateError("audio_truncated", f"Expected {frames} frames, read {count}")
            # Finite floor keeps JSON portable even for digital silence.
            rms_dbfs = 20 * math.log10(max(math.sqrt(energy / count) / 32768, 1e-10))
            if peak == 0 or rms_dbfs < silence_dbfs:
                raise LiveTranslateError("audio_silent", f"Audio is silent or below threshold ({rms_dbfs:.1f} dBFS < {silence_dbfs:.1f})")
            return AudioInfo(frames * 1000 / rate, frames, rate, channels, width, rms_dbfs, peak / 32768)
    except LiveTranslateError:
        raise
    except (OSError, EOFError, wave.Error, ValueError) as exc:
        raise LiveTranslateError("audio_invalid", f"Cannot read WAV {path}: {exc}") from exc
