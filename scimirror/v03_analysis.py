"""Project-flow, output-duplicate and sampling-aware quality analysis for v0.3."""
import json
from collections import Counter, defaultdict
from pathlib import Path

from .common import digest, dump
from .corpus import similarity
from .v03_retrieval import normalize_text
from .v03_review import DIMENSIONS, agreement_rows, quality_scores, read_csv, write_csv


DUPLICATE_VERSION = 'normalized_exact_plus_connected_jaccard_v03_1'


# 将CSV或JSON来源的世界条件规范化为一致索引键。
def condition_key(seed, policy, network):
    return int(seed), str(policy), str(network)


# 计算完整或缺失分层样本的Horvitz-Thompson质量估计与[0,1]边界。
def estimate_stratum_quality(n_total, sampled_values):
    sample_size = len(sampled_values)
    valid = [value for value in sampled_values if value is not None]
    if n_total == 0:
        return {'observed_sum':0.0,'estimated_total':0.0,'lower_bound':0.0,'upper_bound':0.0,'complete':True}
    if sample_size == 0:
        return {'observed_sum':0.0,'estimated_total':None,'lower_bound':0.0,'upper_bound':float(n_total),'complete':False}
    weight = n_total/sample_size
    lower = weight*sum(valid); upper = lower+weight*(sample_size-len(valid))
    return {'observed_sum':sum(valid),'estimated_total':lower if len(valid) == sample_size else None,
            'lower_bound':lower,'upper_bound':upper,'complete':len(valid) == sample_size}


# 从运行归档读取每个分支的终态及条件信息。
def branch_states(run_dir):
    values = []
    for seed_dir in sorted(Path(run_dir).glob('seed_*')):
        seed = int(seed_dir.name.split('_')[1])
        for branch_dir in sorted(path for path in seed_dir.iterdir() if path.is_dir() and path.name != 'prefix'):
            policy, network = branch_dir.name.rsplit('_', 1)
            state = json.loads((branch_dir/'final_state.json').read_text(encoding='utf-8'))
            values.append((seed, policy, network, branch_dir, state))
    return values


# 使用连通分量聚类检测分支内完全重复与冻结阈值近重复成果。
def duplicate_analysis(run_dir, threshold):
    rows = []
    config = json.loads((Path(run_dir)/'config.json').read_text(encoding='utf-8'))
    start_cycle = config['branch_tick']//6
    for seed, policy, network, _, state in branch_states(run_dir):
        cards = []
        for project in state['projects'].values():
            if project['status'] != 'completed' or project['created_tick']//6 < start_cycle:
                continue
            idea = state['ideas'][project['origin_draft_ids'][0]]
            card = idea['versions'][-1]
            text = ' '.join(card[key] for key in ('title','hypothesis','method'))
            cards.append((project['id'], text, digest(normalize_text(text))))
        parent = list(range(len(cards)))
        def find(index):
            # 查找并压缩一个重复簇节点的父节点。
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index
        def union(left, right):
            # 合并两个由相似性边连接的重复簇。
            a, b = find(left), find(right)
            if a != b:
                parent[max(a,b)] = min(a,b)
        exact_pairs = near_pairs = pair_count = 0
        boundary = []
        for i, first in enumerate(cards):
            for j, second in enumerate(cards[i+1:], i+1):
                pair_count += 1
                score = similarity(first[1], second[1])
                exact = first[2] == second[2]
                if exact:
                    exact_pairs += 1
                if exact or score >= threshold:
                    near_pairs += 1; union(i, j)
                    if len(boundary) < 3:
                        boundary.append({'left_project_id': first[0], 'right_project_id': second[0], 'similarity': score})
        clusters = Counter(find(i) for i in range(len(cards)))
        rows.append({'seed': seed, 'policy': policy, 'network': network, 'outputs': len(cards),
            'exact_duplicate_pair_rate': exact_pairs/pair_count if pair_count else None,
            'near_duplicate_pair_rate': near_pairs/pair_count if pair_count else None,
            'unique_clusters': len(clusters), 'cluster_size_distribution': ';'.join(map(str, sorted(clusters.values(), reverse=True))),
            'redundant_output_rate': (len(cards)-len(clusters))/len(cards) if cards else None,
            'threshold': threshold, 'algorithm_version': DUPLICATE_VERSION,
            'boundary_examples': json.dumps(boundary, separators=(',', ':'))})
    return rows


