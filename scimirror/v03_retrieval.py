"""Versioned query construction, relevance gating and retrieval diagnostics."""
import math
import re
from collections import Counter

from .common import digest
from .corpus import similarity, tokens


QUERY_BUILDER_VERSION = 'v03_weighted_topic_field_memory_1'
DEDUP_VERSION = 'v03_exact_id_doi_text_plus_greedy_1'


# 将文本规范化为用于完全重复检测的稳定形式。
def normalize_text(text):
    return ' '.join(re.findall(r'[a-z0-9]+', text.lower()))


# 构建保留topic_id并展开描述、关键词、领域和记忆的查询对象。
def build_query(topic_model, topic_id, field_name, memory_terms, weights):
    if topic_id not in topic_model.topics:
        topic_words = set(tokens(topic_id.replace('_', ' ')))
        unknown = True
    else:
        topic = topic_model.topics[topic_id]
        topic_words = tokens(topic_id.replace('_', ' ')+' '+topic['description']+' '+' '.join(topic['keywords']))
        unknown = False
    components = {'topic': sorted(topic_words), 'field': sorted(tokens(field_name)),
                  'memory': sorted(tokens(' '.join(memory_terms)))}
    normalized = ' '.join(word for key in ('topic', 'field', 'memory') for word in components[key])
    return {'topic_id': topic_id, 'unknown_topic': unknown, 'components': components,
            'weights': {key: float(weights[key]) for key in ('topic', 'field', 'memory')},
            'normalized_query': normalized, 'query_builder_version': QUERY_BUILDER_VERSION}


# 计算论文文本对查询各组成部分的召回式词法相关性。
def component_relevance(query, paper):
    paper_words = tokens(paper['field']+' '+paper['title']+' '+paper['abstract'])
    scores = {}
    for name, words in query['components'].items():
        word_set = set(words)
        scores[name] = len(word_set & paper_words)/len(word_set) if word_set else 0.0
    raw = sum(query['weights'][name]*scores[name] for name in scores)
    return raw, scores


# 依据DOI、规范化全文和冻结词法阈值给论文分配近重复簇。
def duplicate_clusters(items, threshold):
    clusters = []
    assignments = {}
    exact = {}
    for item in sorted(items, key=lambda value: value['paper']['id']):
        paper = item['paper']
        exact_key = ('doi', paper['doi'].lower()) if paper.get('doi') else (
                    'text', normalize_text(paper['title']+' '+paper['abstract']))
        if exact_key in exact:
            assignments[paper['id']] = exact[exact_key]
            continue
        text = paper['title']+' '+paper['abstract']
        cluster_id = None
        for index, representative in enumerate(clusters):
            if similarity(text, representative['title']+' '+representative['abstract']) >= threshold:
                cluster_id = f'cluster_{index:04d}'
                break
        if cluster_id is None:
            cluster_id = f'cluster_{len(clusters):04d}'
            clusters.append(paper)
        assignments[paper['id']] = cluster_id
        exact[exact_key] = cluster_id
    return assignments


