#!/usr/bin/env python3
"""Build pinned upstream sources in this project; preserve any external source tree."""

import argparse
import json
from pathlib import Path
import re
import subprocess
from time import perf_counter
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
REPOSITORIES = {"whisper": "https://github.com/ggml-org/whisper.cpp.git",
                "llama": "https://github.com/ggml-org/llama.cpp.git",
                "speech": "https://github.com/nyosegawa/speech.cpp.git"}


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def revision(name, spec):
    if name not in REPOSITORIES or spec.get("repo") != REPOSITORIES[name]:
        raise RuntimeError(f"Expected the official upstream repository for {name}")
    commit = spec.get("commit", "")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError(f"Expected a full pinned Git commit for {name}")
    return commit


def build_directory(name, spec, backend):
    return ROOT / "build" / f"{name}-{backend}-{revision(name, spec)[:12]}"


def prepare_source(name, spec, reusable=None):
    commit = revision(name, spec)
    target = ROOT / "vendor" / f"{name}.cpp-{commit[:12]}"
    if target.exists():
        if (target / ".git").exists():
            actual = run(["git", "-C", target, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
            remote = run(["git", "-C", target, "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
            # CMake may generate untracked files; tracked source changes are never overwritten.
            changes = run(["git", "-C", target, "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True).stdout
            if actual != commit or changes or remote.removesuffix(".git") != spec["repo"].removesuffix(".git"):
                raise RuntimeError(f"Preserving unrecognized or modified source directory: {target}")
            return target
        raise RuntimeError(f"Preserving existing source directory without Git provenance: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    candidates = [Path(reusable).expanduser().resolve()] if reusable else []
    candidates.append(ROOT / "vendor" / f"{name}.cpp")
    for candidate in candidates:
        if not (candidate / ".git").exists():
            continue
        remote = run(["git", "-C", candidate, "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
        if remote.removesuffix(".git") != spec["repo"].removesuffix(".git"):
            raise RuntimeError(f"Existing {name} repository is not the expected official remote: {candidate}")
        has_commit = subprocess.run(["git", "-C", str(candidate), "cat-file", "-e", commit + "^{commit}"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        if has_commit:
            # Copy committed objects, not local edits, and keep accurate native build metadata.
            run(["git", "clone", "--no-hardlinks", "--no-checkout", candidate, target])
            run(["git", "-C", target, "remote", "set-url", "origin", spec["repo"]])
            run(["git", "-C", target, "checkout", "--detach", commit])
            return target
    run(["git", "init", target])
    run(["git", "-C", target, "remote", "add", "origin", spec["repo"]])
    run(["git", "-C", target, "fetch", "--depth", "1", "origin", commit])
    run(["git", "-C", target, "checkout", "--detach", "FETCH_HEAD"])
    return target


def build(name, spec, source, backend, jobs, output_dir):
    directory = build_directory(name, spec, backend)
    logs = output_dir / "native-build-logs" / f"{directory.name}-{uuid4().hex[:8]}"
    logs.mkdir(parents=True)
    flags = ["-DCMAKE_BUILD_TYPE=Release", f"-DGGML_VULKAN={'ON' if backend == 'vulkan' else 'OFF'}",
             "-DGGML_CUDA=OFF", "-DGGML_HIP=OFF"]
    if name == "whisper":
        flags += ["-DWHISPER_BUILD_TESTS=OFF", "-DWHISPER_BUILD_SERVER=ON"]
        if spec.get("tag"):
            flags += ["-DWHISPER_BUILD_IS_DEV=OFF"]
        targets = ["whisper-cli", "whisper-server"]
    elif name == "speech":
        flags += ["-DGGML_OPENMP=OFF"]
        targets = ["speech-cli"]
    else:
        flags += ["-DLLAMA_BUILD_TESTS=OFF", "-DLLAMA_BUILD_EXAMPLES=OFF", "-DLLAMA_BUILD_TOOLS=ON",
                  "-DLLAMA_BUILD_SERVER=ON", "-DLLAMA_BUILD_APP=OFF", "-DLLAMA_BUILD_UI=OFF", "-DLLAMA_BUILD_MTMD=OFF"]
        targets = ["llama-server", "llama-bench"]
    print(f"Building {name}/{backend} at {spec['commit'][:12]}; logs: {logs}", flush=True)
    try:
        with (logs / "configure.log").open("w") as f:
            run(["cmake", "-S", source, "-B", directory, *flags], stdout=f, stderr=subprocess.STDOUT, timeout=180)
        with (logs / "build.log").open("w") as f:
            run(["cmake", "--build", directory, "--target", *targets, "-j", jobs], stdout=f, stderr=subprocess.STDOUT, timeout=1800)
    except (subprocess.SubprocessError, OSError) as exc:
        raise RuntimeError(f"{name}/{backend} build failed; see {logs}") from exc
    return {"directory": str(directory), "configure_log": str(logs / "configure.log"), "build_log": str(logs / "build.log")}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--whisper-source", type=Path, default=Path.home() / "AI/whisper.cpp")
    p.add_argument("--llama-source", type=Path)
    p.add_argument("--speech-source", type=Path)
    p.add_argument("--lock", type=Path, default=ROOT / "config/native-lock.json")
    p.add_argument("--output-dir", type=Path, default=ROOT / "artifacts")
    p.add_argument("--backend", choices=["vulkan", "cpu"], default="vulkan")
    p.add_argument("--jobs", type=int, default=8)
    p.add_argument("--no-cpu-fallback", action="store_true")
    args = p.parse_args()
    if not 1 <= args.jobs <= 32:
        p.error("--jobs must be between 1 and 32")
    lock = json.loads(args.lock.read_text())
    if set(lock) not in ({"whisper", "llama"}, {"whisper", "llama", "speech"}):
        p.error("The native lock must contain whisper and llama, optionally speech")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    for name, spec in lock.items():
        source = prepare_source(name, spec, getattr(args, name + "_source"))
        if name == "speech":
            run(["git", "-C", source, "submodule", "update", "--init", "--recursive"], timeout=600)
            actual = run(["git", "-C", source / "ggml", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
            if actual != spec.get("ggml_commit"):
                raise RuntimeError("speech.cpp ggml submodule does not match native-lock.json")
        start = perf_counter()
        backend = args.backend
        failure = None
        try:
            built = build(name, spec, source, backend, args.jobs, args.output_dir)
        except RuntimeError as exc:
            if backend != "vulkan" or args.no_cpu_fallback:
                raise
            failure = str(exc)
            print(f"{name}: Vulkan build failed ({exc}); trying CPU; original logs preserved")
            backend = "cpu"
            built = build(name, spec, source, backend, args.jobs, args.output_dir)
        results[name] = {**spec, **built, "source_directory": str(source), "backend": backend,
                         "vulkan_error": failure, "elapsed_ms": (perf_counter() - start) * 1000}
    (args.output_dir / "native-build.json").write_text(json.dumps(results, indent=2) + "\n")
    w, l = results["whisper"], results["llama"]
    # Absolute paths belong only in the ignored machine-local runtime config.
    runtime = (f'[asr]\nbinary = "{w["directory"]}/bin/whisper-cli"\n'
               f'model = "{ROOT}/models/ggml-small.en.bin"\nbackend = "{w["backend"]}"\n'
               f'[server]\nbinary = "{l["directory"]}/bin/llama-server"\n'
               f'model = "{ROOT}/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"\nbackend = "{l["backend"]}"\n'
               f'[stream]\nwhisper_server = "{w["directory"]}/bin/whisper-server"\n'
               f'[output]\ndirectory = "{ROOT}/artifacts"\n')
    if "speech" in results:
        a = results["speech"]
        runtime = (f'[asr]\nengine = "qwen3"\nbinary = "{a["directory"]}/speech"\n'
                   f'model = "{ROOT}/models/Qwen3-ASR-1.7B-Q8_0.gguf"\nbackend = "{a["backend"]}"\n'
                   f'[translation]\nmodel = "Hy-MT2-7B-Q4_K_M"\nprompt_style = "hy_mt"\n'
                   f'[server]\nbinary = "{l["directory"]}/bin/llama-server"\n'
                   f'model = "{ROOT}/models/Hy-MT2-7B-Q4_K_M.gguf"\nbackend = "{l["backend"]}"\n'
                   f'[stream]\nwhisper_server = "{w["directory"]}/bin/whisper-server"\n'
                   f'[output]\ndirectory = "{ROOT}/artifacts"\n')
    runtime_path = args.output_dir / "native-runtime.toml"
    runtime_path.write_text(runtime)
    print(f"Native builds complete. Runtime config: {runtime_path}. GPU inference still requires real validation.")


if __name__ == "__main__":
    main()
