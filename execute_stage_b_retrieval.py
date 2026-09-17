#!/usr/bin/env python3
"""Offline Stage B real-retrieval preparation CLI."""
import argparse, json
from pathlib import Path

ROOT=Path(__file__).resolve().parent


# Resolve paths relative to the repository root.
def project_path(value):
    path=Path(value).expanduser(); return path if path.is_absolute() else ROOT/path


# Dispatch real-corpus preparation without network or synthetic fallback.
def main():
    commands=['init','import-corpus','import-family-map','import-queries','import-reviewers','freeze','pool','export-annotation','import-annotations','export-adjudication','import-adjudication','analyze']
    parser=argparse.ArgumentParser(); parser.add_argument('command',choices=commands)
    parser.add_argument('--config',default='configs/stage_b_retrieval_small.json'); parser.add_argument('--run-dir'); parser.add_argument('--output'); parser.add_argument('--input')
    args=parser.parse_args(); config=json.loads(project_path(args.config).read_text(encoding='utf-8')) if args.command=='init' else None; destination=args.output if args.command=='init' and args.output else args.run_dir
    if not destination: raise SystemExit('--output is required for init; --run-dir is required for other commands')
    run_dir=project_path(destination)
    from scimirror.stage_b_retrieval import (analyze,export_adjudication,export_annotation,freeze,import_adjudication,
      import_annotations,import_corpus,import_family_map,import_queries,import_reviewers,init_pilot,pool)
    if args.command=='init': result=init_pilot(run_dir,config)
    elif args.command.startswith('import-') and not args.input: raise SystemExit('--input required')
    elif args.command=='import-corpus': result=import_corpus(project_path(args.input),run_dir,config)
    elif args.command=='import-family-map': result=import_family_map(project_path(args.input),run_dir,config)
    elif args.command=='import-queries': result=import_queries(project_path(args.input),run_dir,config)
    elif args.command=='import-reviewers': result=import_reviewers(project_path(args.input),run_dir,config)
    elif args.command=='freeze': result=freeze(run_dir,ROOT,config)
    elif args.command=='pool': result=pool(run_dir,config,ROOT)
    elif args.command=='export-annotation': result=export_annotation(run_dir)
    elif args.command=='import-annotations': result=import_annotations(project_path(args.input),run_dir)
    elif args.command=='export-adjudication': result=export_adjudication(run_dir)
    elif args.command=='import-adjudication': result=import_adjudication(project_path(args.input),run_dir)
    else: result=analyze(run_dir)
    print(json.dumps(result,indent=2,ensure_ascii=False))
    if args.command=='analyze' and result['status']!='analysis_completed': raise SystemExit(2)


if __name__=='__main__': main()
