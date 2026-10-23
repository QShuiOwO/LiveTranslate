from collections import defaultdict, deque
from dataclasses import asdict
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import threading
from time import monotonic, monotonic_ns

from ..results import save_json
from ..telemetry import gpu_snapshot


def percentile(values, percentile):
    if not values: return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100
    lo, hi = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


class SessionMetrics:
    def __init__(self, directory):
        self.directory = Path(directory)
        if (self.directory / "events.jsonl").exists():
            raise ValueError(f"Session already exists; choose a new --output-dir: {directory}")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.handler = RotatingFileHandler(self.directory / "events.jsonl", maxBytes=16*1024*1024,
                                           backupCount=2, encoding="utf-8")
        self.handler.setFormatter(logging.Formatter("%(message)s"))
        self.lock = threading.RLock()
        self.values = defaultdict(lambda: deque(maxlen=10000))
        self.counts = defaultdict(int)
        self.resources = deque(maxlen=1200)
        self.start_ns = monotonic_ns()
        self.ui_paints = 0

    def event(self, kind, **data):
        with self.lock:
            payload = {"kind": kind, "event_ns": monotonic_ns(), **data}
            self.handler.emit(logging.LogRecord("session", logging.INFO, "", 0, json.dumps(payload, ensure_ascii=False), (), None))

    def observe(self, name, value):
        with self.lock:
            self.values[name].append(value)
            self.counts[name] += 1

    def snapshot(self):
        with self.lock:
            return {name: {"n": len(v), "total_n": self.counts[name], "p50": percentile(v, 50),
                           "p95": percentile(v, 95), "min": min(v), "max": max(v)}
                    for name, v in self.values.items() if v}

    def close(self):
        self.handler.close()


class ResourceMonitor:
    def __init__(self, engine, interval=5):
        self.engine = engine
        self.interval = interval
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="resource-monitor", daemon=True)

    def start(self): self.thread.start()

    def _run(self):
        import psutil
        root = psutil.Process()
        previous = {}
        previous_time = monotonic()
        while not self.stop_event.is_set():
            now = monotonic()
            processes = [root, *root.children(recursive=True)]
            app_rss, tree_rss, cpu_seconds = 0, 0, 0
            current = {}
            for p in processes:
                try:
                    mem = p.memory_info().rss
                    tree_rss += mem
                    if p.pid == root.pid: app_rss = mem
                    t = p.cpu_times()
                    current[p.pid] = t.user + t.system
                    if p.pid in previous: cpu_seconds += max(0, current[p.pid] - previous[p.pid])
                except (psutil.NoSuchProcess, psutil.AccessDenied): pass
            sample = {"elapsed_s": (monotonic_ns() - self.engine.metrics.start_ns) / 1e9,
                      "app_rss_bytes": app_rss, "owned_tree_rss_bytes": tree_rss,
                      "tree_cpu_percent_one_core": cpu_seconds / max(now - previous_time, 0.001) * 100,
                      "gpu": gpu_snapshot(), "queues": self.engine.queue_stats(),
                      "frames_read": getattr(self.engine.source, "frames_read", None),
                      "caption_rows": len(self.engine.book.rows)}
            with self.engine.metrics.lock: self.engine.metrics.resources.append(sample)
            self.engine.metrics.event("resource", **sample)
            self.engine.bus.put({"kind": "resources", "data": sample})
            previous, previous_time = current, now
            self.stop_event.wait(self.interval)

    def close(self):
        self.stop_event.set()
        if self.thread.is_alive(): self.thread.join(timeout=2)