# 按分支复算项目守恒、退出事件和失败投入分解。
def project_flow_analysis(run_dir):
    config = json.loads((Path(run_dir)/'config.json').read_text(encoding='utf-8'))
    start_cycle = config['branch_tick']//6
    rows = []
    for seed, policy, network, branch_dir, state in branch_states(run_dir):
        projects = [p for p in state['projects'].values() if p['created_tick']//6 >= start_cycle]
        counts = Counter(p['status'] for p in projects)
        exit_events = []
        for line in (branch_dir/'events.jsonl').read_text(encoding='utf-8').splitlines():
            event = json.loads(line)
            if event['type'] == 'project.members.exited' and event['tick']//6 >= start_cycle:
                exit_events.append(event['payload'])
        departed = sum(len(event['exiting']) for event in exit_events)
        projects_with_exit = len({event['project_id'] for event in exit_events})
        full_exit_abandoned = len({event['project_id'] for event in exit_events if event['status'] == 'abandoned'})
        failed_ids = {p['id'] for p in projects if p['status'] in ('abandoned','censored')}
        failed_effort = sum(entry['units'] for entry in state['effort_ledger']
                            if entry['cycle'] >= start_cycle and entry['project_id'] in failed_ids)
        started = len(projects); merged = counts['merged']; abandoned = counts['abandoned']
        completed = counts['completed']; censored = counts['censored'] + counts['active']
        if started != merged+abandoned+completed+censored:
            raise ValueError('v0.3 project flow does not conserve')
        rows.append({'seed': seed, 'policy': policy, 'network': network, 'projects_started': started,
            'projects_merged': merged, 'merge_rate': merged/started if started else None,
            'member_departures': departed, 'membership_exit_events': len(exit_events),
            'projects_with_member_exit': projects_with_exit, 'projects_abandoned': abandoned,
            'all_member_exit_abandonments': full_exit_abandoned,
            'other_abandonments': abandoned-full_exit_abandoned, 'projects_completed': completed,
            'projects_censored': censored, 'failed_effort_units': failed_effort,
            'flow_conservation_residual': started-merged-abandoned-completed-censored})
    return rows


# 以balanced为对照导出完成数变化的项目流量会计恒等分解。
def flow_decomposition(flow_rows):
    output = []
    for seed in sorted({row['seed'] for row in flow_rows}):
        for network in ('closed','open'):
            index = {row['policy']: row for row in flow_rows if row['seed'] == seed and row['network'] == network}
            if 'balanced' not in index:
                continue
            base = index['balanced']
            for policy in ('novelty','recognition'):
                if policy not in index:
                    continue
                row = index[policy]
                deltas = {key: row[key]-base[key] for key in ('projects_started','projects_merged','projects_abandoned','projects_censored','projects_completed')}
                identity = deltas['projects_started']-deltas['projects_merged']-deltas['projects_abandoned']-deltas['projects_censored']
                output.append({'seed':seed,'network':network,'treatment':policy,'control':'balanced',
                    **{'delta_'+key:value for key,value in deltas.items()}, 'identity_completed':identity,
                    'identity_residual':deltas['projects_completed']-identity,
                    'interpretation':'accounting_identity_not_causal_mediation'})
    return output


# 导出评审覆盖、一致性、分歧及抽样加权质量表。
def analyze_reviews(run_dir, review_dir, output_dir):
    review_dir, output_dir = Path(review_dir), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    sample = read_csv(review_dir/'review_private'/'sample_manifest.csv')
    keys = {row['review_id']: row for row in read_csv(review_dir/'review_private'/'review_key.csv')}
    validated_path = review_dir/'review_results'/'validated_reviews.csv'
    reviews = read_csv(validated_path) if validated_path.exists() else []
    for row in reviews:
        for dimension in DIMENSIONS:
            raw = row.get(dimension+'_score')
            row[dimension+'_score'] = int(raw) if raw not in (None, '') else None
    requested = Counter((key['seed'],key['policy'],key['network'],key['output_type']) for key in keys.values())
    valid_by_id = defaultdict(list)
    for row in reviews:
        valid_by_id[row['review_id']].append(row)
    coverage = []
    for stratum, count in sorted(requested.items()):
        ids = [rid for rid,key in keys.items() if (key['seed'],key['policy'],key['network'],key['output_type']) == stratum]
        valid = sum(bool(valid_by_id[rid]) for rid in ids)
        double = sum(len(valid_by_id[rid]) >= 2 for rid in ids)
        unassessable = sum(any(row.get('evidence_access_status') == 'unassessable' for row in valid_by_id[rid]) for rid in ids)
        coverage.append({'seed': stratum[0], 'policy': stratum[1], 'network': stratum[2], 'output_type': stratum[3],
            'sampled_ideas': count, 'ideas_with_any_rating': valid, 'double_rated_ideas': double,
            'missing_rate': 1-double/count if count else None, 'unassessable_ideas': unassessable})
    agreements = agreement_rows(reviews)
    disagreements = []
    for review_id, values in valid_by_id.items():
        if len(values) < 2:
            continue
        for dimension in DIMENSIONS:
            scores = [row[dimension+'_score'] for row in values[:2]]
            if None not in scores and abs(scores[0]-scores[1]) >= 2:
                disagreements.append({'review_id': review_id, 'dimension': dimension,
                                      'reviewer_1_score': scores[0], 'reviewer_2_score': scores[1],
                                      'status': 'awaiting_third_party_adjudication'})
    quality = quality_scores(reviews)
    sample_map = {row['review_id']: row for row in sample}
    stratum_rows = []
    grouped = defaultdict(list)
    for review_id, key in keys.items():
        grouped[(key['seed'],key['policy'],key['network'],key['output_type'])].append(review_id)
    for stratum, ids in sorted(grouped.items()):
        qs = [quality.get(rid, {}).get('Q') for rid in ids]
        valid_q = [value for value in qs if value is not None]
        n_h = int(sample_map[ids[0]]['n_h']); n_total = int(sample_map[ids[0]]['N_h'])
        estimate = estimate_stratum_quality(n_total, qs)
        stratum_rows.append({'seed': int(stratum[0]), 'policy': stratum[1], 'network': stratum[2],
            'output_type': stratum[3], 'N_h': n_total, 'n_h': n_h, 'valid_Q': len(valid_q),
            'observed_reviewed_quality_sum': estimate['observed_sum'],
            'estimated_population_quality_total': estimate['estimated_total'],
            'quality_status': 'complete' if estimate['complete'] else 'incomplete_external_reviews',
            'missing_Q_lower_bound': estimate['lower_bound'], 'missing_Q_upper_bound': estimate['upper_bound']})
    review_results = review_dir/'review_results'
    write_csv(review_results/'coverage.csv', coverage, ['seed','policy','network','output_type','sampled_ideas','ideas_with_any_rating','double_rated_ideas','missing_rate','unassessable_ideas'])
    write_csv(review_results/'agreement.csv', agreements, ['dimension','common_items','exact_agreement_rate','within_one_rate','mean_absolute_disagreement','quadratic_weighted_kappa','kappa_null_reason'])
    write_csv(review_results/'disagreements.csv', disagreements, ['review_id','dimension','reviewer_1_score','reviewer_2_score','status'])
    summary = {condition_key(row['seed'],row['policy'],row['network']): row for row in read_csv(Path(run_dir)/'summary.csv')}
    condition_rows = []
    for condition in sorted({condition_key(row['seed'],row['policy'],row['network']) for row in stratum_rows}):
        values = [row for row in stratum_rows if condition_key(row['seed'],row['policy'],row['network']) == condition]
        complete = all(row['estimated_population_quality_total'] is not None for row in values)
        estimated = sum(row['estimated_population_quality_total'] for row in values) if complete else None
        population_count = sum(row['N_h'] for row in values)
        effort = float(summary[condition]['total_effort_units'])
        condition_rows.append({'seed':condition[0],'policy':condition[1],'network':condition[2],
          'population_completed_count':population_count,'sampled_ideas':sum(row['n_h'] for row in values),
          'valid_Q':sum(row['valid_Q'] for row in values),
          'observed_reviewed_quality_sum':sum(row['observed_reviewed_quality_sum'] for row in values),
          'estimated_population_quality_total':estimated,
          'estimated_mean_quality':estimated/population_count if estimated is not None and population_count else None,
          'estimated_quality_per_100_effort':100*estimated/effort if estimated is not None and effort else None,
          'missing_Q_lower_bound':sum(row['missing_Q_lower_bound'] for row in values),
          'missing_Q_upper_bound':sum(row['missing_Q_upper_bound'] for row in values),
          'quality_status':'complete' if complete else 'incomplete_external_reviews'})
    write_csv(output_dir/'quality_by_world_condition.csv', condition_rows)
    by_type = []
    for output_type in ('solo','team'):
        relevant = [row for row in stratum_rows if row['output_type'] == output_type]
        estimates = [row['estimated_population_quality_total'] for row in relevant if row['estimated_population_quality_total'] is not None]
        by_type.append({'output_type': output_type, 'strata': len(relevant), 'complete_strata': len(estimates),
                        'estimated_quality_total': sum(estimates) if len(estimates) == len(relevant) else None,
                        'status': 'complete' if len(estimates) == len(relevant) else 'awaiting_external_reviews'})
    write_csv(output_dir/'quality_by_output_type.csv', by_type)
    write_csv(output_dir/'quality_paired_effects.csv', [], ['comparison','metric','n_world_pairs','mean_difference','ci95_low','ci95_high','status'])
    write_csv(output_dir/'quality_weight_sensitivity.csv', [], ['weight_scheme','output_type','estimated_quality_total','status'])
    status = 'completed' if reviews and all(row['quality_status'] == 'complete' for row in condition_rows) else 'awaiting_external_reviews'
    dump(output_dir/'quality_status.json', {'schema_version': '0.3', 'status': status,
         'review_rows': len(reviews), 'sampled_ideas': len(keys), 'quality_formula_version': 'four_dimension_equal_weight_v03_1',
         'missing_policy': 'no_zero_imputation; incomplete estimates remain null'})
    return {'status': status, 'review_rows': len(reviews), 'sampled_ideas': len(keys)}


# 写出不依赖外部评分的项目流量和重复成果分析。
def analyze_run(run_dir, analysis_config):
    output = Path(run_dir)/'analysis'
    output.mkdir(exist_ok=True)
    flow = project_flow_analysis(run_dir)
    duplicates = duplicate_analysis(run_dir, analysis_config['duplicates']['similarity_threshold'])
    write_csv(output/'project_flow.csv', flow)
    write_csv(output/'project_flow_decomposition.csv', flow_decomposition(flow))
    write_csv(output/'duplicate_outputs.csv', duplicates)
    return {'project_flow_rows': len(flow), 'duplicate_rows': len(duplicates),
            'duplicate_algorithm_version': DUPLICATE_VERSION}
