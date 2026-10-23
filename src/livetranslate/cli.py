import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

from .backends.llama import LlamaTranslator
from .backends.factory import create_asr
from .config import load_config
from .errors import LiveTranslateError
from .pipeline import TranslationPipeline
from .results import save_json


def new_name(prefix: str) -> str:
    return prefix + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]


def capture_backend(config):
    if sys.platform != "linux":
        raise LiveTranslateError("platform_unsupported", "System capture currently supports Linux only; use --input for existing WAV")
    from .audio.linux import LinuxAudioCapture
    return LinuxAudioCapture(config.audio)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Local English audio to Simplified Chinese translation (M1/M2)")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["demo", "capture", "transcribe", "translate", "devices", "serve", "live"]:
        p = sub.add_parser(name)
        default = Path("config/default.toml")
        p.add_argument("--config", type=Path, default=default if default.exists() else None)
        if name in {"demo", "capture", "live"}:
            p.add_argument("--duration", type=float)
            p.add_argument("--source")
        if name == "live":
            p.add_argument("--headless", action="store_true", help="console mode; does not measure GUI display latency")
            p.add_argument("--output-dir", type=Path)
            p.add_argument("--screenshot", type=Path, help="save the actual subtitle window after the first bilingual final")
        if name in {"demo", "transcribe"}:
            p.add_argument("--input", type=Path, required=name == "transcribe")
            p.add_argument("--asr-model", type=Path)
            p.add_argument("--asr-binary", type=Path)
            p.add_argument("--asr-engine", choices=["whisper", "qwen3"])
            p.add_argument("--backend", choices=["vulkan", "hip", "cpu"])
            p.add_argument("--device", type=int)
        if name == "demo":
            p.add_argument("--audio-output", type=Path)
        if name in {"demo", "translate"}:
            p.add_argument("--base-url")
            p.add_argument("--translation-model")
            p.add_argument("--max-tokens", type=int)
        if name == "translate":
            p.add_argument("--text", required=True)
        if name in {"demo", "capture", "transcribe", "translate"}:
            p.add_argument("--output", type=Path, help="WAV path for capture, JSON path otherwise")
        if name == "serve":
            p.add_argument("--model", type=Path)
            p.add_argument("--binary", type=Path)
            p.add_argument("--backend", choices=["vulkan", "hip", "cpu"])
            p.add_argument("--device")
            p.add_argument("--port", type=int)
            p.add_argument("--log", type=Path)
    args = parser.parse_args(argv)
    try:
        override = {"audio": {}, "asr": {}, "translation": {}, "server": {}}
        for option, section, key in [
            ("duration", "audio", "duration"), ("source", "audio", "source"),
            ("asr_model", "asr", "model"), ("asr_binary", "asr", "binary"),
            ("asr_engine", "asr", "engine"),
            ("base_url", "translation", "base_url"), ("translation_model", "translation", "model"),
            ("max_tokens", "translation", "max_tokens"), ("model", "server", "model"),
            ("binary", "server", "binary"), ("port", "server", "port"),
        ]:
            value = getattr(args, option, None)
            override[section][key] = str(value.expanduser().resolve()) if isinstance(value, Path) else value
        if hasattr(args, "backend"):
            section = "server" if args.command == "serve" else "asr"
            override[section].update(backend=args.backend, device=args.device)
        if args.command == "live":
            override["audio"].pop("duration", None)
        config = load_config(args.config, override)
        output_dir = Path(config.output.directory)
        if args.command == "live":
            from .streaming.runner import run_live
            return run_live(config, source=args.source, duration=args.duration, headless=args.headless,
                            output_dir=args.output_dir, screenshot=args.screenshot)
        if args.command == "serve":
            from .server import serve
            return serve(config, args.log)
        if args.command == "devices":
            print(json.dumps([asdict(m) for m in capture_backend(config).enumerate_monitors()], ensure_ascii=False, indent=2))
            return 0
        if args.command == "capture":
            path = args.output or output_dir / (new_name("capture") + ".wav")
            result = capture_backend(config).capture(config.audio.duration, path, config.audio.source or None)
            json_path = path.with_suffix(".capture.json")
        elif args.command == "transcribe":
            result = create_asr(config.asr, output_dir, config.audio.silence_dbfs).transcribe(args.input)
            json_path = args.output or output_dir / (new_name("asr") + ".json")
        elif args.command == "translate":
            result = LlamaTranslator(config.translation).translate(args.text)
            json_path = args.output or output_dir / (new_name("translation") + ".json")
        else:
            pipeline = TranslationPipeline(None if args.input else capture_backend(config),
                                           create_asr(config.asr, output_dir, config.audio.silence_dbfs), LlamaTranslator(config.translation),
                                           config.audio.silence_dbfs)
            audio = args.audio_output or output_dir / (new_name("capture") + ".wav")
            result = pipeline.run(input_file=args.input, duration=config.audio.duration,
                                  audio_output=audio, source=config.audio.source or None)
            json_path = args.output or output_dir / (new_name("demo") + ".json")
        save_json(json_path, result)
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        print(f"Saved: {json_path}", file=sys.stderr)
        return 0 if result.success else 1
    except LiveTranslateError as exc:
        print(json.dumps({"success": False, "error_code": exc.code, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except OSError as exc:
        print(json.dumps({"success": False, "error_code": "io", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