# 在共同相关性合格池内按政策特征重排并允许证据不足短缺。
def retrieve_relevance_gated(papers, topic_model, policy_context, config, topic_id, field_name,
                             memory_terms, read_texts):
    query = build_query(topic_model, topic_id, field_name, memory_terms, config['query_weights'])
    scored = []
    for paper in papers.values():
        raw, components = component_relevance(query, paper)
        exploration = (sum(1-similarity(paper['title']+' '+paper['abstract'], old) for old in read_texts)/len(read_texts)
                       if read_texts else .5)
        topic_ids = topic_model.paper_topics[paper['id']]
        attention = sum(topic_model.attention[t] for t in topic_ids)/len(topic_ids) if topic_ids else 0.0
        scored.append({'paper': paper, 'raw_relevance': raw, 'component_relevance': components,
                       'exploration': exploration, 'attention': attention, 'topic_ids': topic_ids})
    peak = max((item['raw_relevance'] for item in scored), default=0.0)
    for item in scored:
        item['normalized_relevance'] = item['raw_relevance']/peak if peak > 0 else 0.0
    base = sorted(scored, key=lambda item: (-item['raw_relevance'], item['paper']['id']))[:config['candidate_pool_size']]
    qualified = [item for item in base if item['raw_relevance'] >= config['minimum_relevance']]
    cluster_ids = duplicate_clusters(qualified, config['near_duplicate_threshold'])
    wn, wr = policy_context.weights
    for item in qualified:
        item['cluster_id'] = cluster_ids[item['paper']['id']]
        item['policy_score'] = wn*item['exploration'] + wr*item['attention']
        item['final_score'] = (config['relevance_weight']*item['normalized_relevance'] +
                               (1-config['relevance_weight'])*item['policy_score'])
    ranked = sorted(qualified, key=lambda item: (-item['final_score'], -item['raw_relevance'], item['paper']['id']))
    selected, cluster_counts = [], Counter()
    for item in ranked:
        if cluster_counts[item['cluster_id']] >= config['max_per_near_duplicate_cluster']:
            continue
        selected.append(item)
        cluster_counts[item['cluster_id']] += 1
        if len(selected) == config['top_k']:
            break
    reason = None
    if len(selected) < config['top_k']:
        reason = 'all_relevance_zero' if peak == 0 else ('insufficient_relevant_documents' if len(qualified) < config['top_k']
                  else 'near_duplicate_limit')
    query_id = digest({'query': query, 'corpus': digest(sorted(papers)), 'config': config})[:20]
    audit_items = []
    for item in base:
        audit_items.append({'paper_id': item['paper']['id'], 'raw_relevance': item['raw_relevance'],
            'normalized_relevance': item['normalized_relevance'], 'component_relevance': item['component_relevance'],
            'exploration': item['exploration'], 'attention': item['attention'], 'topic_ids': item['topic_ids'],
            'qualified': item in qualified, 'cluster_id': item.get('cluster_id'),
            'policy_score': item.get('policy_score'), 'final_score': item.get('final_score')})
    audit = {'schema_version': '0.3', 'mode': 'relevance_gated', 'query_id': query_id, **query,
             'corpus_hash': digest(papers), 'dedup_version': DEDUP_VERSION,
             'candidate_pool_size': len(base), 'qualified_count': len(qualified),
             'selected_count': len(selected), 'selected_ids': [item['paper']['id'] for item in selected],
             'selected_topic_ids': [item['topic_ids'] for item in selected], 'fallback': reason,
             'minimum_relevance': config['minimum_relevance'], 'candidates': audit_items,
             'policy_context': policy_context.audit()}
    return [item['paper'] for item in selected], audit


# 汇总一条检索审计的主题匹配、覆盖、相关性和近重复指标。
def audit_metrics(audit):
    selected_ids = set(audit['selected_ids'])
    selected = [item for item in audit['candidates'] if item['paper_id'] in selected_ids]
    labeled = [item for item in selected if item['topic_ids']]
    matches = [audit['topic_id'] in item['topic_ids'] for item in labeled]
    clusters = {item['cluster_id'] for item in selected if item.get('cluster_id')}
    pair_count = len(selected)*(len(selected)-1)//2
    duplicate_pairs = sum(a.get('cluster_id') == b.get('cluster_id') for index, a in enumerate(selected)
                          for b in selected[index+1:] if a.get('cluster_id'))
    pool_scores = sorted(item['raw_relevance'] for item in audit['candidates'])
    selected_scores = sorted(item['raw_relevance'] for item in selected)
    return {'query_id': audit['query_id'], 'topic_id': audit['topic_id'], 'selected_count': len(selected),
            'topic_match_rate': sum(matches)/len(matches) if matches else None,
            'unlabeled_selected': len(selected)-len(labeled),
            'topic_coverage': len({topic for item in selected for topic in item['topic_ids']}),
            'pool_relevance_min': pool_scores[0] if pool_scores else None,
            'pool_relevance_median': pool_scores[len(pool_scores)//2] if pool_scores else None,
            'pool_relevance_max': pool_scores[-1] if pool_scores else None,
            'pool_zero_rate': sum(score == 0 for score in pool_scores)/len(pool_scores) if pool_scores else None,
            'pool_qualified_rate': sum(item['qualified'] for item in audit['candidates'])/len(audit['candidates']) if audit['candidates'] else None,
            'selected_relevance_mean': sum(selected_scores)/len(selected_scores) if selected_scores else None,
            'near_duplicate_pair_rate': duplicate_pairs/pair_count if pair_count else None,
            'unique_cluster_ratio': len(clusters)/len(selected) if selected else None,
            'retrieval_shortfall': len(selected) < 3, 'fallback': audit['fallback']}


# 计算两个文献集合的Jaccard与交集对较小集合比例。
def document_overlap(left, right):
    left, right = set(left), set(right)
    if not left and not right:
        return {'jaccard': None, 'intersection_over_min': None, 'reason': 'both_empty'}
    union = left | right
    minimum = min(len(left), len(right))
    return {'jaccard': len(left & right)/len(union),
            'intersection_over_min': len(left & right)/minimum if minimum else None,
            'reason': None if minimum else 'one_empty'}
