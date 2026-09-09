"""Local corpus only. Lexical retrieval is deliberately a transparent MVP proxy."""
import json
import re
from pathlib import Path


def tokens(text):
    return set(re.findall(r'[a-z0-9_]+', text.lower()))


def similarity(a, b):
    a, b = tokens(a), tokens(b)
    return len(a & b) / max(1, len(a | b))


class Corpus:
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

    def retrieve(self, query, k=3):
        return sorted(self.papers.values(), key=lambda p: (-similarity(query, p['field']+' '+p['title']+' '+p['abstract']), p['id']))[:k]

    def novelty(self, text):
        return 1 - max(similarity(text, p['title']+' '+p['abstract']) for p in self.papers.values())
