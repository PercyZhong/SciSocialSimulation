"""Project-flow, output-duplicate and sampling-aware quality analysis for v0.3."""
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from .common import digest, dump, rng
from .corpus import similarity
from .v03_retrieval import normalize_text
from .v03_review import DIMENSIONS, agreement_rows, final_effort, quality_scores, read_csv, write_csv


DUPLICATE_VERSION = 'normalized_exact_plus_connected_jaccard_v03_1'
DEFAULT_QUALITY_WEIGHT_SCHEMES = {
    'equal': {'novelty': .25, 'feasibility': .25, 'scientific_value': .25, 'evidence_support': .25},
    'novelty_emphasis': {'novelty': .4, 'feasibility': .2, 'scientific_value': .2, 'evidence_support': .2},
    'feasibility_emphasis': {'novelty': .2, 'feasibility': .4, 'scientific_value': .2, 'evidence_support': .2},
    'scientific_value_emphasis': {'novelty': .2, 'feasibility': .2, 'scientific_value': .4, 'evidence_support': .2},
    'evidence_emphasis': {'novelty': .2, 'feasibility': .2, 'scientific_value': .2, 'evidence_support': .4},
}


# 将CSV或JSON来源的世界条件规范化为一致索引键。
def condition_key(seed, policy, network):
    return int(seed), str(policy), str(network)


# 计算有界指标在完整或缺失分层样本中的Horvitz-Thompson估计与界限。
def estimate_stratum_metric(n_total, sampled_values, lower_bound, upper_bound):
    sample_size = len(sampled_values)
    valid = [value for value in sampled_values if value is not None]
    if n_total == 0:
        return {'observed_sum':0.0,'estimated_total':0.0,'lower_bound':0.0,'upper_bound':0.0,'complete':True}
    if sample_size == 0:
        return {'observed_sum':0.0,'estimated_total':None,'lower_bound':n_total*lower_bound,
                'upper_bound':n_total*upper_bound,'complete':False}
    weight = n_total/sample_size
    lower = weight*(sum(valid)+(sample_size-len(valid))*lower_bound)
    upper = weight*(sum(valid)+(sample_size-len(valid))*upper_bound)
    estimate = weight*sum(valid) if len(valid) == sample_size else None
    return {'observed_sum':sum(valid),'estimated_total':estimate,
            'lower_bound':lower,'upper_bound':upper,'complete':len(valid) == sample_size}


# 计算完整或缺失分层样本的Horvitz-Thompson质量估计与[0,1]边界。
def estimate_stratum_quality(n_total, sampled_values):
    return estimate_stratum_metric(n_total, sampled_values, 0.0, 1.0)


# 对世界配对差值执行确定性的百分位bootstrap。
def paired_interval(differences, seed, repetitions, *keys):
    if len(differences) < 2:
        return None, None
    random_source = rng(seed, *keys)
    boots = sorted(statistics.mean(random_source.choices(differences, k=len(differences)))
                   for _ in range(repetitions))
    low = max(0, math.floor(.025*repetitions)-1)
    high = min(repetitions-1, math.ceil(.975*repetitions)-1)
    return boots[low], boots[high]


