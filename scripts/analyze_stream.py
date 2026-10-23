#!/usr/bin/env python3
"""Analyze recorded real paint events and resource trends; never synthesize timestamps."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from _bootstrap import ROOT
from livetranslate.results import save_json
from livetranslate.streaming.metrics import percentile


def slope_per_minute(samples, key):
    if len(samples) < 2: return None
    xs = [s["elapsed_s"] / 60 for s in samples]
    ys = [s[key] / 2**20 for s in samples]
    xbar, ybar = sum(xs)/len(xs), sum(ys)/len(ys)
    denom = sum((x-xbar)**2 for x in xs)
    return sum((x-xbar)*(y-ybar) for x,y in zip(xs,ys))/denom if denom else None


def analyze(directory):
    values = defaultdict(list)
    final_ids, painted_ids, first_en_ids = set(), set(), set()
    final_text = {}
    rewrites = []
    statuses = Counter()
    resources = []
    stops = []
    visibility = []
    log_paths = [directory / "events.jsonl.2", directory / "events.jsonl.1", directory / "events.jsonl"]
    for path in log_paths:
        if not path.exists(): continue
        with path.open() as f:
            for line in f:
                try: event = json.loads(line)
                except json.JSONDecodeError: continue  # a still-running writer may have an incomplete last line
                if event["kind"] == "paint":
                    for key,value in event["metrics"].items(): values[key].append(value)
                    if "final_from_speech_end_ms" in event["metrics"]: painted_ids.add(event["uid"])
                    if "first_en_ms" in event["metrics"]: first_en_ids.add(event["uid"])
                elif event["kind"] == "resource": resources.append(event)
                elif event["kind"] == "status": statuses[event["code"]] += 1
                elif event["kind"] == "stop_requested": stops.append(event)
                elif event["kind"] == "window_visibility": visibility.append(event)
                elif event["kind"] == "caption" and event["final"]:
                    uid = event["uid"]
                    if uid in final_text and final_text[uid] != event["text"]: rewrites.append(uid)
                    final_text[uid] = event["text"]
                    if event["zh_final"]: final_ids.add(uid)
    summary_path = directory / "summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else None
    duration = resources[-1]["elapsed_s"] if resources else 0
    warm = [s for s in resources if s["elapsed_s"] >= 600]
    baseline = [s for s in resources if 600 <= s["elapsed_s"] <= 900]
    tail = [s for s in resources if s["elapsed_s"] >= max(900, duration-300)]
    def mean(items,key): return sum(s[key] for s in items)/len(items) if items else None
    memory = {}
    for key in ["app_rss_bytes", "owned_tree_rss_bytes"]:
        before,after=mean(baseline,key),mean(tail,key)
        memory[key] = {"peak_mib": max((s[key] for s in resources),default=0)/2**20,
                       "baseline_10_to_15min_mean_mib": before/2**20 if before is not None else None,
                       "last_5min_mean_mib": after/2**20 if after is not None else None,
                       "growth_mib": (after-before)/2**20 if before is not None and after is not None else None,
                       "slope_after_10min_mib_per_min": slope_per_minute(warm,key)}
    cpu = [s["tree_cpu_percent_one_core"] for s in resources if s["elapsed_s"] >= 30]
    gpu_busy, gpu_used = [], []
    for s in resources:
        for g in s["gpu"]:
            if g["device_id"] == "0x7550":
                if g["gpu_busy_percent"] is not None: gpu_busy.append(g["gpu_busy_percent"])
                if g["vram_used_bytes"] is not None: gpu_used.append(g["vram_used_bytes"])
    distributions = {k:{"n":len(v),"p50":percentile(v,50),"p95":percentile(v,95),"max":max(v)} for k,v in values.items()}
    app_growth = memory["app_rss_bytes"]["growth_mib"]
    tree_growth = memory["owned_tree_rss_bytes"]["growth_mib"]
    app_slope = memory["app_rss_bytes"]["slope_after_10min_mib_per_min"]
    checks = {"at_least_30_minutes_pcm": bool(summary and summary["pcm_duration_s"] >= 1800),
              "real_exposed_window": bool(summary and summary.get("gui",{}).get("qt_platform") not in {None,"minimal","offscreen"} and summary.get("gui",{}).get("exposed_paint_events",0)>0),
              "at_least_98pct_finals_painted": bool(final_ids and len(final_ids & painted_ids)/len(final_ids)>=.98),
              "no_confirmed_english_rewrites": not rewrites,
              "no_remaining_workers": bool(summary and not summary["workers_alive"]),
              "bounded_memory_observation": bool(duration >= 1800 and app_growth is not None and app_growth < 64
                                                 and tree_growth is not None and tree_growth < 128
                                                 and app_slope is not None and app_slope < 1)}
    return {"completed": summary is not None, "success": all(checks.values()), "checks":checks,
            "latency_ms":distributions, "resource_sample_count":len(resources), "observed_elapsed_s":duration,
            "final_bilingual_ids":len(final_ids), "painted_final_ids":len(painted_ids),
            "final_paint_coverage":len(final_ids & painted_ids)/len(final_ids) if final_ids else None,
            "unpainted_final_ids":sorted(final_ids-painted_ids), "confirmed_rewrite_ids":rewrites,
            "memory":memory, "cpu_percent_one_core":{"p50":percentile(cpu,50),"p95":percentile(cpu,95),"max":max(cpu) if cpu else None},
            "whole_device_gpu":{"busy_p50":percentile(gpu_busy,50),"busy_p95":percentile(gpu_busy,95),
                                 "busy_max":max(gpu_busy) if gpu_busy else None,"vram_peak_gib":max(gpu_used)/2**30 if gpu_used else None},
            "status_counts":dict(statuses), "stop_events":stops,
            "window_visibility_events":visibility,
            "memory_criterion":"30-minute observation only; compare 10-15min with last5min: app growth <64MiB, owned tree <128MiB, app OLS slope after10min <1MiB/min; not a proof of absence of leaks"}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory",type=Path)
    p.add_argument("--output",type=Path)
    args=p.parse_args()
    result=analyze(args.directory)
    save_json(args.output or args.directory/"analysis.json",result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result["success"] else 1


if __name__=="__main__": raise SystemExit(main())
