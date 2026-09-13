#!/usr/bin/env python3
"""Cross-platform offline CLI for the SciMirror Stage A retrieval experiment."""
import argparse
import json
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parent


# Resolve command-line paths relative to the repository root.
def project_path(value):
    path=Path(value).expanduser()
    return path if path.is_absolute() else ROOT/path


# Parse arguments and execute one resumable Stage A workflow command.
def main():
    parser=argparse.ArgumentParser(description='SciMirror Stage A offline frozen retrieval workflow')
    parser.add_argument('command',choices=['check','test','calibrate','run','analyze','validate','all'])
    parser.add_argument('--config',default='configs/stage_a_frozen_retrieval.json')
    parser.add_argument('--run-dir'); parser.add_argument('--output'); parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    from scimirror.stage_a import (analyze, calibrate, engine_regression, load_stage_a_config,
                                   run_all, run_core, validate, write_manifest, write_report)
    config_path=project_path(args.config); config=load_stage_a_config(config_path,ROOT)
    output=project_path(args.output or args.run_dir) if (args.output or args.run_dir) else ROOT/config['output_root']/datetime.now().strftime('%Y%m%d_%H%M%S')
    if args.dry_run:
        print(json.dumps({'command':args.command,'config':str(config_path),'output':str(output),
            'expected_states':108,'expected_core_cases':648,'network_requests':0,'llm_calls':0},indent=2)); return
    if args.command=='check':
        result={'schema_version':'stage_a_1','python':sys.version.split()[0],'python_supported':sys.version_info>=(3,11),
          'config_valid':True,'topics':len(json.loads((ROOT/config['topics']).read_text(encoding='utf-8'))),
          'fields':len(config['fields']),'expected_states':108,'expected_core_cases':648,
          'engine_integration':engine_regression(config,ROOT),'network_requests':0,'llm_calls':0}
        print(json.dumps(result,indent=2)); raise SystemExit(0 if result['python_supported'] and result['engine_integration']['passed'] else 1)
    if args.command=='test':
        raise SystemExit(subprocess.call([sys.executable,'-m','unittest','discover','-s','tests','-v'],cwd=ROOT))
    if args.command=='calibrate':
        print(json.dumps(calibrate(config,ROOT,output),indent=2)); return
    if args.command=='run':
        output.mkdir(parents=True,exist_ok=True); (output/'config.json').write_text(json.dumps(config,indent=2)+'\n',encoding='utf-8')
        engine=engine_regression(config,ROOT); write_manifest(config,config_path,ROOT,output,engine)
        result=run_core(config,ROOT,output)
        usage={'schema_version':'stage_a_1','core_retrieval_calls':len(result['rows']),'computed_this_invocation':result['computed_now'],
               'resumed_cases':result['resumed'],'candidate_diagnostic_recomputations':len(result['rows']),
               'network_requests':0,'http_attempts':0,'llm_calls':0,'paid_api_calls':0}
        (output/'usage.json').write_text(json.dumps(usage,indent=2)+'\n',encoding='utf-8')
        (output/'status.json').write_text(json.dumps({'schema_version':'stage_a_1','status':'core_completed','core_cases':len(result['rows'])},indent=2)+'\n',encoding='utf-8')
        print(output); return
    if args.command=='analyze':
        result=analyze(output); write_report(output,config,result); print(json.dumps(result,indent=2)); return
    if args.command=='validate':
        print(json.dumps(validate(output),indent=2)); return
    test_count=unittest.defaultTestLoader.discover(str(ROOT/'tests')).countTestCases()
    test_code=subprocess.call([sys.executable,'-m','unittest','discover','-s','tests','-v'],cwd=ROOT)
    if test_code:
        raise SystemExit(test_code)
    output.mkdir(parents=True,exist_ok=True)
    (output/'test_status.json').write_text(json.dumps({'status':'passed','tests':test_count,
        'command':'python -m unittest discover -s tests -v'},indent=2)+'\n',encoding='utf-8')
    (output/'environment_check.json').write_text(json.dumps({'schema_version':'stage_a_1',
        'python':sys.version,'python_supported':sys.version_info>=(3,11),'config_valid':True,
        'engine_integration':engine_regression(config,ROOT),'network_requests':0,'llm_calls':0},indent=2)+'\n',encoding='utf-8')
    result=run_all(config,config_path,ROOT,output); print(json.dumps({'run_dir':str(output),'tests_passed':True,**result},indent=2))


if __name__=='__main__':
    main()
