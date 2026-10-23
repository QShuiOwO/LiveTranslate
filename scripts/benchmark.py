#!/usr/bin/env python3
"""Sequential real ASR CPU/GPU and local LLM measurements. Never substitutes fakes."""

import argparse
from dataclasses import asdict, replace
from pathlib import Path
import statistics

from _bootstrap import ROOT
from livetranslate.backends.llama import LlamaTranslator
from livetranslate.backends.factory import create_asr
from livetranslate.config import load_config
from livetranslate.results import save_json


def group(results):
    times = [r.elapsed_ms for r in results if r.success]
    return {"success": all(r.success for r in results), "count": len(results),
            "median_ms": statistics.median(times) if times else None,
            "min_ms": min(times) if times else None, "max_ms": max(times) if times else None,
            "runs": [asdict(r) for r in results]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=ROOT / "config/default.toml")
    p.add_argument("--input", type=Path, default=ROOT / "artifacts/jfk.wav")
    p.add_argument("--iterations", type=int, default=3)
    p.add_argument("--output", type=Path, default=ROOT / "artifacts/benchmark.json")
    args = p.parse_args()
    if not 1 <= args.iterations <= 20:
        p.error("--iterations must be between 1 and 20")
    cfg = load_config(args.config)
    results = {}
    for name, backend in [("asr_gpu", cfg.asr), ("asr_cpu", replace(cfg.asr, backend="cpu"))]:
        engine = create_asr(backend, Path(cfg.output.directory), cfg.audio.silence_dbfs)
        results[name] = group([engine.transcribe(args.input) for _ in range(args.iterations)])
        print(f"{name}: success={results[name]['success']}, median={results[name]['median_ms']} ms", flush=True)
    translator = LlamaTranslator(cfg.translation)
    results["translation"] = group([translator.translate("Artificial intelligence is changing the world.")
                                     for _ in range(args.iterations)])
    results["measurement"] = {
        "input": str(args.input), "text": "Artificial intelligence is changing the world.",
        "asr_includes": "WAV validation, process/model startup, inference, result parsing; OS file cache may be warm",
        "llm_includes": "health + model check + HTTP + generation, persistent loaded server; cache_prompt=false",
        "gpu_memory_scope": "device-wide counters sampled every 100ms, not per-process VRAM",
    }
    results["success"] = all(results[k]["success"] for k in ["asr_gpu", "asr_cpu", "translation"])
    save_json(args.output, results)
    print(f"translation: success={results['translation']['success']}, median={results['translation']['median_ms']} ms")
    return 0 if results["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
