#!/usr/bin/env python3
"""Offline Stage A repair and Stage B preparation entry point."""
import argparse, json, subprocess, sys, unittest
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parent


# Resolve CLI paths relative to the repository root.
def project_path(value):
    path=Path(value).expanduser(); return path if path.is_absolute() else ROOT/path


# Run the complete unittest suite without external services.
def run_tests():
    count=unittest.defaultTestLoader.discover(str(ROOT/'tests')).countTestCases(); code=subprocess.call([sys.executable,'-m','unittest','discover','-s','tests'],cwd=ROOT)
    return {'status':'passed' if code==0 else 'failed','test_count':count,'exit_code':code}


# Execute a requested repair stage and retain exit code 2 for transparent limitations.
def main():
    parser=argparse.ArgumentParser(); parser.add_argument('command',choices=['check','audit-gold','calibrate','freeze','run','analyze','engine','validate','reproduce','package','test','all'])
    parser.add_argument('--config',default='configs/stage_a_repair.json'); parser.add_argument('--output'); parser.add_argument('--run-dir'); parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args(); config_path=project_path(args.config); output=project_path(args.output or args.run_dir) if args.output or args.run_dir else ROOT/'outputs_stage_a_repair'/datetime.now().strftime('repair_%Y%m%d_%H%M%S')
    from scimirror.stage_a_repair import (analyze,calibrate,dataset_registry,engine_and_repository,engine_only,freeze,load_config,package,reproduce,run,validate)
    from scimirror.retrieval_gold_audit import audit_gold
    config=load_config(config_path,ROOT); registry=dataset_registry(config,ROOT)
    if args.dry_run:
        print(json.dumps({'command':args.command,'output':str(output),'e1_logical_cases':1944,'calibration_trials':len(config['calibration_grid']),'network_requests':0,'llm_calls':0},indent=2)); return
    if args.command=='check':
        print(json.dumps({'schema_version':config['schema_version'],'python':sys.version.split()[0],'python_supported':sys.version_info>=(3,11),'config_valid':True,
          'old_raw_documents':registry['supplement_unchanged']['raw_document_count'],'old_eligible_documents':registry['supplement_unchanged']['document_count'],'challenge_documents':registry['verification']['closure_unique_challenge_documents'],
          'challenge_annotations':registry['verification']['closure_challenge_topic_annotations'],'expected_e1_cases':1944,'network_requests':0,'llm_calls':0},indent=2)); return
    if args.command=='test': result=run_tests(); print(json.dumps(result,indent=2)); raise SystemExit(result['exit_code'])
    output.mkdir(parents=True,exist_ok=True); (output/'DATASET_REGISTRY.json').write_text(json.dumps(registry,indent=2)+'\n',encoding='utf-8')
    if args.command=='audit-gold':
        result=audit_gold(config,ROOT,output,registry); (output/'gold_audit_status.json').write_text(json.dumps(result,indent=2)+'\n'); print(json.dumps(result,indent=2)); return
    if args.command=='calibrate': print(json.dumps(calibrate(config,ROOT,output,registry),indent=2)); return
    if args.command=='freeze': print(json.dumps(freeze(config,config_path,ROOT,output,registry),indent=2)); return
    if args.command=='run':
        if not (output/'FROZEN_PROTOCOL.json').exists(): raise SystemExit('FROZEN_PROTOCOL.json missing; run measured calibration and freeze first')
        print(json.dumps({'rows':len(run(config,config_path,ROOT,output,registry)),'output':str(output)},indent=2)); return
    if args.command=='analyze': print(json.dumps(analyze(config,ROOT,output,registry),indent=2)); return
    if args.command=='engine': print(json.dumps({'engine_cases':len(engine_only(config,ROOT,output,registry))},indent=2)); return
    if args.command=='reproduce': print(json.dumps(reproduce(config,config_path,ROOT,output),indent=2)); return
    if args.command=='validate':
        repro=json.loads((output/'reproduction'/'REPRODUCTION_RESULT.json').read_text()) if (output/'reproduction'/'REPRODUCTION_RESULT.json').exists() else {'status':None}
        result=validate(config,ROOT,output,registry,repro['status']); print(json.dumps(result,indent=2)); raise SystemExit(0 if result['all_required_a_checks_passed'] else 2)
    if args.command=='package':
        result=package(config,config_path,ROOT,output,registry,json.loads((output/'DELIVERY_VALIDATION.json').read_text())); print(json.dumps(result,indent=2)); return
    tests=run_tests(); (output/'tests_report.json').write_text(json.dumps(tests,indent=2)+'\n')
    if tests['exit_code']: raise SystemExit(1)
    audit=audit_gold(config,ROOT,output,registry); (output/'gold_audit_status.json').write_text(json.dumps(audit,indent=2)+'\n')
    calibration=calibrate(config,ROOT,output,registry); freeze(config,config_path,ROOT,output,registry); rows=run(config,config_path,ROOT,output,registry); analysis=analyze(config,ROOT,output,registry)
    integration=engine_and_repository(config,ROOT,output,registry)
    from scimirror.stage_b_retrieval import init_pilot
    stage_b=init_pilot(output/'stage_b_prep',json.loads((ROOT/'configs'/'stage_b_retrieval_pilot.json').read_text()))
    reproduction=reproduce(config,config_path,ROOT,output); validation=validate(config,ROOT,output,registry,reproduction['status']); archive=package(config,config_path,ROOT,output,registry,validation)
    result={'run_dir':str(output),'tests':tests,'audit':audit,'calibration':calibration,'rows':len(rows),'analysis':analysis,'engine_cases':len(integration['engine']),
      'repository_regression':integration['repository'],'stage_b':stage_b,'reproduction':reproduction,'validation':validation,'archive':archive}
    print(json.dumps(result,indent=2)); raise SystemExit(0 if validation['all_required_a_checks_passed'] else 2)


if __name__=='__main__': main()
