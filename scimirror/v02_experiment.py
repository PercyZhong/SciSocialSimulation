"""Versioned v0.2 experiment orchestration, exports and replay validation."""
import copy
import csv
import datetime
import json
import platform
import sys
from pathlib import Path

from .accounting import calculate_credits
from .backend import Backend
from .candidate_pool import build_schedule, validate_schedule
from .common import canonical, digest, dump
from .corpus import Corpus
from .events import V02Journal, replay_v02
from .policy import WEIGHTS
from .topics import TopicModel
from .v02_engine import event, step_v02
from .v02_metrics import measure_v02, paired_effects_v02, validate_accounting
from .v02_state import initialize_v02


ROOT = Path(__file__).resolve().parents[1]
ABLATIONS = ['full', 'no_topic', 'no_retrieval', 'no_invitation', 'no_exit_policy', 'all_policy_paths_off']


# 校验v0.2配置版本、机制参数、实验结构和预算字段。
def validate_v02_config(cfg):
    if cfg.get('schema_version') != '0.2':
        raise ValueError('v0.2 runner requires schema_version="0.2"; v0.1 configs are not adapted silently')
    required_paths = {'topic', 'retrieval', 'idea_selection', 'invitation', 'exit'}
    if set(cfg.get('policy_paths', {})) != required_paths:
        raise ValueError('policy_paths must define all five v0.2 paths')
    if cfg['agents'] != 20 or cfg['ticks'] % 6 or cfg['branch_tick'] % 6 or not 0 < cfg['branch_tick'] < cfg['ticks']:
        raise ValueError('v0.2 standard run requires 20 agents and cycle-boundary ticks')
    if set(cfg['policies']) != set(WEIGHTS) or set(cfg['networks']) != {'closed', 'open'}:
        raise ValueError('v0.2 requires three policies and closed/open networks')
    if cfg['pressure_mode'] != 'frozen' or cfg['dynamic_values']:
        raise ValueError('v0.2 requires frozen pressure and dynamic_values=false')
    if cfg['recognition']['method'] != 'community_attention' or not cfg['recognition']['freeze_at_fork']:
        raise ValueError('v0.2 requires frozen community_attention recognition')
    if cfg['retrieval_top_k'] > cfg['retrieval_candidate_pool'] or cfg['topic_candidates'] < 2:
        raise ValueError('Invalid retrieval/topic candidate sizes')
    if cfg['project_exit']['max_per_agent_per_cycle'] != 1:
        raise ValueError('Exactly one exit decision per agent per cycle is supported')


# 加载并严格验证一个显式v0.2 JSON配置。
def load_v02_config(path):
    cfg = json.loads(Path(path).read_text(encoding='utf-8'))
    validate_v02_config(cfg)
    return cfg


# 返回指定消融下仅在分叉后生效的policy通路配置。
def ablated_config(cfg, ablation):
    if ablation not in ABLATIONS:
        raise ValueError(f'Unknown ablation: {ablation}')
    result = copy.deepcopy(cfg)
    if ablation == 'no_topic':
        result['policy_paths']['topic'] = False
    elif ablation == 'no_retrieval':
        result['policy_paths']['retrieval'] = False
    elif ablation == 'no_invitation':
        result['policy_paths']['invitation'] = False
    elif ablation == 'no_exit_policy':
        result['policy_paths']['exit'] = False
    elif ablation == 'all_policy_paths_off':
        result['policy_paths'] = {key: False for key in result['policy_paths']}
    return result


# 估算v0.2配置在无缓存和无重试时的逻辑模型调用数。
def estimate_v02(cfg):
    calls_per_cycle = cfg['agents'] + (cfg['agents']+1)//2
    cycles = cfg['branch_tick']//6 + len(cfg['policies'])*len(cfg['networks'])*(cfg['ticks']-cfg['branch_tick'])//6
    count = len(cfg['seeds'])*calls_per_cycle*cycles
    return {'schema_version': '0.2', 'logical_calls_without_cache_or_retries': count,
            'max_completion_tokens_without_retries': count*cfg['max_tokens'],
            'request_attempt_cap': cfg['max_calls'], 'backend': cfg['backend']}


# 写入字段顺序稳定且Excel可读的CSV文件。
def write_csv(path, rows):
    if not rows:
        return
    with Path(path).open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


