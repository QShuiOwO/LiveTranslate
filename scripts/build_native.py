#!/usr/bin/env python3
"""Build pinned official sources in this project; preserve any external source tree."""

import argparse
import json
from pathlib import Path
import subprocess
import tarfile
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def prepare_source(name, spec, reusable=None):
    target = ROOT / "vendor" / f"{name}.cpp"
    marker = target / ".livetranslate-revision"
    if target.exists():
        if (target / ".git").exists():
            commit = run(["git", "-C", target, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
            changes = run(["git", "-C", target, "status", "--porcelain"], capture_output=True, text=True).stdout
            # CMake creates a JS package file in whisper.cpp; no source edits are made here.
            if commit != spec["commit"] or changes:
                raise RuntimeError(f"Preserving unrecognized or modified source directory: {target}")
            return target
        if marker.exists() and marker.read_text().strip() == spec["commit"]:
            return target
        raise RuntimeError(f"Preserving existing source directory without a matching revision marker: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if reusable and (reusable / ".git").exists():
        remote = run(["git", "-C", reusable, "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
        if remote.removesuffix(".git") != spec["repo"].removesuffix(".git"):
            raise RuntimeError("Existing Whisper repository is not the expected official remote")
        has_commit = subprocess.run(["git", "-C", str(reusable), "cat-file", "-e", spec["commit"] + "^{commit}"]).returncode == 0
        if has_commit:
            target.mkdir()
            archive = ROOT / "artifacts" / "whisper-source.tar"
            archive.parent.mkdir(parents=True, exist_ok=True)
            with archive.open("wb") as f:
                run(["git", "-C", reusable, "archive", spec["commit"]], stdout=f)
            try:
                with tarfile.open(archive) as tar:
                    tar.extractall(target, filter="data")
            finally:
                archive.unlink(missing_ok=True)
            marker.write_text(spec["commit"] + "\n")
            return target
    run(["git", "init", target])
    run(["git", "-C", target, "remote", "add", "origin", spec["repo"]])
    run(["git", "-C", target, "fetch", "--depth", "1", "origin", spec["commit"]])
    run(["git", "-C", target, "checkout", "--detach", "FETCH_HEAD"])
    return target


def build(name, source, backend, jobs):
    directory = ROOT / "build" / f"{name}-{backend}"
    logs = ROOT / "artifacts"
    logs.mkdir(parents=True, exist_ok=True)
    flags = ["-DCMAKE_BUILD_TYPE=Release", f"-DGGML_VULKAN={'ON' if backend == 'vulkan' else 'OFF'}",
             "-DGGML_CUDA=OFF", "-DGGML_HIP=OFF"]
    if name == "whisper":
        flags += ["-DWHISPER_BUILD_TESTS=OFF", "-DWHISPER_BUILD_SERVER=ON"]
        targets = ["whisper-cli", "whisper-server"]
    else:
        flags += ["-DLLAMA_BUILD_TESTS=OFF", "-DLLAMA_BUILD_EXAMPLES=OFF", "-DLLAMA_BUILD_TOOLS=ON"]
        targets = ["llama-server", "llama-bench"]
    with (logs / f"{name}-{backend}-configure.log").open("w") as f:
        run(["cmake", "-S", source, "-B", directory, *flags], stdout=f, stderr=subprocess.STDOUT, timeout=180)
    with (logs / f"{name}-{backend}-build.log").open("w") as f:
        run(["cmake", "--build", directory, "--target", *targets, "-j", jobs], stdout=f, stderr=subprocess.STDOUT, timeout=1800)
    return directory


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--whisper-source", type=Path, default=Path.home() / "AI/whisper.cpp")
    p.add_argument("--backend", choices=["vulkan", "cpu"], default="vulkan")
    p.add_argument("--jobs", type=int, default=8)
    p.add_argument("--no-cpu-fallback", action="store_true")
    args = p.parse_args()
    if not 1 <= args.jobs <= 32:
        p.error("--jobs must be between 1 and 32")
    lock = json.loads((ROOT / "config/native-lock.json").read_text())
    results = {}
    for name, spec in lock.items():
        source = prepare_source(name, spec, args.whisper_source if name == "whisper" else None)
        start = perf_counter()
        backend = args.backend
        failure = None
        try:
            directory = build(name, source, backend, args.jobs)
        except (subprocess.SubprocessError, OSError) as exc:
            if backend != "vulkan" or args.no_cpu_fallback:
                raise
            failure = str(exc)
            print(f"{name}: Vulkan build failed ({exc}); trying CPU; original logs preserved")
            backend = "cpu"
            directory = build(name, source, backend, args.jobs)
        results[name] = {**spec, "backend": backend, "directory": str(directory),
                         "vulkan_error": failure, "elapsed_ms": (perf_counter() - start) * 1000}
    (ROOT / "artifacts/native-build.json").write_text(json.dumps(results, indent=2) + "\n")
    w, l = results["whisper"], results["llama"]
    # Absolute paths belong only in the ignored machine-local runtime config.
    runtime = (f'[asr]\nbinary = "{w["directory"]}/bin/whisper-cli"\n'
               f'model = "{ROOT}/models/ggml-small.en.bin"\nbackend = "{w["backend"]}"\n'
               f'[server]\nbinary = "{l["directory"]}/bin/llama-server"\n'
               f'model = "{ROOT}/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"\nbackend = "{l["backend"]}"\n'
               f'[output]\ndirectory = "{ROOT}/artifacts"\n')
    (ROOT / "artifacts/native-runtime.toml").write_text(runtime)
    print("Native builds complete. Runtime config: artifacts/native-runtime.toml. GPU inference still requires real validation.")


if __name__ == "__main__":
    main()