# 计算政策、网络及交互的世界层质量配对差异和区间。
def paired_quality_effects(condition_rows, expected_seeds, bootstrap_seed, repetitions):
    metrics = ('estimated_mean_quality', 'estimated_quality_per_100_effort')
    comparisons = [('policy', network, treatment, 'balanced')
                   for network in ('closed','open') for treatment in ('novelty','recognition')]
    comparisons += [('network', policy, 'open', 'closed')
                    for policy in ('balanced','novelty','recognition')]
    output = []
    for kind, stratum, treatment, control in comparisons:
        subset = [row for row in condition_rows
                  if row['network'] == stratum] if kind == 'policy' else [row for row in condition_rows
                  if row['policy'] == stratum]
        key = 'policy' if kind == 'policy' else 'network'
        index = {(row['seed'], row[key]): row for row in subset}
        for metric in metrics:
            differences = []
            for seed in expected_seeds:
                treated, base = index.get((seed,treatment)), index.get((seed,control))
                if treated and base and treated[metric] is not None and base[metric] is not None:
                    differences.append(treated[metric]-base[metric])
            complete = len(differences) == len(expected_seeds)
            low = high = mean = None
            status = 'incomplete_external_reviews'
            if complete:
                mean = statistics.mean(differences)
                low, high = paired_interval(differences, bootstrap_seed, repetitions,
                                            kind, stratum, treatment, metric)
                status = 'complete' if low is not None else 'insufficient_world_pairs'
            output.append({'comparison':kind,'stratum':stratum,'treatment':treatment,'control':control,
                'metric':metric,'n_world_pairs':len(differences),'expected_world_pairs':len(expected_seeds),
                'mean_difference':mean,'ci95_low':low,'ci95_high':high,'status':status,
                'interval_scope':'world_bootstrap_conditional_on_reviewed_sample'})
    for treatment in ('novelty','recognition'):
        for metric in metrics:
            differences = []
            for seed in expected_seeds:
                index = {(row['policy'],row['network']):row for row in condition_rows if row['seed'] == seed}
                cells = [(treatment,'open'),('balanced','open'),(treatment,'closed'),('balanced','closed')]
                if all(cell in index and index[cell][metric] is not None for cell in cells):
                    differences.append((index[cells[0]][metric]-index[cells[1]][metric])-
                                       (index[cells[2]][metric]-index[cells[3]][metric]))
            complete = len(differences) == len(expected_seeds)
            low = high = mean = None
            status = 'incomplete_external_reviews'
            if complete:
                mean = statistics.mean(differences)
                low, high = paired_interval(differences, bootstrap_seed, repetitions,
                                            'policy_x_network', treatment, metric)
                status = 'complete' if low is not None else 'insufficient_world_pairs'
            output.append({'comparison':'policy_x_network','stratum':'difference_in_differences',
                'treatment':treatment,'control':'balanced','metric':metric,
                'n_world_pairs':len(differences),'expected_world_pairs':len(expected_seeds),
                'mean_difference':mean,'ci95_low':low,'ci95_high':high,'status':status,
                'interval_scope':'world_bootstrap_conditional_on_reviewed_sample'})
    return output


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


# 对成果文本执行完全重复和连通分量近重复聚类。
def duplicate_card_metrics(cards, threshold):
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
            exact_pairs += exact
            if exact or score >= threshold:
                near_pairs += 1; union(i, j)
                if len(boundary) < 3:
                    boundary.append({'left_project_id': first[0], 'right_project_id': second[0], 'similarity': score})
    clusters = Counter(find(i) for i in range(len(cards)))
    return {'outputs':len(cards),'exact_pairs':exact_pairs,'near_pairs':near_pairs,'pair_count':pair_count,
            'unique_clusters':len(clusters),'cluster_sizes':sorted(clusters.values(), reverse=True),'boundary':boundary}


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
        metrics = duplicate_card_metrics(cards, threshold)
        rows.append({'seed': seed, 'policy': policy, 'network': network, 'outputs': metrics['outputs'],
            'exact_duplicate_pair_rate': metrics['exact_pairs']/metrics['pair_count'] if metrics['pair_count'] else None,
            'near_duplicate_pair_rate': metrics['near_pairs']/metrics['pair_count'] if metrics['pair_count'] else None,
            'unique_clusters': metrics['unique_clusters'], 'cluster_size_distribution': ';'.join(map(str, metrics['cluster_sizes'])),
            'redundant_output_rate': (metrics['outputs']-metrics['unique_clusters'])/metrics['outputs'] if metrics['outputs'] else None,
            'threshold': threshold, 'algorithm_version': DUPLICATE_VERSION,
            'boundary_examples': json.dumps(metrics['boundary'], separators=(',', ':'))})
    return rows


