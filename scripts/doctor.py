#!/usr/bin/env python3
import argparse
from pathlib import Path
import json
from _bootstrap import ROOT
from livetranslate.config import load_config
from livetranslate.diagnostics import audit, markdown
from livetranslate.errors import LiveTranslateError
from livetranslate.results import save_json


def main():
    p = argparse.ArgumentParser(description="Read-only M0 environment audit; no claim of model GPU inference")
    p.add_argument("--config", type=Path, default=ROOT / "config/default.toml")
    p.add_argument("--json", action="store_true")
    p.add_argument("--output", type=Path)
    p.add_argument("--markdown", type=Path)
    p.add_argument("--hip-smoke", action="store_true", help="compile and verify a tiny gfx1201 HIP kernel in a temporary directory")
    args = p.parse_args()
    try:
        report = audit(load_config(args.config), args.hip_smoke)
    except LiveTranslateError as exc:
        print(json.dumps({"success": False, "error_code": exc.code, "error": str(exc)}))
        return 2
    if args.output:
        save_json(args.output, report)
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(markdown(report), encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for item in report["checks"]:
            state = "PASS" if item["success"] else "FAIL"
            optional = " (optional)" if not item["required"] else ""
            print(f"[{state}] {item['name']}{optional}: {item.get('error') or item.get('stdout', '').split(chr(10))[0]}")
        print("Model GPU inference: not assessed by doctor; requires real model runs and logs.")
    return 0 if report["environment_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
