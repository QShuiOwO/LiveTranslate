#!/usr/bin/env python3
"""Real desktop GUI + HDMI monitor acceptance. Playback starts only after capture is ready."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic, monotonic_ns, sleep

from _bootstrap import ROOT
from livetranslate.audio.linux import LinuxAudioCapture, select_monitor
from livetranslate.config import load_config
from livetranslate.results import save_json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=ROOT / "config/local.toml")
    p.add_argument("--speech", type=Path, default=ROOT / "artifacts/jfk.wav")
    p.add_argument("--duration", type=float, default=35)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--launcher", type=Path, help="Exercise the public launcher instead of calling Python directly")
    p.add_argument("--require-soak", action="store_true")
    args = p.parse_args()
    if args.require_soak and args.duration < 1830: p.error("Use at least 1830 seconds for a 30-minute capture after service warmup")
    if (args.output_dir / "events.jsonl").exists(): p.error("Choose an unused session directory")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_config(args.config)
    monitor = select_monitor(LinuxAudioCapture(cfg.audio).enumerate_monitors(), cfg.audio.source or None)
    if not monitor.sink: p.error("Monitor has no associated output sink")
    env = dict(os.environ); env.pop("PYTHONPATH", None)
    playback = None
    start_ns = None
    playback_terminated = False
    with (args.output_dir / "app.log").open("w") as log:
        command = [str(args.launcher.resolve())] if args.launcher else [sys.executable, "-m", "livetranslate", "live"]
        app = subprocess.Popen([*command, "--config", str(args.config.resolve()),
                                "--duration", str(args.duration), "--output-dir", str(args.output_dir),
                                "--screenshot", str(args.output_dir / "subtitles.png")], stdout=log, stderr=log, env=env)
        try:
            deadline = monotonic() + 40
            while not (args.output_dir / "capture-ready.json").exists():
                if app.poll() is not None: raise RuntimeError("Subtitle application exited before capture became ready")
                if monotonic() > deadline: raise RuntimeError("No ready capture after 40 seconds")
                sleep(0.1)
            start_ns = monotonic_ns()
            playback = subprocess.Popen(["paplay", "--device", monitor.sink, "--volume", "32768", str(args.speech)],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env)
            save_json(args.output_dir / "playback.json", {"submitted_ns": start_ns, "pid": playback.pid,
                      "source": str(args.speech), "sink": monitor.sink, "note": "submission timestamp, not a measured physical speaker onset"})
            app.wait(timeout=args.duration + 60)
        finally:
            if app.poll() is None:
                app.terminate()
                try: app.wait(timeout=50)
                except subprocess.TimeoutExpired: app.kill(); app.wait()
            if playback and playback.poll() is None:
                playback_terminated = True
                playback.terminate()
            if playback:
                playback.wait(timeout=5)
                playback_error = playback.stderr.read().decode(errors="replace")
                playback.stderr.close()
    summary = json.loads((args.output_dir / "summary.json").read_text())
    gui = summary.get("gui", {})
    metrics = summary["speech_to_display"]
    checks = {
        "app_clean_exit": app.returncode == 0 and not summary["workers_alive"],
        "real_desktop": gui.get("qt_platform") not in {None, "offscreen", "minimal"} and gui.get("exposed_paint_events", 0) > 0,
        "actual_bilingual_display": metrics.get("first_en_ms", {}).get("n", 0) > 0 and metrics.get("final_from_speech_end_ms", {}).get("n", 0) > 0,
        "bounded_queues": all(q.get("high_water", 0) <= q["capacity"] for q in summary["queues"].values()),
        "no_pipeline_errors": summary["error_count"] == 0 and summary["stale_jobs"] == 0,
        "no_audio_drops": summary["queues"]["audio"]["dropped"] == 0,
        "no_final_rewrites": summary["final_mutation_attempts"] == 0,
        "playback_completed": playback is not None and playback.returncode == 0 and not playback_terminated,
    }
    if args.require_soak:
        checks["at_least_30_minutes_pcm"] = summary["pcm_duration_s"] >= 1800
        checks["continuous_capture"] = summary["capture_reconnects"] == 0
        painted = metrics.get("final_from_speech_end_ms", {}).get("total_n", 0)
        checks["at_least_98pct_finals_painted"] = bool(summary["final_bilingual"] and painted / summary["final_bilingual"] >= .98)
    acceptance = {"success": all(checks.values()), "checks": checks,
                  "playback_returncode": playback.returncode if playback else None,
                  "playback_terminated": playback_terminated,
                  "playback_error": playback_error if playback else None,
                  "summary": str(args.output_dir / "summary.json")}
    save_json(args.output_dir / "acceptance.json", acceptance)
    print(json.dumps(acceptance, ensure_ascii=False, indent=2))
    return 0 if acceptance["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
