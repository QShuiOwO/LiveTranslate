"""Read-only AMD sysfs telemetry. Values are device-wide, not process allocations."""

from pathlib import Path
import threading
from time import monotonic


def gpu_snapshot() -> list[dict]:
    devices = []
    for path in sorted(Path("/sys/bus/pci/devices").glob("*")):
        try:
            if (path / "vendor").read_text().strip() != "0x1002" or not (path / "mem_info_vram_total").exists():
                continue
            def read_int(name):
                try:
                    return int((path / name).read_text())
                except (OSError, ValueError):
                    return None
            devices.append({"pci": path.name, "device_id": (path / "device").read_text().strip(),
                            "vram_total_bytes": read_int("mem_info_vram_total"),
                            "vram_used_bytes": read_int("mem_info_vram_used"),
                            "gpu_busy_percent": read_int("gpu_busy_percent")})
        except OSError:
            continue
    return devices


class GPUSampler:
    def __init__(self, interval: float = 0.1):
        self.interval = interval
        self.samples: list[dict] = []
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def _sample(self):
        self.samples.append({"monotonic_s": monotonic(), "devices": gpu_snapshot()})

    def _worker(self):
        while not self.stop_event.wait(self.interval):
            self._sample()

    def __enter__(self):
        self._sample()
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
        self._sample()

    def summary(self) -> dict:
        result = {"scope": "device-wide; includes desktop and other processes", "interval_ms": self.interval * 1000,
                  "sample_count": len(self.samples), "devices": []}
        keys = {d["pci"] for s in self.samples for d in s["devices"]}
        for key in sorted(keys):
            values = [d for s in self.samples for d in s["devices"] if d["pci"] == key]
            used = [d["vram_used_bytes"] for d in values if d["vram_used_bytes"] is not None]
            busy = [d["gpu_busy_percent"] for d in values if d["gpu_busy_percent"] is not None]
            result["devices"].append({"pci": key, "device_id": values[0]["device_id"],
                                      "vram_total_bytes": values[0]["vram_total_bytes"],
                                      "before_bytes": used[0] if used else None,
                                      "peak_bytes": max(used) if used else None,
                                      "after_bytes": used[-1] if used else None,
                                      "peak_minus_before_bytes": max(used) - used[0] if used else None,
                                      "max_gpu_busy_percent": max(busy) if busy else None})
        if not keys:
            result["unavailable_reason"] = "AMD VRAM sysfs counters are not available in this runtime"
        return result