# 将实验者决策审计以每行一个JSON对象写出。
def write_jsonl(path, rows):
    Path(path).write_text(''.join(canonical(row)+'\n' for row in rows), encoding='utf-8')


# 导出不含条件标签、奖励和主观分数的匿名最终成果盲评表。
def export_blind_review(output, branch_worlds, start_cycle):
    cards, mapping = [], []
    for branch_key, world in sorted(branch_worlds.items()):
        for project in world.projects.values():
            if project.status != 'completed' or project.created_tick//6 < start_cycle:
                continue
            idea = world.ideas[project.origin_draft_ids[0]]
            card = idea['versions'][-1]
            review_id = digest([world.seed, project.id, card['title'], card['hypothesis']])[:16]
            cards.append({'review_id': review_id, 'title': card['title'], 'hypothesis': card['hypothesis'],
                          'method': card['method'], 'references': ';'.join(card['references']),
                          'novelty_1_to_5': '', 'feasibility_1_to_5': '', 'comment': ''})
            mapping.append({'review_id': review_id, 'branch': branch_key, 'project_id': project.id})
    write_csv(output/'blind_review.csv', sorted(cards, key=lambda row: row['review_id']))
    write_csv(output/'review_key_private.csv', mapping)


# 从冻结候选日程导出数量、学科配额和公开属性平衡审计。
def candidate_pool_rows(world):
    rows = []
    for key, item in sorted(world.candidate_schedule.items()):
        cycle, leader = key.split(':', 1)
        for network in ['closed', 'open']:
            candidates = item[network]
            attrs = [world.agents[x] for x in candidates]
            rows.append({'seed': world.seed, 'cycle': int(cycle), 'leader': leader, 'network': network,
                         'offered_count': len(candidates), 'candidate_ids': ';'.join(candidates),
                         'same_field_count': sum(x.field == world.agents[leader].field for x in attrs),
                         'other_field_count': sum(x.field != world.agents[leader].field for x in attrs),
                         'mean_baseline_reputation': sum(x.simulated_reputation for x in attrs)/len(attrs),
                         'mean_public_topic_count': sum(len(x.public_topics) for x in attrs)/len(attrs),
                         'schedule_source_hash': item['source_hash']})
    return rows


# 导出项目、投入和Agent分数信用账本行。
def ledger_rows(branch, world, start_cycle):
    projects = []
    for project in sorted(world.projects.values(), key=lambda x: x.id):
        projects.append({'branch': branch, **{key: (';'.join(value) if isinstance(value, list) else value)
                        for key, value in project.__dict__.items() if key != 'effort_ledger'},
                        'effort_entry_ids': ';'.join(project.effort_ledger)})
    effort = [{'branch': branch, **row} for row in world.effort_ledger]
    credits = [{'branch': branch, **row} for row in calculate_credits(world, start_cycle)]
    return projects, effort, credits


# 删除纯条件标签以比较全政策通路关闭后的实质世界状态。
def policy_neutral_state(world):
    value = world.export()
    value['policy'] = 'disabled'
    for audit in value['decision_audit']:
        audit.get('policy_context', {}).update(effective_policy='balanced')
    return value


# 验证全政策通路关闭时同一seed和network的三个政策分支完全一致。
def validate_no_policy_leak(branch_worlds):
    groups = {}
    for branch, world in branch_worlds.items():
        key = (world.seed, world.network)
        state_hash = digest(policy_neutral_state(world))
        groups.setdefault(key, set()).add(state_hash)
    failures = [{'seed': seed, 'network': network, 'state_hashes': sorted(values)}
                for (seed, network), values in sorted(groups.items()) if len(values) != 1]
    if failures:
        raise ValueError(f'Policy leak detected with all paths off: {failures}')
    return {'all_passed': True, 'groups_checked': len(groups)}


