"""Frozen topic classification and community-attention recognition for v0.2."""
import json
from collections import Counter
from pathlib import Path

from .common import digest
from .corpus import tokens


class TopicModel:
    # 加载冻结主题词表并用截点前语料构建固定关注度快照。
    def __init__(self, topics_path, papers, alpha=1.0):
        topics = json.loads(Path(topics_path).read_text(encoding='utf-8'))
        if not topics or alpha <= 0:
            raise ValueError('topics required and alpha must be positive')
        self.topics = {t['topic_id']: t for t in topics}
        if len(self.topics) != len(topics):
            raise ValueError('Duplicate topic_id')
        self.alpha = float(alpha)
        self.paper_topics = {}
        counts = Counter()
        unknown = []
        for paper in papers.values():
            labels = list(paper.get('topic_ids') or self.classify(paper['title']+' '+paper['abstract']))
            labels = sorted(set(labels))
            if not labels:
                unknown.append(paper['id'])
                self.paper_topics[paper['id']] = []
                continue
            if not set(labels) <= set(self.topics):
                raise ValueError('Paper contains unknown topic_id')
            self.paper_topics[paper['id']] = labels
            for topic_id in labels:
                counts[topic_id] += 1 / len(labels)
        labeled = sum(bool(v) for v in self.paper_topics.values())
        denominator = labeled + self.alpha * len(self.topics)
        probabilities = {k: (counts[k]+self.alpha)/denominator for k in self.topics}
        peak = max(probabilities.values())
        self.attention = {k: probabilities[k]/peak for k in self.topics}
        self.audit = {'papers_total': len(papers), 'papers_labeled': labeled,
                      'coverage': labeled/max(1, len(papers)), 'unknown_paper_ids': unknown,
                      'fractional_counts': dict(counts), 'attention': self.attention,
                      'classifier_hash': digest(topics), 'snapshot_hash': ''}
        self.audit['snapshot_hash'] = digest(self.audit)

    # 使用冻结关键词规则为文本返回全部命中的主题ID。
    def classify(self, text):
        words = tokens(text)
        return sorted(topic_id for topic_id, topic in self.topics.items()
                      if words & set(topic['keywords']))

    # 计算文本主题的固定历史关注度均值并返回评分依据。
    def recognition(self, text):
        topic_ids = self.classify(text)
        score = sum(self.attention[t] for t in topic_ids)/len(topic_ids) if topic_ids else 0.0
        return score, topic_ids

    # 返回主题的公开领域和固定历史关注度特征。
    def features(self, topic_id):
        topic = self.topics[topic_id]
        return {'topic_id': topic_id, 'field': topic['field'],
                'attention': self.attention[topic_id], 'keywords': list(topic['keywords'])}


