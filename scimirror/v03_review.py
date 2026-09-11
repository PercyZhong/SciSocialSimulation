"""Frozen sampling, blinded package export, review import and agreement metrics."""
import csv
import json
import random
import secrets
from collections import Counter, defaultdict
from pathlib import Path

from .common import canonical, digest, dump


DIMENSIONS = ('novelty', 'feasibility', 'scientific_value', 'evidence_support')
RUBRIC_VERSION = 'scimirror_review_v03_1'
SAMPLING_VERSION = 'stratified_srswor_v03_1'


# 读取CSV为字典行并兼容UTF-8 BOM。
def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


# 使用稳定字段顺序写入UTF-8 CSV。
def write_csv(path, rows, fields=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or (list(rows[0]) if rows else [])
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


# 沿项目合并链聚合每位历史贡献者的正投入。
def final_effort(final_state, start_cycle):
    projects = final_state['projects']
    result = defaultdict(lambda: defaultdict(float))
    for entry in final_state['effort_ledger']:
        if entry['cycle'] < start_cycle:
            continue
        project_id = entry['project_id']
        seen = set()
        while projects[project_id]['status'] == 'merged':
            if project_id in seen:
                raise ValueError('Project merge cycle in review population')
            seen.add(project_id)
            project_id = projects[project_id]['merged_into']
        result[project_id][entry['agent_id']] += entry['units']
    return result


# 冻结单次运行中干预期完成项目的评审总体及文本证据哈希。
def freeze_population(run_dir):
    run_dir = Path(run_dir).resolve()
    config = json.loads((run_dir/'config.json').read_text(encoding='utf-8'))
    start_cycle = config['branch_tick']//6
    rows = []
    for seed_dir in sorted(run_dir.glob('seed_*')):
        seed = int(seed_dir.name.split('_')[1])
        for branch_dir in sorted(path for path in seed_dir.iterdir() if path.is_dir() and path.name != 'prefix'):
            policy, network = branch_dir.name.rsplit('_', 1)
            state = json.loads((branch_dir/'final_state.json').read_text(encoding='utf-8'))
            effort = final_effort(state, start_cycle)
            for project in state['projects'].values():
                if project['status'] != 'completed' or project['created_tick']//6 < start_cycle:
                    continue
                draft = state['ideas'][project['origin_draft_ids'][0]]
                card = draft['versions'][-1]
                contributors = sorted(aid for aid, units in effort[project['id']].items() if units > 0)
                text = {key: card[key] for key in ('title', 'hypothesis', 'method', 'references')}
                rows.append({'project_id': project['id'], 'seed': seed, 'policy': policy, 'network': network,
                    'output_type': 'team' if len(contributors) >= 2 else 'solo', 'version_id': project['version_ids'][-1],
                    'title': card['title'], 'hypothesis': card['hypothesis'], 'method': card['method'],
                    'references': list(card['references']), 'historical_positive_contributors': contributors,
                    'text_evidence_hash': digest(text)})
    keys = [row['project_id']+'|'+str(row['seed'])+'|'+row['policy']+'|'+row['network'] for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate population project identity')
    return {'schema_version': '0.3', 'source_run': str(run_dir), 'population_completed_count': len(rows),
            'population_hash': digest(rows), 'items': rows}


# 按seed×policy×network×output_type执行可复现的不放回分层抽样。
def stratified_sample(population, sample_seed, per_stratum):
    strata = defaultdict(list)
    for item in population['items']:
        key = (item['seed'], item['policy'], item['network'], item['output_type'])
        strata[key].append(item)
    expected = [(seed, policy, network, output_type)
                for seed in sorted({item['seed'] for item in population['items']})
                for policy in ('balanced', 'novelty', 'recognition') for network in ('closed', 'open')
                for output_type in ('solo', 'team')]
    random_source = random.Random(sample_seed)
    rows, audit = [], []
    for key in expected:
        values = sorted(strata.get(key, []), key=lambda item: item['project_id'])
        count = min(per_stratum, len(values))
        chosen = random_source.sample(values, count) if count else []
        probability = count/len(values) if values else None
        rows.extend((item, key, probability) for item in chosen)
        audit.append({'seed': key[0], 'policy': key[1], 'network': key[2], 'output_type': key[3],
                      'N_h': len(values), 'n_h': count, 'inclusion_probability': probability})
    return rows, audit


# 扫描公开评审对象以阻止条件映射与制度评分泄漏。
def validate_public_payload(value):
    banned_keys = {'seed', 'policy', 'network', 'branch', 'project_id', 'institutional_reward',
                   'lexical_novelty_proxy', 'community_attention_proxy', 'agent_id', 'contributors'}
    banned_markers = ('balanced_closed', 'balanced_open', 'novelty_closed', 'novelty_open',
                      'recognition_closed', 'recognition_open', 'seed_')
    def walk(item):
        # 递归检查公开对象的键和值。
        if isinstance(item, dict):
            if banned_keys & set(item):
                raise ValueError(f'Public review payload leaks sensitive keys: {banned_keys & set(item)}')
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
        elif isinstance(item, str) and any(marker in item.lower() for marker in banned_markers):
            raise ValueError('Public review payload leaks condition marker')
    walk(value)


# 导出随机ID公开盲评包、私有映射、抽样权重和评分模板。
def export_review_package(run_dir, review_dir, review_cfg, corpus):
    review_dir = Path(review_dir)
    if review_dir.exists():
        raise FileExistsError('Refuse to overwrite review package')
    public_dir, private_dir, results_dir = review_dir/'review_public', review_dir/'review_private', review_dir/'review_results'
    public_dir.mkdir(parents=True); private_dir.mkdir(); results_dir.mkdir()
    population = freeze_population(run_dir)
    sampled, strata = stratified_sample(population, review_cfg['sample_seed'], review_cfg['per_stratum'])
    public_ideas, keys, sample_rows = [], [], []
    used_ids = set()
    for item, stratum, probability in sampled:
        review_id = 'rvw_'+secrets.token_hex(12)
        while review_id in used_ids:
            review_id = 'rvw_'+secrets.token_hex(12)
        used_ids.add(review_id)
        public_ideas.append({'review_id': review_id, 'title': item['title'], 'hypothesis': item['hypothesis'],
                             'method': item['method'], 'reference_ids': item['references'],
                             'text_evidence_hash': item['text_evidence_hash']})
        keys.append({'review_id': review_id, 'project_id': item['project_id'], 'seed': item['seed'],
                     'policy': item['policy'], 'network': item['network'], 'output_type': item['output_type'],
                     'version_id': item['version_id']})
        sample_rows.append({'review_id': review_id, 'stratum': '|'.join(map(str, stratum)),
                            'N_h': next(x['N_h'] for x in strata if tuple(x[k] for k in ('seed','policy','network','output_type')) == stratum),
                            'n_h': next(x['n_h'] for x in strata if tuple(x[k] for k in ('seed','policy','network','output_type')) == stratum),
                            'inclusion_probability': probability, 'sampling_round': 1})
    evidence_ids = sorted({reference for idea in public_ideas for reference in idea['reference_ids']})
    evidence = [{'evidence_id': paper_id, 'title': corpus.papers[paper_id]['title'],
                 'abstract': corpus.papers[paper_id]['abstract'], 'year': corpus.papers[paper_id]['year']}
                for paper_id in evidence_ids if paper_id in corpus.papers]
    validate_public_payload(public_ideas); validate_public_payload(evidence)
    (public_dir/'ideas.jsonl').write_text(''.join(canonical(row)+'\n' for row in public_ideas), encoding='utf-8')
    (public_dir/'evidence.jsonl').write_text(''.join(canonical(row)+'\n' for row in evidence), encoding='utf-8')
    rubric = '# Independent review rubric\n\nRubric version: '+RUBRIC_VERSION+'\n\nScore novelty, feasibility, scientific_value and evidence_support from 1–5 with a short reason and evidence ID. Use blank scores with evidence_access_status=unassessable when required evidence cannot be accessed. Novelty is relative only to supplied evidence.\n'
    (public_dir/'rubric.md').write_text(rubric, encoding='utf-8')
    fields = ['review_id','reviewer_id','reviewer_kind','model_version','rubric_version','evidence_access_status',
              'references_valid', *[f'{d}_{suffix}' for d in DIMENSIONS for suffix in ('score','reason','evidence_id')],
              'is_mock','started_at','completed_at','status']
    template = []
    for idea in public_ideas:
        for reviewer in review_cfg['reviewers']:
            row = {field: '' for field in fields}
            row.update(review_id=idea['review_id'], reviewer_id=reviewer['id'], reviewer_kind=reviewer['kind'],
                       rubric_version=RUBRIC_VERSION, is_mock='false', status='pending')
            template.append(row)
    write_csv(public_dir/'ratings_template.csv', template, fields)
    write_csv(private_dir/'review_key.csv', keys)
    write_csv(private_dir/'sample_manifest.csv', sample_rows)
    dump(private_dir/'reviewer_provenance.json', {'schema_version': '0.3', 'reviewers': review_cfg['reviewers'],
         'generation_backend': 'mock', 'review_independence_status': 'not_yet_reviewed'})
    dump(review_dir/'population_manifest.json', population)
    dump(private_dir/'sampling_audit.json', {'schema_version': '0.3', 'sample_seed': review_cfg['sample_seed'],
         'per_stratum': review_cfg['per_stratum'], 'sampling_algorithm_version': SAMPLING_VERSION,
         'population_hash': population['population_hash'], 'sample_hash': digest(sample_rows), 'strata': strata})
    dump(review_dir/'status.json', {'schema_version': '0.3', 'status': 'awaiting_external_reviews',
         'sampled_ideas': len(public_ideas), 'requested_ratings': len(template), 'is_mock': False})
    (results_dir/'raw_reviews.jsonl').write_text('', encoding='utf-8')
    write_csv(results_dir/'validated_reviews.csv', [], fields)
    write_csv(results_dir/'coverage.csv', [], ['seed','policy','network','output_type','sampled_ideas','ideas_with_any_rating','double_rated_ideas','missing_rate','unassessable_ideas'])
    write_csv(results_dir/'agreement.csv', [], ['dimension','common_items','exact_agreement_rate','within_one_rate','mean_absolute_disagreement','quadratic_weighted_kappa','kappa_null_reason'])
    write_csv(results_dir/'disagreements.csv', [], ['review_id','dimension','reviewer_1_score','reviewer_2_score','status'])
    return {'sampled_ideas': len(public_ideas), 'requested_ratings': len(template), 'empty_strata': sum(x['N_h'] == 0 for x in strata)}


# 验证评审包的抽样概率、随机ID唯一性、公开私有映射和敏感信息隔离。
def validate_review_package(review_dir):
    review_dir = Path(review_dir)
    ideas = [json.loads(line) for line in (review_dir/'review_public'/'ideas.jsonl').read_text(encoding='utf-8').splitlines()]
    evidence = [json.loads(line) for line in (review_dir/'review_public'/'evidence.jsonl').read_text(encoding='utf-8').splitlines()]
    keys = read_csv(review_dir/'review_private'/'review_key.csv')
    sample = read_csv(review_dir/'review_private'/'sample_manifest.csv')
    ratings = read_csv(review_dir/'review_public'/'ratings_template.csv')
    validate_public_payload(ideas); validate_public_payload(evidence)
    idea_ids = [row['review_id'] for row in ideas]
    checks = {'review_ids_unique': len(idea_ids) == len(set(idea_ids)),
      'random_id_shape': all(value.startswith('rvw_') and len(value) == 28 for value in idea_ids),
      'public_private_id_alignment': set(idea_ids) == {row['review_id'] for row in keys} == {row['review_id'] for row in sample},
      'sampling_probabilities_valid': all(abs(float(row['inclusion_probability'])-int(row['n_h'])/int(row['N_h'])) < 1e-12 for row in sample),
      'two_requested_reviewers': len(ratings) == 2*len(ideas),
      'no_mock_as_formal': all(row['is_mock'].lower() == 'false' for row in ratings),
      'status_waiting': json.loads((review_dir/'status.json').read_text(encoding='utf-8'))['status'] == 'awaiting_external_reviews'}
    return {'checks': checks, 'all_passed': all(checks.values()), 'sampled_ideas': len(ideas),
            'requested_ratings': len(ratings), 'external_scores_present': False}


# 验证并导入人工或外部模型评分且拒绝mock混入正式结果。
def import_reviews(review_dir, input_path):
    review_dir = Path(review_dir)
    ideas = [json.loads(line) for line in (review_dir/'review_public'/'ideas.jsonl').read_text(encoding='utf-8').splitlines()]
    allowed = {item['review_id']: set(item['reference_ids']) for item in ideas}
    provenance = json.loads((review_dir/'review_private'/'reviewer_provenance.json').read_text(encoding='utf-8'))
    allowed_reviewers = {item['id'] for item in provenance['reviewers']}
    rows = read_csv(input_path)
    if not rows:
        raise ValueError('No external review rows supplied')
    seen = set()
    normalized = []
    for row in rows:
        pair = (row.get('review_id'), row.get('reviewer_id'))
        if pair in seen or not all(pair) or pair[0] not in allowed or pair[1] not in allowed_reviewers:
            raise ValueError('Duplicate/unknown review identity')
        seen.add(pair)
        if row.get('rubric_version') != RUBRIC_VERSION or row.get('is_mock','').lower() == 'true':
            raise ValueError('Wrong rubric or mock review in formal import')
        access = row.get('evidence_access_status')
        if access not in ('available','unassessable') or row.get('references_valid','').lower() not in ('true','false'):
            raise ValueError('Invalid evidence access or reference-validation status')
        output = dict(row)
        for dimension in DIMENSIONS:
            raw = row.get(dimension+'_score', '').strip()
            if not raw:
                if access != 'unassessable':
                    raise ValueError('Missing score requires unassessable status')
                output[dimension+'_score'] = None
            else:
                score = int(raw)
                if score < 1 or score > 5:
                    raise ValueError('Score outside 1..5')
                if not row.get(dimension+'_reason','').strip():
                    raise ValueError('Each score requires a reason')
                evidence_id = row.get(dimension+'_evidence_id','').strip()
                if evidence_id and evidence_id not in allowed[pair[0]]:
                    raise ValueError('Review cites evidence outside public package')
                output[dimension+'_score'] = score
        normalized.append(output)
    results = review_dir/'review_results'
    (results/'raw_reviews.jsonl').write_text(''.join(canonical(row)+'\n' for row in rows), encoding='utf-8')
    write_csv(results/'validated_reviews.csv', normalized)
    dump(review_dir/'status.json', {'schema_version': '0.3', 'status': 'reviews_imported',
         'valid_rows': len(normalized), 'is_mock': False, 'input_hash': digest(rows)})
    return normalized


# 计算两个序数评分向量的二次加权Cohen kappa。
def quadratic_weighted_kappa(left, right):
    if len(left) != len(right) or not left:
        return None, 'no_common_items'
    if len(set(left+right)) == 1:
        return None, 'constant_ratings'
    total = len(left)
    observed = Counter(zip(left, right)); left_counts = Counter(left); right_counts = Counter(right)
    disagreement = sum(((a-b)/4)**2*count/total for (a,b), count in observed.items())
    expected = sum(((a-b)/4)**2*(left_counts[a]/total)*(right_counts[b]/total)
                   for a in range(1,6) for b in range(1,6))
    if expected == 0:
        return None, 'zero_expected_disagreement'
    return 1-disagreement/expected, None


# 汇总逐维双评分覆盖、绝对分歧与序数一致性。
def agreement_rows(reviews):
    grouped = defaultdict(list)
    for row in reviews:
        grouped[row['review_id']].append(row)
    output = []
    for dimension in DIMENSIONS:
        pairs = []
        for values in grouped.values():
            valid = [x[dimension+'_score'] for x in values if x[dimension+'_score'] is not None]
            if len(valid) >= 2:
                pairs.append(valid[:2])
        left, right = [x[0] for x in pairs], [x[1] for x in pairs]
        kappa, reason = quadratic_weighted_kappa(left, right)
        output.append({'dimension': dimension, 'common_items': len(pairs),
            'exact_agreement_rate': sum(a == b for a,b in pairs)/len(pairs) if pairs else None,
            'within_one_rate': sum(abs(a-b) <= 1 for a,b in pairs)/len(pairs) if pairs else None,
            'mean_absolute_disagreement': sum(abs(a-b) for a,b in pairs)/len(pairs) if pairs else None,
            'quadratic_weighted_kappa': kappa, 'kappa_null_reason': reason})
    return output


# 按预注册四维等权公式计算具备双评分的项目质量Q。
def quality_scores(reviews):
    grouped = defaultdict(list)
    for row in reviews:
        grouped[row['review_id']].append(row)
    result = {}
    for review_id, rows in grouped.items():
        means = {}
        for dimension in DIMENSIONS:
            values = [row[dimension+'_score'] for row in rows if row[dimension+'_score'] is not None]
            if len(values) >= 2:
                means[dimension] = sum(values[:2])/2
        result[review_id] = {'dimension_means': means,
            'Q': sum((means[d]-1)/4 for d in DIMENSIONS)/4 if len(means) == 4 else None,
            'quality_formula_version': 'four_dimension_equal_weight_v03_1'}
    return result


# 生成只用于测试schema与公式且禁止正式导入的确定性mock评分。
def mock_review_records(public_ideas, reviewers=('mock_a','mock_b')):
    rows = []
    for idea in public_ideas:
        for reviewer in reviewers:
            base = 1 + int(digest([idea['review_id'], reviewer])[:4], 16) % 5
            row = {'review_id':idea['review_id'],'reviewer_id':reviewer,'reviewer_kind':'mock_review',
                   'model_version':'deterministic_fixture','rubric_version':RUBRIC_VERSION,
                   'evidence_access_status':'available','references_valid':'true','is_mock':True,
                   'validation_only':True,'status':'completed'}
            for dimension in DIMENSIONS:
                row.update({dimension+'_score':base,dimension+'_reason':'schema validation only',
                            dimension+'_evidence_id':idea['reference_ids'][0] if idea['reference_ids'] else ''})
            rows.append(row)
    return rows