# 按历史正贡献者口径汇总单人和团队成果的分支内重复率。
def duplicate_output_type_rates(run_dir, threshold):
    config = json.loads((Path(run_dir)/'config.json').read_text(encoding='utf-8'))
    start_cycle = config['branch_tick']//6
    totals = {output_type:{'outputs':0,'clusters':0} for output_type in ('solo','team')}
    for _, _, _, _, state in branch_states(run_dir):
        efforts = final_effort(state, start_cycle)
        cards = {'solo':[], 'team':[]}
        for project in state['projects'].values():
            if project['status'] != 'completed' or project['created_tick']//6 < start_cycle:
                continue
            contributors = [agent for agent, units in efforts[project['id']].items() if units > 0]
            output_type = 'team' if len(contributors) >= 2 else 'solo'
            card = state['ideas'][project['origin_draft_ids'][0]]['versions'][-1]
            text = ' '.join(card[key] for key in ('title','hypothesis','method'))
            cards[output_type].append((project['id'], text, digest(normalize_text(text))))
        for output_type, values in cards.items():
            metrics = duplicate_card_metrics(values, threshold)
            totals[output_type]['outputs'] += metrics['outputs']
            totals[output_type]['clusters'] += metrics['unique_clusters']
    return {output_type:((value['outputs']-value['clusters'])/value['outputs'] if value['outputs'] else None)
            for output_type,value in totals.items()}


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
    run_config = json.loads((Path(run_dir)/'config.json').read_text(encoding='utf-8'))
    analysis_config = run_config['analysis']
    schemes = analysis_config.get('quality_weight_schemes', DEFAULT_QUALITY_WEIGHT_SCHEMES)
    if 'equal' not in schemes:
        raise ValueError('Quality sensitivity schemes must include equal')
    quality_by_scheme = {name: quality_scores(reviews, weights) for name, weights in schemes.items()}
    quality = quality_by_scheme['equal']
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
        dimension_estimates = {dimension: estimate_stratum_metric(n_total,
            [quality.get(rid, {}).get('dimension_means', {}).get(dimension) for rid in ids], 1.0, 5.0)
            for dimension in DIMENSIONS}
        stratum_rows.append({'seed': int(stratum[0]), 'policy': stratum[1], 'network': stratum[2],
            'output_type': stratum[3], 'N_h': n_total, 'n_h': n_h, 'valid_Q': len(valid_q),
            'observed_reviewed_quality_sum': estimate['observed_sum'],
            'estimated_population_quality_total': estimate['estimated_total'],
            'quality_status': 'complete' if estimate['complete'] else 'incomplete_external_reviews',
            'missing_Q_lower_bound': estimate['lower_bound'], 'missing_Q_upper_bound': estimate['upper_bound'],
            **{dimension+'_estimated_total': dimension_estimates[dimension]['estimated_total'] for dimension in DIMENSIONS}})
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
        dimension_totals = {dimension: ([row[dimension+'_estimated_total'] for row in values])
                            for dimension in DIMENSIONS}
        condition_rows.append({'seed':condition[0],'policy':condition[1],'network':condition[2],
          'population_completed_count':population_count,'sampled_ideas':sum(row['n_h'] for row in values),
          'valid_Q':sum(row['valid_Q'] for row in values),
          'observed_reviewed_quality_sum':sum(row['observed_reviewed_quality_sum'] for row in values),
          'estimated_population_quality_total':estimated,
          'estimated_mean_quality':estimated/population_count if estimated is not None and population_count else None,
          'estimated_quality_per_100_effort':100*estimated/effort if estimated is not None and effort else None,
          'missing_Q_lower_bound':sum(row['missing_Q_lower_bound'] for row in values),
          'missing_Q_upper_bound':sum(row['missing_Q_upper_bound'] for row in values),
          **{dimension+'_mean': (sum(totals)/population_count
              if population_count and all(value is not None for value in totals) else None)
             for dimension, totals in dimension_totals.items()},
          'quality_status':'complete' if complete else 'incomplete_external_reviews'})
    write_csv(output_dir/'quality_by_world_condition.csv', condition_rows)
    by_type = []
    duplicate_rates = duplicate_output_type_rates(run_dir, analysis_config['duplicates']['similarity_threshold'])
    for output_type in ('solo','team'):
        relevant = [row for row in stratum_rows if row['output_type'] == output_type]
        estimates = [row['estimated_population_quality_total'] for row in relevant if row['estimated_population_quality_total'] is not None]
        population_count = sum(row['N_h'] for row in relevant)
        complete = len(estimates) == len(relevant)
        total = sum(estimates) if complete else None
        dimension_totals = {dimension:[row[dimension+'_estimated_total'] for row in relevant]
                            for dimension in DIMENSIONS}
        by_type.append({'output_type': output_type, 'strata': len(relevant), 'complete_strata': len(estimates),
                        'population_completed_count':population_count,
                        'sampled_ideas':sum(row['n_h'] for row in relevant),
                        'valid_Q':sum(row['valid_Q'] for row in relevant),
                        'estimated_quality_total':total,
                        'estimated_mean_quality':total/population_count if total is not None and population_count else None,
                        **{dimension+'_mean':(sum(values)/population_count
                           if population_count and all(value is not None for value in values) else None)
                           for dimension,values in dimension_totals.items()},
                        'redundant_output_rate':duplicate_rates[output_type],
                        'status': 'complete' if complete else 'incomplete_external_reviews'})
    write_csv(output_dir/'quality_by_output_type.csv', by_type)
    effects = paired_quality_effects(condition_rows, sorted(run_config['seeds']),
                                     analysis_config['bootstrap_seed'], analysis_config['bootstrap_repetitions'])
    write_csv(output_dir/'quality_paired_effects.csv', effects)
    sensitivity = []
    for scheme_name, scheme_quality in quality_by_scheme.items():
        scheme_strata = []
        for stratum, ids in sorted(grouped.items()):
            n_total = int(sample_map[ids[0]]['N_h'])
            estimate = estimate_stratum_quality(n_total, [scheme_quality.get(rid, {}).get('Q') for rid in ids])
            scheme_strata.append({'output_type':stratum[3], 'N_h':n_total, 'n_h':len(ids), **estimate})
        for output_type in ('solo','team','all'):
            relevant = [row for row in scheme_strata if output_type == 'all' or row['output_type'] == output_type]
            complete = all(row['estimated_total'] is not None for row in relevant)
            population_count = sum(row['N_h'] for row in relevant)
            total = sum(row['estimated_total'] for row in relevant) if complete else None
            sensitivity.append({'weight_scheme':scheme_name,'weights':json.dumps(schemes[scheme_name], sort_keys=True),
                'output_type':output_type,'population_completed_count':population_count,
                'sampled_ideas':sum(row['n_h'] for row in relevant),
                'estimated_quality_total':total,
                'estimated_mean_quality':total/population_count if total is not None and population_count else None,
                'status':'complete' if complete else 'incomplete_external_reviews'})
    write_csv(output_dir/'quality_weight_sensitivity.csv', sensitivity)
    complete = bool(condition_rows) and all(row['quality_status'] == 'complete' for row in condition_rows)
    status = 'completed' if complete else ('awaiting_external_reviews' if not reviews else 'incomplete_external_reviews')
    dump(output_dir/'quality_status.json', {'schema_version': '0.3', 'status': status,
         'review_rows': len(reviews), 'requested_review_rows': 2*len(keys), 'sampled_ideas': len(keys),
         'double_rated_ideas':sum(len(values) >= 2 for values in valid_by_id.values()),
         'quality_formula_version': 'four_dimension_equal_weight_v03_1',
         'weight_schemes':schemes, 'paired_effect_rows':len(effects),
         'missing_policy': 'no_zero_imputation; incomplete estimates remain null'})
    return {'status': status, 'review_rows': len(reviews), 'sampled_ideas': len(keys),
            'double_rated_ideas':sum(len(values) >= 2 for values in valid_by_id.values()),
            'paired_effect_rows':len(effects),'weight_sensitivity_rows':len(sensitivity)}


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
