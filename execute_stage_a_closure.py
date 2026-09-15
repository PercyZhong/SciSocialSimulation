#!/usr/bin/env python3
"""Offline CLI for the SciMirror Stage A closure workflow."""
import argparse, json, subprocess, sys, unittest
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parent


# Resolve CLI paths relative to the repository rather than the caller directory.
def project_path(value):
    path=Path(value).expanduser(); return path if path.is_absolute() else ROOT/path


# Run the full repository test suite without network access.
def run_tests():
    count=unittest.defaultTestLoader.discover(str(ROOT/'tests')).countTestCases()
    code=subprocess.call([sys.executable,'-m','unittest','discover','-s','tests','-v'],cwd=ROOT)
    return {'status':'passed' if code==0 else 'failed','test_count':count,'exit_code':code}


# Dispatch closure stages and preserve exit code 2 for completed quality failures.
def main():
    parser=argparse.ArgumentParser(description='SciMirror Stage A closure offline workflow')
    parser.add_argument('command',choices=['check','calibrate','freeze','run','analyze','engine','validate','reproduce','package','test','all'])
    parser.add_argument('--config',default='configs/stage_a_closure.json'); parser.add_argument('--output'); parser.add_argument('--run-dir'); parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args(); config_path=project_path(args.config); output=project_path(args.output or args.run_dir) if args.output or args.run_dir else ROOT/'outputs_stage_a_closure'/datetime.now().strftime('closure_%Y%m%d_%H%M%S')
    from scimirror.stage_a_closure import (analyze,calibrate,engine_validation,freeze,load_config,package,prepare,repository_regression,reproduce,run,validate)
    config=load_config(config_path,ROOT)
    if args.dry_run:
        print(json.dumps({'command':args.command,'output':str(output),'e1_cases':1296,'topic_pair_rows':2376,'network_requests':0,'llm_calls':0},indent=2)); return
    if args.command=='check':
        print(json.dumps({'schema_version':config['schema_version'],'python':sys.version.split()[0],'python_supported':sys.version_info>=(3,11),'config_valid':True,'expected_cases':1296,'network_requests':0,'llm_calls':0},indent=2)); return
    if args.command=='test':
        result=run_tests(); print(json.dumps(result,indent=2)); raise SystemExit(result['exit_code'])
    output.mkdir(parents=True,exist_ok=True)
    if args.command=='calibrate': print(json.dumps(calibrate(config,ROOT,output),indent=2)); return
    if args.command=='freeze': print(json.dumps(freeze(config,config_path,ROOT,output),indent=2)); return
    if args.command=='run':
        if not (output/'FROZEN_PROTOCOL.json').exists(): raise SystemExit('FROZEN_PROTOCOL.json missing; run freeze first')
        rows=run(config,config_path,ROOT,output); print(json.dumps({'rows':len(rows),'output':str(output)},indent=2)); return
    if args.command=='analyze': print(json.dumps(analyze(config,ROOT,output),indent=2)); return
    if args.command=='engine': print(json.dumps({'cases':len(engine_validation(config,ROOT,output))},indent=2)); return
    if args.command=='validate':
        status='passed' if (output/'reproduction'/'REPRODUCTION_RESULT.json').exists() and json.loads((output/'reproduction'/'REPRODUCTION_RESULT.json').read_text(encoding='utf-8'))['status']=='passed' else None
        result=validate(config,ROOT,output,status); print(json.dumps(result,indent=2)); raise SystemExit(0 if result['all_passed'] else 2)
    if args.command=='reproduce': print(json.dumps(reproduce(config,config_path,ROOT,output),indent=2)); return
    if args.command=='package':
        validation=json.loads((output/'DELIVERY_VALIDATION.json').read_text(encoding='utf-8')); print(json.dumps(package(config,ROOT,output,validation),indent=2)); return
    prepare(config,ROOT); tests=run_tests()
    if tests['exit_code']: raise SystemExit(1)
    (output/'tests_report.json').write_text(json.dumps(tests,indent=2)+'\n',encoding='utf-8')
    calibrate(config,ROOT,output); freeze(config,config_path,ROOT,output); rows=run(config,config_path,ROOT,output)
    analysis=analyze(config,ROOT,output); engine=engine_validation(config,ROOT,output); regression=repository_regression(ROOT,output)
    preliminary=validate(config,ROOT,output,None); repro=reproduce(config,config_path,ROOT,output)
    final=validate(config,ROOT,output,repro['status']); archive=package(config,ROOT,output,final)
    result={'run_dir':str(output),'tests':tests,'rows':len(rows),'analysis':analysis,'engine_cases':len(engine),'repository_regression':regression,'reproduction':repro,'validation':final,'archive':archive}
    print(json.dumps(result,indent=2)); raise SystemExit(0 if final['all_passed'] else 2)


if __name__=='__main__': main()
