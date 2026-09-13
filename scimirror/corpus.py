"""Local corpus only. Lexical retrieval is deliberately a transparent MVP proxy."""
import json
import re
from pathlib import Path


# 将文本规范化为用于检索和指标计算的英文词元集合。
def tokens(text):
    return set(re.findall(r'[a-z0-9_]+', text.lower()))


# 计算两个文本词元集合之间的 Jaccard 相似度。
def similarity(a, b):
    a, b = tokens(a), tokens(b)
    return len(a & b) / max(1, len(a | b))


class Corpus:
    # 加载并校验语料，同时应用年份边界和合成语料限制。
    def __init__(self, path, cutoff, allow_synthetic, retrieval_config=None):
        raw = [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]
        self.papers = {}
        for p in raw:
            for key in ['id', 'title', 'abstract', 'year', 'field', 'synthetic']:
                if key not in p:
                    raise ValueError(f'Corpus missing {key}')
            if any(not isinstance(p[k], str) or not p[k].strip() for k in ['id', 'title', 'abstract', 'field']):
                raise ValueError('Corpus text fields must be nonempty strings')
            if p['id'] in self.papers:
                raise ValueError('Duplicate paper id')
            if not isinstance(p['year'], int) or not isinstance(p['synthetic'], bool):
                raise ValueError('year must be integer; synthetic must be boolean')
            if p['year'] >= cutoff:
                continue
            if p['synthetic'] and not allow_synthetic:
                raise ValueError('Synthetic corpus forbidden by config')
            self.papers[p['id']] = p
        if len(self.papers) < 3:
            raise ValueError('Need at least 3 pre-cutoff papers')
        self.retrieval_config = retrieval_config

    # 按词汇相似度检索最相关的前 k 篇文献。
    def retrieve(self, query, k=3):
        return sorted(self.papers.values(), key=lambda p: (-similarity(query, p['field']+' '+p['title']+' '+p['abstract']), p['id']))[:k]

    # 以文本和历史语料的最大相似度补数计算新颖性代理。
    def novelty(self, text):
        return 1 - max(similarity(text, p['title']+' '+p['abstract']) for p in self.papers.values())

    # 从统一候选池按query相关性与政策特征选择固定数量文献并返回审计信息。
    def retrieve_v02(self, query, read_ids, topic_model, policy_context, candidate_pool_size, top_k,
                     selected_topic_id=None, field_name=None, memory_terms=None):
        if self.retrieval_config and self.retrieval_config.get('mode') == 'relevance_gated':
            from .v03_retrieval import retrieve_relevance_gated
            read_texts = [self.papers[x]['title']+' '+self.papers[x]['abstract'] for x in read_ids if x in self.papers]
            return retrieve_relevance_gated(self.papers, topic_model, policy_context, self.retrieval_config,
                                            selected_topic_id, field_name, memory_terms or [], read_texts)
        if self.retrieval_config and self.retrieval_config.get('mode') == 'stage_a_fixed':
            from .v03_retrieval import retrieve_stage_a_fixed
            read_texts = [self.papers[x]['title']+' '+self.papers[x]['abstract'] for x in read_ids if x in self.papers]
            return retrieve_stage_a_fixed(self.papers, topic_model, policy_context, self.retrieval_config,
                                          selected_topic_id, field_name, memory_terms or [], read_texts)
        scored = []
        read_texts = [self.papers[x]['title']+' '+self.papers[x]['abstract'] for x in read_ids if x in self.papers]
        for paper in self.papers.values():
            text = paper['field']+' '+paper['title']+' '+paper['abstract']
            relevance = similarity(query, text)
            exploration = (sum(1-similarity(text, old) for old in read_texts)/len(read_texts)
                           if read_texts else .5)
            topic_ids = topic_model.paper_topics[paper['id']]
            attention = (sum(topic_model.attention[t] for t in topic_ids)/len(topic_ids) if topic_ids else 0.0)
            scored.append({'paper': paper, 'query_relevance': relevance, 'exploration': exploration,
                           'recognition': attention, 'topic_ids': topic_ids})
        pool = sorted(scored, key=lambda x: (-x['query_relevance'], x['paper']['id']))[:min(candidate_pool_size, len(scored))]
        wn, wr = policy_context.weights
        for item in pool:
            item['policy_weighted_features'] = wn*item['exploration'] + wr*item['recognition']
            item['retrieval_score'] = .5*item['query_relevance'] + .5*item['policy_weighted_features']
        selected = sorted(pool, key=lambda x: (-x['retrieval_score'], x['paper']['id']))[:min(top_k, len(pool))]
        audit = {'query': query, 'candidate_pool_size': len(pool), 'selected_count': len(selected),
                 'candidates': [{k: v for k, v in item.items() if k != 'paper'} |
                                {'paper_id': item['paper']['id']} for item in pool],
                 'selected_ids': [item['paper']['id'] for item in selected],
                 'policy_context': policy_context.audit()}
        return [item['paper'] for item in selected], audit
