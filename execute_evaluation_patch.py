#!/usr/bin/env python3
"""Offline evaluation patch and real-pilot preparation delivery entry point."""
import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parent


# Resolve user paths relative to the repository root.
def project_path(value):
    path=Path(value).expanduser(); return path if path.is_absolute() else ROOT/path


# Execute the complete repository test suite without external services.
def run_tests():
    count=__import__('unittest').defaultTestLoader.discover(str(ROOT/'tests')).countTestCases(); code=subprocess.call([sys.executable,'-m','unittest','discover','-s','tests'],cwd=ROOT)
    return {'status':'passed' if code==0 else 'failed','test_count':count,'exit_code':code}


# Dispatch patch, reanalysis, pending-input preparation, and packaging.
def main():
    parser=argparse.ArgumentParser(); parser.add_argument('command',choices=['check','test','reanalyze','import-gold-decisions','all'])
    parser.add_argument('--output'); parser.add_argument('--input-run'); parser.add_argument('--previous-run'); parser.add_argument('--input'); parser.add_argument('--review-dir')
    parser.add_argument('--real-corpus'); parser.add_argument('--queries'); parser.add_argument('--family-map'); parser.add_argument('--reviewers')
    args=parser.parse_args(); output=project_path(args.output) if args.output else ROOT/'outputs_evaluation_patch'/datetime.now().strftime('patch_%Y%m%d_%H%M%S')
    from scimirror.evaluation_patch import package,reanalyze,reproduce
    from scimirror.retrieval_gold_audit import export_human_review,import_gold_decisions
    from scimirror.stage_a_repair import dataset_registry,load_config
    config=load_config(ROOT/'configs'/'stage_a_repair.json',ROOT); registry=dataset_registry(config,ROOT)
    if args.command=='check':
        print(json.dumps({'schema_version':'evaluation_patch_1','python':sys.version.split()[0],'python_supported':sys.version_info>=(3,11),'retrieval_changes':False,'calibration_runs_planned':0,'network_requests':0,'llm_calls':0},indent=2)); return
    if args.command=='test':
        result=run_tests(); print(json.dumps(result,indent=2)); raise SystemExit(result['exit_code'])
    if args.command=='reanalyze':
        source=args.input_run or args.previous_run
        if not source: raise SystemExit('--input-run required')
        result=reanalyze(project_path(source),output,ROOT); print(json.dumps(result,indent=2)); return
    if args.command=='import-gold-decisions':
        if not args.input or not args.review_dir: raise SystemExit('--input and --review-dir required')
        result=import_gold_decisions(project_path(args.input),project_path(args.review_dir),ROOT,config,registry); print(json.dumps(result,indent=2)); return
    output.mkdir(parents=True,exist_ok=True); tests=run_tests()
    if tests['exit_code']: raise SystemExit(1)
    source=args.previous_run or args.input_run; candidates=[]
    if source: candidates=[project_path(source)]
    else:
        candidates=[path for path in (ROOT/'outputs_stage_a_repair').glob('*/') if (path/'core_results.jsonl').exists() and (path/'DELIVERY_VALIDATION.json').exists()]
    if len(candidates)==1: reanalysis=reanalyze(candidates[0],output/'reanalysis',ROOT)
    else:
        reanalysis={'status':'missing_previous_run' if not candidates else 'needs_run_selection','candidate_runs':[str(x) for x in candidates]}; (output/'reanalysis').mkdir(exist_ok=True); (output/'reanalysis'/'REANALYSIS_STATUS.json').write_text(json.dumps(reanalysis,indent=2)+'\n')
    human=export_human_review(config,ROOT,output/'human_review',registry)
    small=json.loads((ROOT/'configs'/'stage_b_retrieval_small.json').read_text(encoding='utf-8'))
    from scimirror.stage_b_retrieval import import_corpus,import_family_map,import_queries,import_reviewers,init_pilot
    pilot_dir=output/'pilot_templates'; pilot=init_pilot(pilot_dir,small)
    for value,function in ((args.real_corpus,import_corpus),(args.family_map,import_family_map),(args.queries,import_queries),(args.reviewers,import_reviewers)):
        if value: pilot=function(project_path(value),pilot_dir)
    from scimirror.stage_a_closure import repository_regression
    repository=repository_regression(ROOT,output); reproduction=reproduce(ROOT,output); validation,archive=package(ROOT,output,tests,reanalysis,human,pilot,repository,reproduction)
    result={'output':str(output),'tests':tests,'reanalysis':reanalysis,'human_review':human,'pilot':pilot,'repository_regression':repository,'reproduction':reproduction,'validation':validation,'archive':archive}
    print(json.dumps(result,indent=2,ensure_ascii=False)); raise SystemExit(0 if validation['tool_delivery_completed'] else 1)


if __name__=='__main__': main()
