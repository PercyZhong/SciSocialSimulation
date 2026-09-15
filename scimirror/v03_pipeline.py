"""SciMirror v0.3 orchestration around the compatible v0.2 state engine."""
import copy
import csv
import json
import platform
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from .common import canonical, digest, dump
from .corpus import Corpus
from .policy import context
from .topics import TopicModel
from .v02_experiment import ROOT, run_v02
from .v03_analysis import analyze_run
from .v03_retrieval import audit_metrics, document_overlap, retrieve_relevance_gated
from .v03_review import write_csv


# 加载并严格验证v0.3模拟配置和检索协议。
def load_v03_config(path):
    config = json.loads(Path(path).read_text(encoding='utf-8'))
    if config.get('schema_version') != '0.3':
        raise ValueError('v0.3 configuration required')
    retrieval = config.get('retrieval', {})
    required = {'mode','query_weights','minimum_relevance','candidate_pool_size','top_k',
                'relevance_weight','near_duplicate_threshold','max_per_near_duplicate_cluster','version'}
    if not required <= set(retrieval) or retrieval['mode'] not in ('legacy_v02','relevance_gated','stage_a_fixed','stage_a_semantic_guarded','stage_a_repaired_v1'):
        raise ValueError('Incomplete or invalid v0.3 retrieval configuration')
    if abs(sum(retrieval['query_weights'].values())-1) > 1e-12:
        raise ValueError('Query weights must sum to one')
    if not 0 <= retrieval['minimum_relevance'] <= 1 or not 0 <= retrieval['relevance_weight'] <= 1:
        raise ValueError('Invalid retrieval weights or threshold')
    if retrieval['top_k'] > retrieval['candidate_pool_size']:
        raise ValueError('top_k exceeds common candidate pool')
    analysis = config.get('analysis', {})
    schemes = analysis.get('quality_weight_schemes', {})
    if schemes and ('equal' not in schemes or any(set(weights) != {'novelty','feasibility','scientific_value','evidence_support'}
                   or any(value < 0 for value in weights.values()) or abs(sum(weights.values())-1) > 1e-12
                   for weights in schemes.values())):
        raise ValueError('Invalid quality sensitivity weights')
    if analysis.get('bootstrap_repetitions', 0) < 2:
        raise ValueError('Quality bootstrap requires at least two repetitions')
    return config


# 将v0.3配置显式适配到保留的v0.2状态机而不改变旧配置文件。
def simulation_adapter(config):
    adapted = copy.deepcopy(config)
    adapted['schema_version'] = '0.2'
    adapted['cache_protocol'] = 3
    adapted['retrieval_candidate_pool'] = adapted['retrieval']['candidate_pool_size']
    adapted['retrieval_top_k'] = adapted['retrieval']['top_k']
    if adapted['retrieval']['mode'] == 'legacy_v02':
        adapted['retrieval'] = None
    return adapted


# 从决策审计导出v0.3实时检索事件和逐查询诊断。
def export_live_retrieval(run_dir):
    run_dir = Path(run_dir)
    records = []
    for line in (run_dir/'decision_audit.jsonl').read_text(encoding='utf-8').splitlines():
        item = json.loads(line)
        if item.get('stage') == 'retrieval' and item.get('schema_version') == '0.3':
            branch = item.pop('branch')
            seed_part, condition = branch.split('/', 1)
            policy, network = condition.rsplit('_', 1)
            item.update(branch=branch, seed=int(seed_part.split('_')[1]), policy=policy, network=network)
            records.append(item)
    (run_dir/'live_retrieval_audit.jsonl').write_text(''.join(canonical(row)+'\n' for row in records), encoding='utf-8')
    diagnostics = []
    for item in records:
        metrics = audit_metrics(item)
        diagnostics.append({'seed': item['seed'], 'policy': item['policy'], 'network': item['network'],
                            'agent_id': item['agent'], 'tick': item['tick'], **metrics})
    balanced = {(row['seed'],row['network'],row['agent_id'],row['tick']): row for row in diagnostics if row['policy'] == 'balanced'}
    audits = {(item['seed'],item['network'],item['agent'],item['tick'],item['policy']): item for item in records}
    for row in diagnostics:
        base = balanced.get((row['seed'],row['network'],row['agent_id'],row['tick']))
        current = audits[(row['seed'],row['network'],row['agent_id'],row['tick'],row['policy'])]
        if base is None or row['policy'] == 'balanced':
            row.update(same_query_policy_jaccard=None, same_query_policy_intersection_over_min=None,
                       same_query_policy_reason='control_or_missing_pair')
            continue
        base_audit = audits[(base['seed'],base['network'],base['agent_id'],base['tick'],'balanced')]
        if base['query_id'] != row['query_id']:
            row.update(same_query_policy_jaccard=None, same_query_policy_intersection_over_min=None,
                       same_query_policy_reason='query_changed_in_live_trajectory')
        else:
            overlap = document_overlap(base_audit['selected_ids'], current['selected_ids'])
            row.update(same_query_policy_jaccard=overlap['jaccard'],
                       same_query_policy_intersection_over_min=overlap['intersection_over_min'],
                       same_query_policy_reason=overlap['reason'])
    write_csv(run_dir/'retrieval_diagnostics.csv', diagnostics)
    return {'retrieval_records': len(records), 'diagnostic_rows': len(diagnostics),
            'shortfalls': sum(row['retrieval_shortfall'] for row in diagnostics)}


