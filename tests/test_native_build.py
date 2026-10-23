import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

from livetranslate.config import Config, load_config


MODULE = importlib.util.spec_from_file_location("native_build", Path(__file__).resolve().parents[1] / "scripts/build_native.py")
native = importlib.util.module_from_spec(MODULE)
MODULE.loader.exec_module(native)


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def repository(tmp_path):
    source = tmp_path / "downloaded"
    source.mkdir()
    git(source, "init", "-b", "main")
    git(source, "remote", "add", "origin", "https://github.com/ggml-org/whisper.cpp.git")
    (source / "source.txt").write_text("committed source\n")
    git(source, "add", "source.txt")
    git(source, "-c", "user.name=Native build test", "-c", "user.email=tests@example.invalid", "commit", "-m", "fixture")
    return source, {"repo": "https://github.com/ggml-org/whisper.cpp.git", "commit": git(source, "rev-parse", "HEAD")}


@pytest.mark.parametrize("commit", ["master", "../escape", "a" * 12, "z" * 40])
def test_native_source_requires_a_full_commit(tmp_path, monkeypatch, commit):
    monkeypatch.setattr(native, "ROOT", tmp_path)
    with pytest.raises(RuntimeError, match="full pinned Git commit"):
        native.prepare_source("whisper", {"repo": "https://github.com/ggml-org/whisper.cpp.git", "commit": commit})
    assert not (tmp_path / "vendor").exists()


def test_native_source_rejects_other_repositories(tmp_path, monkeypatch):
    monkeypatch.setattr(native, "ROOT", tmp_path)
    with pytest.raises(RuntimeError, match="official"):
        native.prepare_source("whisper", {"repo": "https://example.invalid/whisper.cpp.git", "commit": "a" * 40})


def test_reuse_preserves_downloaded_edits_and_old_runtime(tmp_path, monkeypatch):
    root = tmp_path / "project"
    monkeypatch.setattr(native, "ROOT", root)
    source, spec = repository(tmp_path)
    (source / "source.txt").write_text("user edits\n")
    (source / "personal.txt").write_text("keep me\n")
    previous = root / "vendor/whisper.cpp"
    previous.mkdir(parents=True)
    (previous / "old-source.txt").write_text("keep old version\n")

    target = native.prepare_source("whisper", spec, source)
    assert target != previous and target != source
    assert git(target, "rev-parse", "HEAD") == spec["commit"]
    assert git(target, "remote", "get-url", "origin") == spec["repo"]
    assert (target / "source.txt").read_text() == "committed source\n"
    assert (source / "source.txt").read_text() == "user edits\n"
    assert (source / "personal.txt").read_text() == "keep me\n"
    assert (previous / "old-source.txt").read_text() == "keep old version\n"
    assert native.prepare_source("whisper", spec, source) == target

    (target / "source.txt").write_text("edited build source\n")
    with pytest.raises(RuntimeError, match="Preserving"):
        native.prepare_source("whisper", spec, source)
    assert (target / "source.txt").read_text() == "edited build source\n"


def test_unrecognized_version_directory_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(native, "ROOT", tmp_path)
    spec = {"repo": "https://github.com/ggml-org/whisper.cpp.git", "commit": "a" * 40}
    target = tmp_path / "vendor" / ("whisper.cpp-" + "a" * 12)
    target.mkdir(parents=True)
    (target / "sentinel").write_text("preserve")
    with pytest.raises(RuntimeError, match="Git provenance"):
        native.prepare_source("whisper", spec)
    assert (target / "sentinel").read_text() == "preserve"


def test_failed_build_keeps_each_attempt_log_and_old_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(native, "ROOT", tmp_path)
    previous = tmp_path / "build/whisper-vulkan/bin/whisper-cli"
    previous.parent.mkdir(parents=True)
    previous.write_text("old working executable")
    commands = []

    def fail(args, **kwargs):
        commands.append(args)
        kwargs["stdout"].write("compiler error from this attempt\n")
        if "--build" in args:
            raise subprocess.CalledProcessError(17, args)

    monkeypatch.setattr(native, "run", fail)
    spec = {"repo": "https://github.com/ggml-org/whisper.cpp.git", "commit": "a" * 40, "tag": "v1.9.5"}
    for _ in range(2):
        with pytest.raises(RuntimeError, match="build failed; see"):
            native.build("whisper", spec, tmp_path / "source", "vulkan", 2, tmp_path / "reports")
    logs = list((tmp_path / "reports/native-build-logs").glob("*/build.log"))
    assert len(logs) == 2
    assert all("compiler error" in p.read_text() for p in logs)
    assert previous.read_text() == "old working executable"
    assert "-DGGML_VULKAN=ON" in commands[0]
    assert "-DGGML_CUDA=OFF" in commands[0] and "-DGGML_HIP=OFF" in commands[0]
    assert "whisper-cli" in commands[1] and "whisper-server" in commands[1]


def test_active_config_matches_pinned_builds():
    root = Path(__file__).resolve().parents[1]
    lock = json.loads((root / "config/native-lock.json").read_text())
    config = load_config(root / "config/default.toml")
    fallback = Config()
    for actual, relative, name, binary in [
        (config.asr.binary, fallback.asr.binary, "whisper", "whisper-cli"),
        (config.stream.whisper_server, fallback.stream.whisper_server, "whisper", "whisper-server"),
        (config.server.binary, fallback.server.binary, "llama", "llama-server"),
    ]:
        expected = native.build_directory(name, lock[name], "vulkan") / "bin" / binary
        assert Path(actual) == expected
        assert root / relative == expected
