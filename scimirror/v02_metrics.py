"""Flow, stock, effort and paired metrics for SciMirror v0.2."""
import math
import statistics
from collections import Counter, defaultdict

from .accounting import calculate_credits
from .common import rng
from .corpus import similarity


# 计算分类分布的自然对数熵。
def entropy(values):
    counts = Counter(values)
    total = sum(counts.values())
    return -sum((n/total)*math.log(n/total) for n in counts.values()) if total else 0.0


# 将投入沿项目合并链解析到最终项目。
def effort_by_final_project(world, start_cycle):
    result = defaultdict(lambda: defaultdict(float))
    for entry in world.effort_ledger:
        if entry['cycle'] < start_cycle:
            continue
        project_id = entry['project_id']
        seen = set()
        while world.projects[project_id].status == 'merged':
            if project_id in seen:
                raise ValueError('Project merge cycle')
            seen.add(project_id)
            project_id = world.projects[project_id].merged_into
        result[project_id][entry['agent_id']] += entry['units']
    return result


# 汇总v0.2干预期的草案、项目、成果、投入、合作、退出和主题指标。
def measure_v02(world, start_cycle):
    drafts = [x for x in world.ideas.values() if x['cycle'] >= start_cycle]
    projects = [x for x in world.projects.values() if x.created_tick//6 >= start_cycle]
    completed = [x for x in projects if x.status == 'completed']
    effort_map = effort_by_final_project(world, start_cycle)
    contributor_counts = {p.id: sum(v > 0 for v in effort_map[p.id].values()) for p in completed}
    final_cards = [world.ideas[p.origin_draft_ids[0]] for p in completed]
    texts = [' '.join(card['versions'][-1][k] for k in ('title', 'hypothesis', 'method')) for card in final_cards]
    pairs = [1-similarity(a, b) for i, a in enumerate(texts) for b in texts[i+1:]]
    total_effort = sum(x['units'] for x in world.effort_ledger if x['cycle'] >= start_cycle)
    invitations = [x for x in world.invitation_history if int(x['id'].split(':')[2]) >= start_cycle]
    all_audits = [x for x in world.decision_audit if x.get('tick', -1)//6 >= start_cycle]
    exits = [x for x in all_audits if x.get('stage') == 'exit']
    invitation_audits = [x for x in all_audits if x.get('stage') == 'invitation' and 'offered_count' in x]
    target_load = Counter(x['target'] for x in invitations)
    historical_cross = []
    current_cross = []
    for project in completed:
        positive = [aid for aid, units in effort_map[project.id].items() if units > 0]
        historical_cross.append(len({world.agents[aid].field for aid in positive}) > 1)
        current_cross.append(len({world.agents[aid].field for aid in project.completion_members}) > 1)
    return {
        'candidate_ideas_generated': sum(x['candidate_count'] for x in drafts),
        'drafts_created': len(drafts), 'projects_started': len(projects),
        'projects_merged': sum(x.status == 'merged' for x in projects),
        'projects_abandoned': sum(x.status == 'abandoned' for x in projects),
        'projects_completed': len(completed), 'projects_censored': sum(x.status in {'active','censored'} for x in projects),
        'final_outputs': len(completed), 'solo_outputs': sum(contributor_counts[p.id] == 1 for p in completed),
        'team_outputs': sum(contributor_counts[p.id] >= 2 for p in completed),
        'total_effort_units': total_effort,
        'outputs_per_100_effort': (100*len(completed)/total_effort if total_effort else None),
        'team_size_discounted_outputs': sum(1/contributor_counts[p.id] for p in completed if contributor_counts[p.id]),
        'mean_contributors_per_output': statistics.mean(contributor_counts.values()) if contributor_counts else 0.0,
        'mean_active_team_size_at_completion': statistics.mean(len(p.completion_members) for p in completed) if completed else 0.0,
        'lexical_novelty_proxy': statistics.mean(c['lexical_novelty_proxy'] for c in final_cards) if final_cards else 0.0,
        'community_attention_proxy': statistics.mean(c['community_attention_proxy'] for c in final_cards) if final_cards else 0.0,
        'text_diversity': statistics.mean(pairs) if pairs else 0.0,
        'selected_topic_entropy': entropy(x['topic_id'] for x in drafts),
        'reading_topic_entropy': entropy(topic for agent in world.agents.values() for topic in agent.read_topic_ids),
        'invitation_acceptance_rate': (sum(x['status'] == 'accepted' for x in invitations)/len(invitations) if invitations else 0.0),
        'invitation_conflict_rate': (sum(v > 1 for v in target_load.values())/len(target_load) if target_load else 0.0),
        'candidate_shortfall_rate': (statistics.mean(x['offered_count'] < 3 for x in invitation_audits)
                                     if invitation_audits else 0.0),
        'exit_rate': (statistics.mean(x['selected_action'] == 'exit' for x in exits) if exits else 0.0),
        'cross_field_output_rate': statistics.mean(historical_cross) if historical_cross else 0.0,
        'current_cross_field_rate': statistics.mean(current_cross) if current_cross else 0.0,
        'mean_simulated_reputation': statistics.mean(a.simulated_reputation for a in world.agents.values()),
    }


# 验证项目流量、成果拆分和分数信用的守恒关系。
def validate_accounting(world, start_cycle):
    metrics = measure_v02(world, start_cycle)
    if metrics['projects_started'] != (metrics['projects_merged'] + metrics['projects_abandoned'] +
                                       metrics['projects_completed'] + metrics['projects_censored']):
        raise ValueError('Project flow does not conserve')
    if metrics['solo_outputs'] + metrics['team_outputs'] != metrics['final_outputs']:
        raise ValueError('Output type split does not conserve')
    credits = calculate_credits(world, start_cycle)
    by_project = defaultdict(float)
    for row in credits:
        by_project[row['project_id']] += row['fractional_output_credit']
    if any(abs(value-1) > 1e-12 for value in by_project.values()) or abs(sum(by_project.values())-metrics['final_outputs']) > 1e-12:
        raise ValueError('Fractional credit does not conserve')
    return metrics, credits


# 计算政策、网络和政策乘网络交互的世界层配对bootstrap效应。
def paired_effects_v02(rows):
    identifiers = {'seed', 'policy', 'network', 'ablation'}
    metrics = [key for key, value in rows[0].items() if key not in identifiers and isinstance(value, (int, float)) and value is not None]
    comparisons = []
    for network in ['closed', 'open']:
        for treatment in ['novelty', 'recognition']:
            comparisons.append(('policy', network, treatment, 'balanced'))
    for policy in ['balanced', 'novelty', 'recognition']:
        comparisons.append(('network', policy, 'open', 'closed'))
    out = []
    for kind, stratum, treatment, control in comparisons:
        subset = [r for r in rows if r['network'] == stratum] if kind == 'policy' else [r for r in rows if r['policy'] == stratum]
        key = 'policy' if kind == 'policy' else 'network'
        controls = {r['seed']: r for r in subset if r[key] == control}
        for metric in metrics:
            differences = [r[metric]-controls[r['seed']][metric] for r in subset
                           if r[key] == treatment and r['seed'] in controls and r[metric] is not None and controls[r['seed']][metric] is not None]
            if differences:
                random_source = rng(202, kind, stratum, treatment, metric)
                boots = sorted(statistics.mean(random_source.choices(differences, k=len(differences))) for _ in range(2000))
                out.append({'comparison': kind, 'stratum': stratum, 'treatment': treatment, 'control': control,
                            'metric': metric, 'n_world_pairs': len(differences),
                            'mean_difference': statistics.mean(differences),
                            'ci95_low': boots[49], 'ci95_high': boots[1949],
                            'interpretation': 'simulation_internal_proxy_only'})
    for treatment in ['novelty', 'recognition']:
        for metric in metrics:
            values = []
            for seed in sorted({r['seed'] for r in rows}):
                index = {(r['policy'], r['network']): r for r in rows if r['seed'] == seed}
                keys = [(treatment, 'open'), ('balanced', 'open'), (treatment, 'closed'), ('balanced', 'closed')]
                if all(k in index and index[k][metric] is not None for k in keys):
                    values.append((index[keys[0]][metric]-index[keys[1]][metric])-
                                  (index[keys[2]][metric]-index[keys[3]][metric]))
            if values:
                random_source = rng(203, 'interaction', treatment, metric)
                boots = sorted(statistics.mean(random_source.choices(values, k=len(values))) for _ in range(2000))
                out.append({'comparison': 'policy_x_network', 'stratum': 'difference_in_differences',
                            'treatment': treatment, 'control': 'balanced', 'metric': metric,
                            'n_world_pairs': len(values), 'mean_difference': statistics.mean(values),
                            'ci95_low': boots[49], 'ci95_high': boots[1949],
                            'interpretation': 'simulation_internal_proxy_only'})
    return out