# 执行v0.3模拟、重放兼容状态并生成检索和数量分析归档。
def run_v03(config, output, ablation='full'):
    output = Path(output)
    adapted = simulation_adapter(config)
    run_v02(adapted, output, ablation)
    retrieval = export_live_retrieval(output)
    analysis = analyze_run(output, config['analysis'])
    old_manifest = json.loads((output/'manifest.json').read_text(encoding='utf-8'))
    dump(output/'config.json', config | {'ablation': ablation})
    dump(output/'manifest.json', {'schema_version': '0.3', 'python': sys.version, 'platform': platform.platform(),
         'config_hash': digest(config), 'simulation_adapter': 'v03_to_v02_state_engine_1',
         'adapted_config_hash': old_manifest['config_hash'], 'source_hash': old_manifest['source_hash'],
         'corpus_hash': old_manifest['corpus_hash'], 'topic_snapshot_hash': old_manifest['topic_snapshot_hash'],
         'candidate_schedule_hashes': old_manifest['candidate_schedule_hashes'], 'backend': config['backend'],
         'synthetic_corpus': old_manifest['synthetic_corpus'], 'cache_protocol': 3,
         'retrieval': retrieval, 'analysis': analysis,
         'scientific_status': 'v0.3 synthetic mock engineering validation; external quality review not performed'})
    dump(output/'status.json', {'schema_version': '0.3', 'status': 'completed', 'ablation': ablation,
         'external_review': 'not_part_of_simulation_run', 'http_attempts': 0})
    return output


