#!/usr/bin/env python3
"""Run and validate the fixed Stage B candidate-size diagnostic."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def resolve(value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT/path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "validate", "reproduce", "finalize"))
    parser.add_argument("--config", default="configs/stage_b_candidate_diagnostic.json")
    parser.add_argument("--source-run"); parser.add_argument("--source-archive")
    parser.add_argument("--run-dir"); parser.add_argument("--output")
    args = parser.parse_args()
    from scimirror.stage_b_candidate_diagnostic import finalize, reproduce, run, validate
    if args.command == "run":
        source = args.source_run or args.source_archive
        if not source or not args.output: parser.error("run requires one source input and --output")
        if args.source_run and args.source_archive: parser.error("choose source-run or source-archive")
        result = run(resolve(args.config), resolve(source), resolve(args.output), ROOT)
    elif args.command == "validate":
        if not args.run_dir: parser.error("validate requires --run-dir")
        result = validate(resolve(args.run_dir))
    elif args.command == "reproduce":
        if not args.run_dir or not args.output: parser.error("reproduce requires --run-dir and --output")
        result = reproduce(resolve(args.run_dir), resolve(args.output))
    else:
        if not args.run_dir: parser.error("finalize requires --run-dir")
        result = finalize(resolve(args.run_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command in ("validate", "reproduce") and result.get("status") != "passed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
