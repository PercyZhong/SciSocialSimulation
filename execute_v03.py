#!/usr/bin/env python3
"""Cross-platform SciMirror v0.3 check, experiment, review and validation CLI."""
import argparse
import csv
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


# 将相对命令行路径解析为项目根目录下路径。
def project_path(value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT/path


# 加载并验证版本化评审配置。
def load_review_config(path):
    config = json.loads(Path(path).read_text(encoding='utf-8'))
    if config.get('schema_version') != '0.3' or config.get('mode') not in ('human_import','llm_external','mock_review'):
        raise ValueError('Invalid v0.3 review configuration')
    if config.get('rubric_version') != 'scimirror_review_v03_1' or config.get('per_stratum', 0) < 1:
        raise ValueError('Invalid rubric or sampling quota')
    reviewer_ids = [row['id'] for row in config['reviewers']]
    if len(reviewer_ids) != len(set(reviewer_ids)):
        raise ValueError('Duplicate reviewer IDs')
    return config


# 打印不联网的环境、配置、语料和评审资源检查结果。
def check_environment(config_path):
    from scimirror.corpus import Corpus
    from scimirror.v03_pipeline import load_v03_config
    config = load_v03_config(config_path)
    corpus = Corpus(ROOT/config['corpus'], config['cutoff_year'], config['allow_synthetic'])
    result = {'python': sys.version.split()[0], 'python_supported': sys.version_info >= (3,11),
      'platform': sys.platform, 'root': str(ROOT), 'writable': os.access(ROOT, os.W_OK),
      'schema_version': config['schema_version'], 'backend': config['backend'], 'corpus_papers': len(corpus.papers),
      'retrieval_mode': config['retrieval']['mode'], 'paid_api_called': False,
      'external_review_resources': 'not_authorized'}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result['python_supported'] or not result['writable']:
        raise SystemExit(1)


# 解析并执行v0.3的离线模拟、评审和分析命令。
def main():
    parser = argparse.ArgumentParser(description='SciMirror v0.3 offline workflow')
    parser.add_argument('command', choices=['check','test','retrieval-diagnose','run','export-review',
                                           'review-estimate','import-reviews','analyze','validate','review-run'])
    parser.add_argument('--config', default='configs/v03_mock_main.json')
    parser.add_argument('--run-dir'); parser.add_argument('--review-dir'); parser.add_argument('--diagnosis-dir'); parser.add_argument('--input')
    parser.add_argument('--output'); parser.add_argument('--ablation', default='full')
    args = parser.parse_args()
    if sys.version_info < (3,11):
        raise SystemExit('Python >= 3.11 required')
    if args.command == 'check':
        check_environment(project_path(args.config)); return
    if args.command == 'test':
        raise SystemExit(subprocess.call([sys.executable,'-m','unittest','discover','-s','tests','-v'], cwd=ROOT))
    if args.command == 'review-run':
        raise SystemExit('External review execution is disabled: no paid-review authorization. Use export-review/human_import.')
    from scimirror.v03_pipeline import diagnose_retrieval, load_v03_config, run_v03, validate_v03
    if args.command == 'retrieval-diagnose':
        config = load_v03_config(project_path(args.config))
        output = project_path(args.output) if args.output else ROOT/'outputs_v03'/datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')/'retrieval_diagnosis'
        print(diagnose_retrieval(config, output)); return
    if args.command == 'run':
        config = load_v03_config(project_path(args.config))
        output = project_path(args.output) if args.output else ROOT/'outputs_v03'/datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        print(run_v03(config, output, args.ablation)); return
    if not args.review_dir and args.command in ('export-review','review-estimate','import-reviews','analyze'):
        raise SystemExit('--review-dir required')
    if args.command == 'export-review':
        if not args.run_dir:
            raise SystemExit('--run-dir required')
        from scimirror.corpus import Corpus
        from scimirror.v03_review import export_review_package
        review_cfg = load_review_config(project_path(args.config))
        run_dir = project_path(args.run_dir)
        run_cfg = json.loads((run_dir/'config.json').read_text(encoding='utf-8'))
        corpus = Corpus(ROOT/run_cfg['corpus'], run_cfg['cutoff_year'], run_cfg['allow_synthetic'])
        print(json.dumps(export_review_package(run_dir, project_path(args.review_dir), review_cfg, corpus), indent=2)); return
    if args.command == 'review-estimate':
        from scimirror.v03_review import read_csv
        review_dir = project_path(args.review_dir)
        requested = len(read_csv(review_dir/'review_public'/'ratings_template.csv'))
        print(json.dumps({'schema_version':'0.3','requested_reviews':requested,'retry_limit':0,
          'price_estimate':None,'price_null_reason':'no_authorized_provider_or_price_configuration',
          'network_request_made':False}, indent=2)); return
    if args.command == 'import-reviews':
        if not args.input:
            raise SystemExit('--input required')
        from scimirror.v03_analysis import analyze_reviews
        from scimirror.v03_review import import_reviews
        review_dir = project_path(args.review_dir)
        rows = import_reviews(review_dir, project_path(args.input))
        population = json.loads((review_dir/'population_manifest.json').read_text(encoding='utf-8'))
        source_run = Path(population['source_run'])
        result = analyze_reviews(source_run, review_dir, source_run/'analysis')
        print(json.dumps({'valid_rows_total':len(rows), **result}, indent=2)); return
    if args.command == 'analyze':
        if not args.run_dir:
            raise SystemExit('--run-dir required')
        from scimirror.v03_analysis import analyze_reviews, analyze_run
        run_dir = project_path(args.run_dir); review_dir = project_path(args.review_dir)
        config = json.loads((run_dir/'config.json').read_text(encoding='utf-8'))
        run_result = analyze_run(run_dir, config['analysis'])
        review_result = analyze_reviews(run_dir, review_dir, run_dir/'analysis')
        print(json.dumps({'run':run_result,'review':review_result}, indent=2)); return
    if args.command == 'validate':
        if not args.run_dir:
            raise SystemExit('--run-dir required')
        from scimirror.common import dump
        run_dir = project_path(args.run_dir)
        run_config = json.loads((run_dir/'config.json').read_text(encoding='utf-8'))
        simulation = validate_v03(run_dir)
        external = {'status':'not_run','reason':'no_authorized_independent_review_resource'}
        quality = {'status':'awaiting_external_reviews','reason':'no_valid_external_scores'}
        review_validation = None
        if args.review_dir:
            from scimirror.v03_review import validate_review_package
            review_dir = project_path(args.review_dir)
            review_status = json.loads((review_dir/'status.json').read_text(encoding='utf-8'))
            external = {'status': review_status['status'], 'sampled_ideas': review_status.get('sampled_ideas'),
                        'requested_ratings': review_status.get('requested_ratings'),
                        'valid_ratings':review_status.get('valid_rows', 0),
                        'remaining_ratings':review_status.get('remaining_ratings', review_status.get('requested_ratings'))}
            review_validation = validate_review_package(review_dir)
            independence_status = review_validation.get('review_independence_status', 'not_declared')
            independent_review = independence_status in ('independent', 'verified_independent')
            external.update({'review_independence_status': independence_status,
                             'review_scope': review_validation.get('review_scope', 'not_declared'),
                             'independent_review_confirmed': independent_review})
            quality_path = run_dir/'analysis'/'quality_status.json'
            if quality_path.exists():
                quality = json.loads(quality_path.read_text(encoding='utf-8'))
                quality['interpretation_status'] = ('independent_review' if independent_review
                                                    else 'exploratory_nonindependent_review')
        diagnosis = {'status':'not_checked'}
        if args.diagnosis_dir:
            diagnosis_dir = project_path(args.diagnosis_dir)
            diagnosis_status = json.loads((diagnosis_dir/'status.json').read_text(encoding='utf-8'))
            comparisons = list(csv.DictReader((diagnosis_dir/'retrieval_diagnostics.csv').open(encoding='utf-8-sig')))
            probes = (diagnosis_dir/'retrieval_query_probes.jsonl').read_text(encoding='utf-8').splitlines()
            diagnosis = {'status':'completed' if diagnosis_status.get('status') == 'completed' and len(comparisons) >= 4 and len(probes) >= 5 else 'failed',
                         'path':str(diagnosis_dir),'probe_rows':len(probes),'comparison_rows':len(comparisons)}
        if not simulation['all_passed'] or (review_validation and not review_validation['all_passed']) or diagnosis['status'] == 'failed':
            overall_status = 'failed'
        elif not args.review_dir and not args.diagnosis_dir:
            overall_status = 'completed_simulation_only'
        elif quality.get('status') == 'completed' and external.get('independent_review_confirmed'):
            overall_status = 'completed'
        elif quality.get('status') == 'completed':
            overall_status = 'completed_exploratory_review_only'
        elif external.get('status') == 'reviews_partially_imported':
            overall_status = 'completed_with_external_review_incomplete'
        elif external.get('status') in ('reviews_imported_complete','reviews_imported'):
            overall_status = 'completed_with_quality_incomplete'
        else:
            overall_status = 'completed_with_external_review_pending'
        report = {'schema_version':'0.3','declared_scope':'local_mock_and_review_workflow',
          'implementation':{'status':'present'},
          'mock_validation':{'status':'completed' if simulation['all_passed'] and run_config.get('backend') == 'mock' else 'not_applicable_or_failed'},
          'retrieval_diagnosis':diagnosis,
          'simulation':{'status':'completed' if simulation['all_passed'] else 'failed', **simulation},
          'review_sampling':({'status':'completed' if review_validation['all_passed'] else 'failed', **review_validation}
                             if args.review_dir else {'status':'not_checked'}),
          'external_review':external, 'quality_analysis':quality,
          'overall':{'status':overall_status,
                     'paid_api_calls':0,
                     'independent_review_confirmed':external.get('independent_review_confirmed', False),
                     'real_world_causal_claim_supported':False}}
        output = project_path(args.output) if args.output else run_dir/'DELIVERY_VALIDATION_V03.json'
        dump(output, report); print(output)


if __name__ == '__main__':
    main()
