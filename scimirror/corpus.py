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
    def __init__(self, path, cutoff, allow_synthetic):
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

    # 按词汇相似度检索最相关的前 k 篇文献。
    def retrieve(self, query, k=3):
        return sorted(self.papers.values(), key=lambda p: (-similarity(query, p['field']+' '+p['title']+' '+p['abstract']), p['id']))[:k]

    # 以文本和历史语料的最大相似度补数计算新颖性代理。
    def novelty(self, text):
        return 1 - max(similarity(text, p['title']+' '+p['abstract']) for p in self.papers.values())
