#!/usr/bin/env python3
"""Verify OpenSLR LibriSpeech test-clean and assemble varied, real English human speech."""
import argparse
from array import array
import hashlib
import json
import math
from pathlib import Path
import random
import subprocess
import sys
import tarfile
import wave

from _bootstrap import ROOT
from livetranslate.results import save_json

PUBLISHED_MD5 = "32fa31d27d2e1cad72775fee3f4849a9"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seconds", type=float, default=1810)
    p.add_argument("--directory", type=Path, default=ROOT / "artifacts/m2-fixtures")
    args = p.parse_args()
    archive = args.directory / "test-clean.tar.gz"
    md5, sha = hashlib.md5(), hashlib.sha256()
    with archive.open("rb") as f:
        for data in iter(lambda: f.read(8 * 1024 * 1024), b""):
            md5.update(data); sha.update(data)
    if md5.hexdigest() != PUBLISHED_MD5: raise RuntimeError("OpenSLR published MD5 mismatch")
    extracted = args.directory / "LibriSpeech/test-clean"
    if not extracted.exists():
        with tarfile.open(archive) as tar: tar.extractall(args.directory, filter="data")
    files = sorted(extracted.rglob("*.flac")); random.Random(20261023).shuffle(files)
    refs = {}
    for ref in extracted.rglob("*.trans.txt"):
        for line in ref.read_text().splitlines():
            key, text = line.split(" ", 1); refs[key] = text
    output = args.directory / "english-30min.wav"
    if output.exists(): raise RuntimeError("Preserving existing fixture; choose another directory")
    total, entries = 0, []
    with wave.open(str(output), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        for path in files:
            if total / 16000 >= args.seconds: break
            proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
                                   "-ar", "16000", "-ac", "1", "-f", "s16le", "pipe:1"],
                                  capture_output=True, check=True, timeout=20)
            samples = array("h", proc.stdout)
            if sys.byteorder != "little": samples.byteswap()
            rms = math.sqrt(sum(x*x for x in samples)/len(samples))
            peak = max(abs(x) for x in samples)
            gain = min((32768 * 10**(-18/20))/max(rms, 1), 30000/max(peak, 1))
            scaled = array("h", (int(x * gain) for x in samples))
            if sys.byteorder != "little": scaled.byteswap()
            entries.append({"id": path.stem, "speaker": path.parts[-3], "start_sample": total,
                            "speech_samples": len(samples), "reference": refs.get(path.stem), "linear_gain": gain})
            w.writeframes(scaled.tobytes()); total += len(samples)
            w.writeframes(b"\0" * 25600); total += 12800  # 800ms inter-clip pause
    save_json(args.directory / "fixture.json", {
        "source": "https://www.openslr.org/12", "download": "https://www.openslr.org/resources/12/test-clean.tar.gz",
        "attribution": "LibriSpeech, Vassil Panayotov et al., OpenSLR SLR12 / LibriVox readings",
        "license": "CC BY 4.0", "archive_md5": md5.hexdigest(), "archive_sha256": sha.hexdigest(),
        "wav": str(output), "duration_s": total/16000, "clips": len(entries),
        "speakers": len({e['speaker'] for e in entries}), "entries": entries,
        "processing": "Real human audio decoded to 16kHz mono PCM16; per-clip gain targets -18dBFS RMS with peak limit; 800ms silence between clips; no TTS or fake ASR"})
    print(f"Prepared {total/16000:.2f}s, {len(entries)} real clips, {len({e['speaker'] for e in entries})} speakers")


if __name__ == "__main__": main()
