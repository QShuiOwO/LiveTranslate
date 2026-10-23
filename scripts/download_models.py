#!/usr/bin/env python3
"""Anonymous, revision-pinned Hugging Face downloads with published SHA-256 verification."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SPECS = {
    "asr": ("sakasegawa/Qwen3-ASR-1.7B-GGUF", "Qwen3-ASR-1.7B-Q8_0.gguf"),
    "translation": ("tencent/Hy-MT2-7B-GGUF", "Hy-MT2-7B-Q4_K_M.gguf"),
}


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def metadata(repo, filename):
    req = Request(f"https://huggingface.co/api/models/{repo}?blobs=true", headers={"User-Agent": "LiveTranslate/0.1"})
    with urlopen(req, timeout=60) as r:
        info = json.load(r)
    sibling = next(s for s in info["siblings"] if s["rfilename"] == filename)
    lfs = sibling["lfs"]
    revision = info["sha"]
    return {"repo": repo, "filename": filename, "revision": revision,
            "sha256": lfs["sha256"], "size": lfs["size"],
            "url": f"https://huggingface.co/{repo}/resolve/{revision}/{filename}",
            "source": f"https://huggingface.co/{repo}", "authentication": "anonymous"}


def download(spec, directory):
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / spec["filename"]
    if dest.exists():
        if dest.stat().st_size == spec["size"] and sha256(dest) == spec["sha256"]:
            return {"file": str(dest), "verified": True, "reused": True, "elapsed_ms": 0}
        raise RuntimeError(f"Existing model has wrong size/hash; preserved without overwriting: {dest}")
    part = dest.with_suffix(dest.suffix + ".part")
    remaining = spec["size"] - (part.stat().st_size if part.exists() else 0)
    if shutil.disk_usage(directory).free < remaining + 512 * 1024 * 1024:
        raise RuntimeError("Not enough disk space for model and verification")
    start = time.perf_counter()
    for attempt in range(6):
        offset = part.stat().st_size if part.exists() else 0
        if offset == spec["size"]:
            break
        headers = {"User-Agent": "LiveTranslate/0.1", "Cache-Control": "no-cache"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            with urlopen(Request(spec["url"], headers=headers), timeout=90) as response:
                resume = offset > 0 and response.status == 206
                if resume and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                    raise RuntimeError("Server returned an unexpected download range")
                with part.open("ab" if resume else "wb") as f:
                    last = time.monotonic()
                    while chunk := response.read(1024 * 1024):
                        f.write(chunk)
                        if time.monotonic() - last > 10:
                            print(f"{dest.name}: {f.tell() / spec['size']:.1%}", flush=True)
                            last = time.monotonic()
            if part.stat().st_size == spec["size"]:
                break
        except (OSError, TimeoutError) as exc:
            print(f"Download attempt {attempt + 1} failed: {exc}", file=sys.stderr, flush=True)
            if attempt == 5:
                raise
            time.sleep(min(2 ** attempt, 15))
    if part.stat().st_size != spec["size"] or sha256(part) != spec["sha256"]:
        raise RuntimeError(f"Incomplete or corrupt model: {part}; not promoted to final path")
    os.replace(part, dest)
    return {"file": str(dest), "verified": True, "reused": False,
            "elapsed_ms": (time.perf_counter() - start) * 1000}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--only", choices=["all", *SPECS], default="all")
    p.add_argument("--metadata-only", action="store_true")
    p.add_argument("--directory", type=Path, default=ROOT / "models")
    p.add_argument("--lock", type=Path, default=ROOT / "config/model-lock.json")
    args = p.parse_args()
    if args.lock.exists():
        specs = json.loads(args.lock.read_text())
    else:
        specs = {name: metadata(*spec) for name, spec in SPECS.items()}
        args.lock.parent.mkdir(parents=True, exist_ok=True)
        args.lock.write_text(json.dumps(specs, indent=2) + "\n")
    if args.metadata_only:
        print(json.dumps(specs, indent=2))
        return 0
    results = {}
    for name, spec in specs.items():
        if args.only in ("all", name):
            results[name] = {**spec, **download(spec, args.directory)}
            print(f"{name}: verified {spec['filename']}", flush=True)
    report = ROOT / "artifacts" / f"models-download-{args.only}.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(results, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Model download failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
