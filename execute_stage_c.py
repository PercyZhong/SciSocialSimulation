#!/usr/bin/env python3
"""Stage C offline engineering and explicitly authorized live entry point."""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent


def resolve(value):
    path=Path(value).expanduser(); return path if path.is_absolute() else ROOT/path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("command",choices=("doctor","estimate","run","probe","replay","repair-stage-b","finalize"))
    parser.add_argument("--config",default="configs/stage_c_mock.json"); parser.add_argument("--phase",default="c0")
    parser.add_argument("--mode",default="mock"); parser.add_argument("--output"); parser.add_argument("--run-dir")
    parser.add_argument("--archive"); parser.add_argument("--kind",choices=("improvement","candidate")); parser.add_argument("--offline",action="store_true")
    parser.add_argument("--authorize-live",action="store_true")
    parser.add_argument("--model-key")
    args=parser.parse_args()
    from scimirror.model_registry import load_registry,environment_status
    from scimirror.stage_c_frozen import estimate,run_c0
    config_path=resolve(args.config); config=json.loads(config_path.read_text(encoding="utf-8")); registry=load_registry(resolve(config["model_registry"]))
    if args.command=="doctor":
        result={"schema_version":"stage_c_doctor_1","python":sys.version.split()[0],"python_supported":sys.version_info>=(3,11),
                "config_valid":True,"models":environment_status(registry),"live_request_attempted":False,"probe_performed":False}
    elif args.command=="estimate": result=estimate(config,registry)
    elif args.command=="run":
        if args.phase!="c0": raise SystemExit("C1 is prepared but not authorized or executed in this round")
        if args.mode=="live" and not args.authorize_live: raise SystemExit("Live C0 requires --authorize-live and allow_live_calls=true")
        if not args.output: parser.error("run requires --output")
        result=run_c0(config_path,resolve(args.output),args.mode,None)
    elif args.command=="probe":
        if not args.authorize_live: raise SystemExit("Probe is a real API call; explicit --authorize-live is required")
        if not config.get("allow_live_calls"): raise SystemExit("Set allow_live_calls=true in the reviewed local config")
        from scimirror.decision_backend import OpenAICompatibleTransport
        from scimirror.usage_ledger import UsageLedger
        selected=[m for m in registry["models"] if not args.model_key or m["model_key"]==args.model_key]
        if len(selected)!=1: raise SystemExit("Probe exactly one model with --model-key")
        ledger=UsageLedger(config["budget"]); result=OpenAICompatibleTransport(ledger).probe(selected[0])
        result={"model_key":selected[0]["model_key"],**result,"ledger":ledger.export()}
    elif args.command=="replay":
        if not args.run_dir or not args.offline: parser.error("replay requires --run-dir and --offline")
        from scimirror.stage_c_delivery import replay_validate
        result=replay_validate(resolve(args.run_dir))
    elif args.command=="repair-stage-b":
        if not args.archive or not args.output or not args.kind: parser.error("repair-stage-b requires --archive --output --kind")
        from scimirror.stage_c_packaging import repair_stage_b_archive
        result=repair_stage_b_archive(resolve(args.archive),resolve(args.output),args.kind)
    else:
        if not args.run_dir: parser.error("finalize requires --run-dir")
        from scimirror.stage_c_delivery import finalize
        result=finalize(resolve(args.run_dir),ROOT)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if isinstance(result,dict) and result.get("status")=="failed": raise SystemExit(2)


if __name__=="__main__": main()
