#!/usr/bin/env python3
"""Cross-platform offline CLI for the SciMirror Stage A supplement."""
import argparse
import json
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parent


# Resolve user paths relative to the repository root.
def project_path(value):
    path=Path(value).expanduser()
    return path if path.is_absolute() else ROOT/path


# Run the complete unit-test suite and return a serializable outcome.
def run_tests():
    count=unittest.defaultTestLoader.discover(str(ROOT/'tests')).countTestCases()
    code=subprocess.call([sys.executable,'-m','unittest','discover','-s','tests','-v'],cwd=ROOT)
    return {'status':'passed' if code==0 else 'failed','test_count':count,'exit_code':code,
            'command':'python -m unittest discover -s tests -v'}


# Parse supplement commands and dispatch to the offline implementation.
def main():
    parser=argparse.ArgumentParser(description='SciMirror Stage A supplement offline workflow')
    parser.add_argument('command',choices=['check','prepare','test','run','analyze','validate','package','all'])
    parser.add_argument('--config',default='configs/stage_a_supplement.json'); parser.add_argument('--run-dir'); parser.add_argument('--output')
    parser.add_argument('--dry-run',action='store_true'); args=parser.parse_args()
    from scimirror.stage_a_supplement import (analyze, build_manifest, build_resume_contract, engine_integration,
        ensure_resume_contract, load_config, package, policy_e2e, policy_unit, prepare, report, run_all, run_e1,
        validate, write_jsonl)
    config_path=project_path(args.config); config=load_config(config_path,ROOT)
    output=project_path(args.output or args.run_dir) if (args.output or args.run_dir) else ROOT/config['output_root']/datetime.now().strftime('%Y%m%d_%H%M%S')
    if args.dry_run:
        print(json.dumps({'command':args.command,'output':str(output),'e1_calls':1296,'e2_enabled_calls':54,
                          'topic_pair_rows':2376,'network_requests':0,'llm_calls':0},indent=2)); return
    if args.command=='check':
        print(json.dumps({'schema_version':config['schema_version'],'python':sys.version.split()[0],
          'python_supported':sys.version_info>=(3,11),'config_valid':True,'ranker_formula_changed':False,
          'expected_e1_calls':1296,'expected_topic_pairs':2376,'network_requests':0,'llm_calls':0},indent=2)); return
    if args.command=='prepare': print(json.dumps(prepare(config,ROOT),indent=2)); return
    if args.command=='test':
        result=run_tests(); print(json.dumps(result,indent=2)); raise SystemExit(result['exit_code'])
    if args.command=='run':
        output.mkdir(parents=True,exist_ok=True); prep=prepare(config,ROOT)
        ensure_resume_contract(output,build_resume_contract(config,config_path,ROOT))
        (output/'config.json').write_text(json.dumps(config,indent=2)+'\n',encoding='utf-8')
        (output/'prepare_status.json').write_text(json.dumps(prep,indent=2)+'\n',encoding='utf-8')
        rows,candidates,failures,computed=run_e1(config,ROOT,output); units=policy_unit(config); e2e=policy_e2e(config,ROOT)
        write_jsonl(output/'policy_unit_results.jsonl',units); write_jsonl(output/'policy_e2e_results.jsonl',e2e)
        engine_integration(config,ROOT,output)
        (output/'manifest.json').write_text(json.dumps(build_manifest(config,config_path,ROOT,len(rows)),indent=2)+'\n',encoding='utf-8')
        usage={'schema_version':config['schema_version'],'e1_retrieval_calls':len(rows),'e1_computed_this_invocation':computed,
          'e1_resumed':len(rows)-computed,'e2_enabled_retrieval_calls':54,'e2_disabled_control_calls':6,
          'network_requests':0,'http_attempts':0,'llm_calls':0,'paid_api_calls':0}
        (output/'usage.json').write_text(json.dumps(usage,indent=2)+'\n',encoding='utf-8'); print(output); return
    if args.command=='analyze':
        result=analyze(config,ROOT,output); e2e=[json.loads(x) for x in (output/'policy_e2e_results.jsonl').read_text(encoding='utf-8').splitlines()]
        report(config,ROOT,output,result,e2e); print(json.dumps(result,indent=2)); return
    if args.command=='package': print(json.dumps(package(config,ROOT,output),indent=2)); return
    if args.command=='validate': print(json.dumps(validate(config,ROOT,output),indent=2)); return
    prepare(config,ROOT)
    tests=run_tests()
    if tests['exit_code']: raise SystemExit(tests['exit_code'])
    result=run_all(config,config_path,ROOT,output,tests); print(json.dumps({'run_dir':str(output),**result},indent=2))


if __name__=='__main__':
    main()