# 执行共同前缀和六分支v0.2实验并生成全部审计与重放归档。
def run_v02(cfg, output, ablation='full'):
    validate_v02_config(cfg)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    corpus = Corpus(ROOT/cfg['corpus'], cfg['cutoff_year'], cfg['allow_synthetic'], cfg.get('retrieval'))
    topic_model = TopicModel(ROOT/cfg['topics'], corpus.papers, cfg['recognition']['alpha'])
    backend = Backend(cfg, ROOT)
    branch_cfg = ablated_config(cfg, ablation)
    dump(output/'config.json', branch_cfg | {'ablation': ablation})
    dump(output/'topic_audit.json', topic_model.audit)
    rows, traces, replay_rows = [], [], []
    pool_audit, decisions, projects, effort, credits = [], [], [], [], []
    branch_worlds = {}
    schedule_hashes = {}
    try:
        for seed in cfg['seeds']:
            world = initialize_v02(cfg['agents'], seed, topic_model)
            world.intervention_start_cycle = cfg['branch_tick']//6
            world.policy_paths = dict(cfg['policy_paths'])
            world.candidate_schedule = build_schedule(world, cfg['ticks']//6, cfg)
            validate_schedule(world, cfg)
            pool_audit.extend(candidate_pool_rows(world))
            schedule_hashes[str(seed)] = digest(world.candidate_schedule)
            prefix_dir = output/f'seed_{seed}'/'prefix'
            journal = V02Journal(prefix_dir/'events.jsonl', f'{seed}/prefix')
            journal.commit(world, [event('system.initialized.v02', 'system', {'seed': seed})])
            while world.tick < cfg['branch_tick']:
                world, pending = step_v02(world, corpus, topic_model, backend, cfg)
                journal.commit(world, pending)
            initial = world.export()
            dump(prefix_dir/'fork.json', {'schema_version': '0.2', 'state_hash': digest(initial), 'state': initial,
                                          'candidate_schedule_hash': schedule_hashes[str(seed)]})
            for network in cfg['networks']:
                for policy in cfg['policies']:
                    branch = f'{policy}_{network}'
                    branch_key = f'seed_{seed}/{branch}'
                    current = copy.deepcopy(world)
                    current.policy, current.network = policy, network
                    current.policy_paths = dict(branch_cfg['policy_paths'])
                    folder = output/f'seed_{seed}'/branch
                    branch_journal = V02Journal(folder/'events.jsonl', f'{seed}/{branch}')
                    branch_journal.commit(current, [event('policy.intervention.started.v02', 'system',
                        {'effective_policy': policy, 'network': network, 'ablation': ablation,
                         'policy_paths': branch_cfg['policy_paths'], 'fork_state_hash': digest(initial)})])
                    while current.tick < cfg['ticks']:
                        current, pending = step_v02(current, corpus, topic_model, backend, branch_cfg)
                        branch_journal.commit(current, pending)
                        if current.tick % 6 == 0:
                            metrics = measure_v02(current, cfg['branch_tick']//6)
                            traces.append({'seed': seed, 'policy': policy, 'network': network,
                                           'ablation': ablation, 'tick': current.tick, **metrics})
                    if any(project.status == 'active' for project in current.projects.values()):
                        raise ValueError('Standard cycle-boundary run unexpectedly ended with an active project')
                    metrics, branch_credits = validate_accounting(current, cfg['branch_tick']//6)
                    restored = replay_v02(folder/'events.jsonl')
                    matched = digest(restored.export()) == digest(current.export())
                    replay_row = {'seed': seed, 'branch': branch, 'events': str(folder/'events.jsonl'),
                                  'tick': restored.tick, 'agents': len(restored.agents), 'state_hash': digest(restored.export()),
                                  'final_state_hash': digest(current.export()), 'fork_state_hash': digest(initial),
                                  'replay_match': matched}
                    if not matched:
                        raise ValueError('v0.2 replay mismatch')
                    replay_rows.append(replay_row)
                    dump(folder/'final_state.json', current.export())
                    dump(folder/'replay_validation.json', replay_row)
                    rows.append({'seed': seed, 'policy': policy, 'network': network, 'ablation': ablation, **metrics})
                    decisions.extend({'branch': branch_key, **record} for record in current.decision_audit
                                     if record.get('tick', -1) >= cfg['branch_tick'])
                    p, e, _ = ledger_rows(branch_key, current, cfg['branch_tick']//6)
                    projects.extend(p); effort.extend(e)
                    credits.extend({'branch': branch_key, **row} for row in branch_credits)
                    branch_worlds[branch_key] = current
                    print(f'Completed v0.2 seed={seed} branch={branch} ablation={ablation} tick={current.tick}', flush=True)
        leak_validation = (validate_no_policy_leak(branch_worlds) if ablation == 'all_policy_paths_off'
                           else {'all_passed': True, 'groups_checked': 0, 'not_applicable': True})
        dump(output/'policy_leak_validation.json', leak_validation)
        write_csv(output/'summary.csv', rows)
        write_csv(output/'trajectories.csv', traces)
        write_csv(output/'paired_effects.csv', paired_effects_v02(rows))
        write_csv(output/'candidate_pool_audit.csv', pool_audit)
        write_jsonl(output/'decision_audit.jsonl', decisions)
        write_csv(output/'project_ledger.csv', projects)
        write_csv(output/'effort_ledger.csv', effort)
        write_csv(output/'agent_credits.csv', credits)
        export_blind_review(output, branch_worlds, cfg['branch_tick']//6)
        dump(output/'replay_validation.json', {'schema_version': '0.2', 'branches': replay_rows,
             'all_passed': all(x['replay_match'] and x['agents'] == cfg['agents'] and x['tick'] == cfg['ticks'] for x in replay_rows),
             'branch_count': len(replay_rows)})
        manifest = {'schema_version': '0.2', 'python': sys.version, 'platform': platform.platform(),
                    'config_hash': digest(branch_cfg), 'source_hash': digest({str(p.relative_to(ROOT)): p.read_text(encoding='utf-8')
                    for p in sorted((ROOT/'scimirror').glob('*.py'))}), 'corpus_hash': digest(corpus.papers),
                    'topic_snapshot_hash': topic_model.audit['snapshot_hash'], 'candidate_schedule_hashes': schedule_hashes,
                    'backend': cfg['backend'], 'model': backend.model, 'synthetic_corpus':
                    any(p['synthetic'] for p in corpus.papers.values()), 'estimate': estimate_v02(cfg),
                    'scientific_status': 'prototype v0.2; simulation-internal proxies; no real-world causal claim'}
        dump(output/'manifest.json', manifest)
        (output/'SCHEMA.md').write_text(
            '# SciMirror v0.2 output schema\n\nCounts in summary/trajectories are cumulative from the intervention boundary.\n'
            '`candidate_ideas_generated` counts validated initial alternatives; `drafts_created` counts selected drafts; '
            '`projects_started` includes later merged/abandoned projects; `final_outputs` counts unique completed projects.\n'
            '`outputs_per_100_effort` is the main resource-normalized measure. `team_size_discounted_outputs` is a structural '
            'headcount discount and must not be used as the sole quality criterion. Fractional credit conserves to one per output.\n',
            encoding='utf-8')
        report = ['# SciMirror v0.2 mock run', '', f'Backend: {cfg["backend"]}; ablation: {ablation}; agents: {cfg["agents"]}; seeds: {len(cfg["seeds"])}.', '',
                  'This is synthetic software/mechanism validation, not evidence about real science or policy.', '',
                  '|seed|policy|network|drafts|started|merged|abandoned|completed|final outputs|team outputs|effort|outputs/100 effort|novelty|attention|',
                  '|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
        for row in rows:
            report.append(f'|{row["seed"]}|{row["policy"]}|{row["network"]}|{row["drafts_created"]}|{row["projects_started"]}|'
                          f'{row["projects_merged"]}|{row["projects_abandoned"]}|{row["projects_completed"]}|{row["final_outputs"]}|'
                          f'{row["team_outputs"]}|{row["total_effort_units"]}|{row["outputs_per_100_effort"]:.4f}|'
                          f'{row["lexical_novelty_proxy"]:.4f}|{row["community_attention_proxy"]:.4f}|')
        (output/'REPORT.md').write_text('\n'.join(report)+'\n', encoding='utf-8')
        dump(output/'status.json', {'status': 'completed', 'schema_version': '0.2', 'ablation': ablation})
    except Exception as exc:
        dump(output/'status.json', {'status': 'failed', 'schema_version': '0.2', 'error_type': type(exc).__name__,
                                    'guidance': 'Inspect terminal; no mock substitution and no log repair performed.'})
        raise
    finally:
        dump(output/'usage.json', {'http_attempts': backend.calls, 'cache_hits': backend.hits,
             'prompt_tokens_reported': backend.prompt_tokens, 'completion_tokens_reported': backend.completion_tokens,
             'note': 'Mock has zero HTTP attempts; provider totals are used only when reported.'})
    return output
