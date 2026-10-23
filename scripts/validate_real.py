#!/usr/bin/env python3
"""Play official English speech to a real output, capture its monitor, then use real models."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
import sys
from time import sleep

from _bootstrap import ROOT
from livetranslate.audio.linux import LinuxAudioCapture, select_monitor
from livetranslate.audio.wav import inspect_wav
from livetranslate.backends.llama import LlamaTranslator
from livetranslate.cli import new_name
from livetranslate.config import load_config
from livetranslate.errors import LiveTranslateError
from livetranslate.process import run_command
from livetranslate.results import PipelineResult, save_json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=ROOT / "config/default.toml")
    p.add_argument("--source", help="Explicit monitor; playback is directed to its associated sink without changing system defaults")
    p.add_argument("--duration", type=float, default=15)
    p.add_argument("--speech", type=Path, default=ROOT / "artifacts/jfk.wav")
    p.add_argument("--capture-only", action="store_true")
    p.add_argument("--output", type=Path, default=ROOT / "artifacts/real-system-e2e.json")
    args = p.parse_args()
    config = load_config(args.config)
    capture = LinuxAudioCapture(config.audio)
    monitor = select_monitor(capture.enumerate_monitors(), args.source or config.audio.source or None)
    if not monitor.sink:
        raise LiveTranslateError("audio_sink_missing", "Selected monitor has no known output sink for playback")
    speech_info = inspect_wav(args.speech)
    if speech_info.duration_ms > (args.duration - 2) * 1000:
        raise LiveTranslateError("validation_duration", "Capture must be at least 2 seconds longer than playback speech")
    translator = LlamaTranslator(config.translation)
    if not args.capture_only:
        translator.health()
    audio_file = Path(config.output.directory) / (new_name("system-jfk") + ".wav")

    def playback():
        sleep(1.0)
        return run_command(["paplay", "--device", monitor.sink, "--volume", "32768", str(args.speech)], timeout=args.duration + 5)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(playback)
        cli_exit_code = None
        if args.capture_only:
            result = capture.capture(args.duration, audio_file, monitor.name)
        else:
            import json
            proc = run_command([sys.executable, "-m", "livetranslate", "demo", "--config", str(args.config.resolve()),
                                "--duration", str(args.duration), "--source", monitor.name,
                                "--audio-output", str(audio_file), "--output", str(args.output)],
                               timeout=args.duration + config.asr.timeout + config.translation.timeout + 60, check=False)
            args.output.with_suffix(".stdout.json").write_text(proc.stdout, encoding="utf-8")
            args.output.with_suffix(".stderr.txt").write_text(proc.stderr, encoding="utf-8")
            cli_exit_code = proc.returncode
            if not args.output.exists():
                raise LiveTranslateError("validation_cli", f"Demo CLI exited {proc.returncode} without a result: {proc.stderr[-2000:]}")
            result = PipelineResult(**json.loads(args.output.read_text(encoding="utf-8")))
        try:
            played = future.result(timeout=args.duration + 10)
            playback_result = {"success": True, "elapsed_ms": played.elapsed_ms, "sink": monitor.sink}
        except Exception as exc:
            playback_result = {"success": False, "error": str(exc)}
    save_json(args.output, result)
    acceptance = {"success": bool(result.success and playback_result["success"]),
                  "mode": "capture_only" if args.capture_only else "real_system_audio_to_real_models",
                  "playback": playback_result, "speech_file": str(args.speech),
                  "cli_exit_code": cli_exit_code, "result_file": str(args.output), "result": asdict(result)}
    if not args.capture_only:
        acceptance["english_content_verified"] = "country" in result.original_text.lower()
        acceptance["chinese_content_verified"] = "国家" in result.translated_text
        acceptance["success"] &= acceptance["english_content_verified"] and acceptance["chinese_content_verified"]
        acceptance["success"] &= cli_exit_code == 0
    save_json(args.output.with_suffix(".acceptance.json"), acceptance)
    print(f"Real validation: {'PASS' if acceptance['success'] else 'FAIL'}; result={args.output}")
    if not result.success:
        print(result.error, file=sys.stderr)
    return 0 if acceptance["success"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (LiveTranslateError, OSError) as exc:
        print(f"Real validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