# 执行legacy与relevance-gated冻结状态查询反事实探针。
def diagnose_retrieval(config, output):
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    corpus = Corpus(ROOT/config['corpus'], config['cutoff_year'], config['allow_synthetic'])
    topics = TopicModel(ROOT/config['topics'], corpus.papers, config['recognition']['alpha'])
    probes = []
    base = {'field': 'agents', 'memory': ['agent communication'], 'topic': 'agent_memory'}
    for policy in ('balanced','novelty','recognition'):
        policy_context = context(config, policy, 'retrieval')
        papers, audit = retrieve_relevance_gated(corpus.papers, topics, policy_context, config['retrieval'],
                                                  base['topic'], base['field'], base['memory'], [])
        probes.append({'probe': 'same_query_policy', 'variant': policy, 'selected_ids': [p['id'] for p in papers], 'audit': audit})
    for topic_id in ('agent_memory','agent_tools'):
        papers, audit = retrieve_relevance_gated(corpus.papers, topics, context(config,'balanced','retrieval'),
                                                  config['retrieval'], topic_id, base['field'], base['memory'], [])
        probes.append({'probe': 'changed_topic', 'variant': topic_id, 'selected_ids': [p['id'] for p in papers], 'audit': audit})
    legacy_a = [p['id'] for p in corpus.retrieve('agents memory retrieval', 3)]
    legacy_b = [p['id'] for p in corpus.retrieve('retrieval memory agents', 3)]
    probes.extend([{'probe': 'legacy_equivalent_query', 'variant': 'a', 'selected_ids': legacy_a},
                   {'probe': 'legacy_equivalent_query', 'variant': 'b', 'selected_ids': legacy_b}])
    same_policy = [row for row in probes if row['probe'] == 'same_query_policy']
    changed = [row for row in probes if row['probe'] == 'changed_topic']
    rows = []
    for left, right, label in [(same_policy[0], same_policy[1], 'same_query_balanced_vs_novelty'),
                               (same_policy[0], same_policy[2], 'same_query_balanced_vs_recognition'),
                               (changed[0], changed[1], 'changed_topic_memory_vs_tools'),
                               (probes[-2], probes[-1], 'legacy_equivalent_expression')]:
        rows.append({'comparison': label, **document_overlap(left['selected_ids'], right['selected_ids']),
                     'left_selected': ';'.join(left['selected_ids']), 'right_selected': ';'.join(right['selected_ids'])})
    (output/'retrieval_query_probes.jsonl').write_text(''.join(canonical(row)+'\n' for row in probes), encoding='utf-8')
    write_csv(output/'retrieval_diagnostics.csv', rows)
    changed_overlap = next(row for row in rows if row['comparison'].startswith('changed_topic'))
    report = ['# Retrieval before/after diagnosis', '',
      'Root cause reproduced: legacy lexical retrieval tokenizes underscored topic IDs as one token and broad field/title tokens dominate ties; policy reranking can therefore receive a common pool with weak topic separation.', '',
      'Fix: v0.3 expands frozen topic descriptions/keywords, records weighted topic/field/memory components, gates on raw relevance before policy reranking, preserves zero scores, and applies exact/near-duplicate limits with stable ties.', '',
      f"Changed-topic selected-set Jaccard after repair: {changed_overlap['jaccard']}.",
      'A result may still remain unchanged when topics share vocabulary, the qualifying pool is small, or stable high-relevance evidence is genuinely common; change is not forced.', '',
      'This diagnostic validates ranker behavior on synthetic evidence, not scientific relevance or real-world retrieval quality.']
    (output/'retrieval_before_after.md').write_text('\n'.join(report)+'\n', encoding='utf-8')
    dump(output/'status.json', {'schema_version': '0.3', 'status': 'completed', 'probes': len(probes),
         'comparisons': len(rows), 'http_attempts': 0})
    return output


# 验证v0.3运行的状态、分支、重放、检索和分析输出。
def validate_v03(run_dir):
    run_dir = Path(run_dir)
    config = json.loads((run_dir/'config.json').read_text(encoding='utf-8'))
    expected = len(config['seeds'])*len(config['policies'])*len(config['networks'])
    summary = list(csv.DictReader((run_dir/'summary.csv').open(encoding='utf-8-sig')))
    trajectories = list(csv.DictReader((run_dir/'trajectories.csv').open(encoding='utf-8-sig')))
    replay = json.loads((run_dir/'replay_validation.json').read_text(encoding='utf-8'))
    usage = json.loads((run_dir/'usage.json').read_text(encoding='utf-8'))
    retrieval = (run_dir/'live_retrieval_audit.jsonl').read_text(encoding='utf-8').splitlines()
    checks = {'status_completed': json.loads((run_dir/'status.json').read_text(encoding='utf-8'))['status'] == 'completed',
      'summary_rows': len(summary) == expected, 'trajectory_rows': len(trajectories) == expected*((config['ticks']-config['branch_tick'])//6),
      'replay_all_passed': replay['all_passed'] and replay['branch_count'] == expected,
      'http_attempts_zero': usage['http_attempts'] == 0, 'retrieval_audit_nonempty': bool(retrieval),
      'project_flow_rows': len(list(csv.DictReader((run_dir/'analysis'/'project_flow.csv').open(encoding='utf-8-sig')))) == expected,
      'duplicate_rows': len(list(csv.DictReader((run_dir/'analysis'/'duplicate_outputs.csv').open(encoding='utf-8-sig')))) == expected}
    return {'schema_version': '0.3', 'run_dir': str(run_dir.resolve()), 'checks': checks,
            'all_passed': all(checks.values()), 'summary_rows': len(summary), 'trajectory_rows': len(trajectories),
            'retrieval_records': len(retrieval), 'external_review': 'not_assessed_by_run_validation'}
