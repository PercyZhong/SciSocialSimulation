#!/usr/bin/env python3
"""Validate archived SciMirror v0.2 mock deliveries without model calls."""
import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from scimirror.common import digest, dump
from scimirror.events import replay_v02


# 读取UTF-8 JSON归档文件。
def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


# 读取带BOM或普通UTF-8的CSV归档表。
def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


# 验证一次完整运行的状态、行数、终态、共同分叉、用量和日志重放。
def validate_run(path, seeds, ticks, expected_summary, expected_trajectories, replay_logs):
    path = Path(path).resolve()
    checks = {}
    checks['status_completed'] = read_json(path/'status.json').get('status') == 'completed'
    summary = read_csv(path/'summary.csv')
    trajectories = read_csv(path/'trajectories.csv')
    checks['summary_rows'] = len(summary) == expected_summary
    checks['trajectory_rows'] = len(trajectories) == expected_trajectories
    checks['backend_mock'] = read_json(path/'manifest.json').get('backend') == 'mock'
    usage = read_json(path/'usage.json')
    checks['zero_http_attempts'] = usage.get('http_attempts') == 0
    replay_archive = read_json(path/'replay_validation.json')
    checks['archived_replay_all_passed'] = replay_archive.get('all_passed') is True
    checks['archived_replay_branch_count'] = replay_archive.get('branch_count') == expected_summary
    fork_groups = defaultdict(set)
    event_counts = Counter()
    final_count = 0
    direct_replay_count = 0
    for seed in seeds:
        branches = replay_archive['branches']
        for row in (x for x in branches if x['seed'] == seed):
            fork_groups[seed].add(row['fork_state_hash'])
            folder = path/f'seed_{seed}'/row['branch']
            final = read_json(folder/'final_state.json')
            final_count += 1
            if len(final['agents']) != 20 or final['tick'] != ticks:
                checks['final_states_20_agents_at_tick'] = False
            for line in (folder/'events.jsonl').read_text(encoding='utf-8').splitlines():
                event_counts[json.loads(line)['type']] += 1
            if replay_logs:
                restored = replay_v02(folder/'events.jsonl')
                direct_replay_count += 1
                if digest(restored.export()) != digest(final):
                    checks['direct_replay_matches'] = False
    checks.setdefault('final_states_20_agents_at_tick', final_count == expected_summary)
    checks.setdefault('direct_replay_matches', direct_replay_count == expected_summary if replay_logs else True)
    checks['shared_fork_hash_per_seed'] = all(len(fork_groups[seed]) == 1 for seed in seeds)
    checks['candidate_pool_rows'] = len(read_csv(path/'candidate_pool_audit.csv')) == len(seeds)*(ticks//6)*20
    checks['topic_coverage_complete'] = read_json(path/'topic_audit.json').get('coverage') == 1.0
    checks['flow_events_observed'] = event_counts['project.merged'] > 0 and event_counts['project.members.exited'] > 0
    return {'path': str(path), 'checks': checks, 'all_passed': all(checks.values()),
            'summary_rows_actual': len(summary), 'trajectory_rows_actual': len(trajectories),
            'final_states_checked': final_count, 'direct_replays_checked': direct_replay_count,
            'event_counts': dict(event_counts), 'http_attempts': usage.get('http_attempts')}


# 验证六个规定消融均完整且全通路关闭的政策泄漏检查通过。
def validate_ablations(path):
    path = Path(path).resolve()
    names = ['full', 'no_topic', 'no_retrieval', 'no_invitation', 'no_exit_policy', 'all_policy_paths_off']
    rows = {}
    for name in names:
        folder = path/name
        replay = read_json(folder/'replay_validation.json')
        status = read_json(folder/'status.json')
        usage = read_json(folder/'usage.json')
        rows[name] = {'status_completed': status.get('status') == 'completed',
                      'summary_rows': len(read_csv(folder/'summary.csv')),
                      'trajectory_rows': len(read_csv(folder/'trajectories.csv')),
                      'replay_all_passed': replay.get('all_passed') is True,
                      'replay_branch_count': replay.get('branch_count'),
                      'http_attempts': usage.get('http_attempts')}
    leak = read_json(path/'all_policy_paths_off'/'policy_leak_validation.json')
    passed = all(x['status_completed'] and x['summary_rows'] == 18 and x['trajectory_rows'] == 72
                 and x['replay_all_passed'] and x['replay_branch_count'] == 18 and x['http_attempts'] == 0
                 for x in rows.values()) and leak.get('all_passed') is True
    return {'path': str(path), 'runs': rows, 'policy_leak_validation': leak, 'all_passed': passed}


# 解析归档路径并输出机器可读的总体验收文件。
def main():
    parser = argparse.ArgumentParser(description='Validate SciMirror v0.2 archived mock runs')
    parser.add_argument('--smoke', required=True)
    parser.add_argument('--main-run', required=True)
    parser.add_argument('--ablations', required=True)
    parser.add_argument('--diagnostic', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    report = {'schema_version': '0.2', 'validation_kind': 'offline_mock_delivery',
              'smoke': validate_run(args.smoke, [42, 43, 44], 12, 18, 18, True),
              'main': validate_run(args.main_run, [42, 43, 44], 30, 18, 72, True),
              'ablations': validate_ablations(args.ablations),
              'diagnostic': validate_run(args.diagnostic, list(range(100, 110)), 30, 60, 240, False),
              'live_llm_validated': False,
              'scientific_scope': 'Synthetic mock engineering validation; no real-world causal claim.'}
    report['all_passed'] = all(report[key]['all_passed'] for key in ('smoke', 'main', 'ablations', 'diagnostic'))
    dump(Path(args.output), report)
    print(json.dumps({'all_passed': report['all_passed'], 'output': str(Path(args.output).resolve())}, ensure_ascii=False))
    if not report['all_passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
