"""Traceability and conflict tests for legacy human gold review tooling."""
import csv
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

from scimirror.retrieval_gold_audit import export_human_review,import_gold_decisions
from scimirror.stage_a_repair import dataset_registry,load_config

ROOT=Path(__file__).resolve().parents[1]


class GoldReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.review=Path(self.temp.name); self.config=load_config(ROOT/'configs/stage_a_repair.json',ROOT); self.registry=dataset_registry(self.config,ROOT); export_human_review(self.config,ROOT,self.review,self.registry)
        with (self.review/'human_gold_review_template.csv').open(encoding='utf-8-sig') as stream: self.rows=list(csv.DictReader(stream))

    def tearDown(self): self.temp.cleanup()

    def write(self,rows):
        path=self.review/'decisions.csv'
        with path.open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
        return path

    # Require the excerpt to be locatable and the base qrels version to match.
    def test_invalid_excerpt_rejected(self):
        row=dict(self.rows[0],decision='retain',rationale='manual judgment',evidence_excerpt='not present anywhere',reviewer_id='reviewer-x',reviewed_at='2026-09-17')
        with self.assertRaises(ValueError): import_gold_decisions(self.write([row]),self.review,ROOT,self.config,self.registry)

    # Keep conflicting member-level decisions in a queue rather than collapsing a family label.
    def test_family_conflict_is_not_silently_collapsed(self):
        grouped=defaultdict(list)
        for row in self.rows: grouped[(row['topic_id'],row['gold_family_id'])].append(row)
        pair=next(values[:2] for values in grouped.values() if len(values)>=2); decisions=[]
        for grade,row in zip((0,2),pair): decisions.append(dict(row,decision='revise',proposed_grade=str(grade),rationale='manual judgment',evidence_excerpt=row['title'],reviewer_id='reviewer-x',reviewed_at='2026-09-17'))
        result=import_gold_decisions(self.write(decisions),self.review,ROOT,self.config,self.registry)
        self.assertEqual('conflict',result['status']); self.assertEqual(1,result['family_conflicts'])


if __name__=='__main__': unittest.main()
