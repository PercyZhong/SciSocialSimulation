"""Offline Stage B real-corpus and blind-annotation tool tests."""
import csv, json, tempfile, unittest
from pathlib import Path

from scimirror.stage_b_retrieval import analyze,bm25_scores,import_corpus,import_reviewers,init_pilot,normalize_identifier

ROOT=Path(__file__).resolve().parents[1]


class StageBRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.run=Path(self.temp.name)/'run'; self.config=json.loads((ROOT/'configs/stage_b_retrieval_pilot.json').read_text()); init_pilot(self.run,self.config)

    def tearDown(self): self.temp.cleanup()

    # Normalize DOI and arXiv versions without using a network.
    def test_identifier_normalization(self):
        self.assertEqual('10.1/abc',normalize_identifier('https://doi.org/10.1/ABC','doi')); self.assertEqual('2401.00001',normalize_identifier('arXiv:2401.00001v2','arxiv'))

    # Reject synthetic input and exclude missing abstracts rather than inventing text.
    def test_real_import_validation(self):
        source=Path(self.temp.name)/'papers.jsonl'; source.write_text('\n'.join([
          json.dumps({'paper_id':'p1','title':'A','abstract':'Real abstract','year':2020,'source_url':'https://example.org/1','synthetic':False}),
          json.dumps({'paper_id':'p2','title':'B','abstract':'','year':2020,'source_url':'https://example.org/2','synthetic':False}),
          json.dumps({'paper_id':'p3','title':'C','abstract':'Synthetic','year':2020,'source_url':'https://example.org/3','synthetic':True})])+'\n')
        status=import_corpus(source,self.run,self.config); self.assertEqual(1,status['documents']); self.assertEqual(2,status['excluded'])

    # Verify free-text BM25 rankings change for different queries on the same topic corpus.
    def test_free_text_query_changes_scores(self):
        papers={'a':{'title':'episodic memory','abstract':'agent recall'},'b':{'title':'proposal evaluation','abstract':'scientific idea'}}
        self.assertGreater(bm25_scores('episodic memory',papers)['a'],bm25_scores('episodic memory',papers)['b'])
        self.assertGreater(bm25_scores('proposal evaluation',papers)['b'],bm25_scores('proposal evaluation',papers)['a'])

    # Keep initialized annotation files empty and the scientific status pending.
    def test_empty_templates_and_pending_status(self):
        with (self.run/'queries_template.csv').open(encoding='utf-8-sig') as stream: rows=list(csv.DictReader(stream))
        self.assertEqual(0,len(rows))
        self.assertEqual('invalid_empty_scope',analyze(self.run)['status'])

    # Require exactly two frozen rating roles instead of inferring reviewers from rows.
    def test_reviewer_registry_requires_two_rating_roles(self):
        source=Path(self.temp.name)/'reviewers.json'; source.write_text(json.dumps({'reviewers':[{'reviewer_id':'a','role':'reviewer'}]}))
        with self.assertRaises(ValueError): import_reviewers(source,self.run)

    # Preserve explicit test-only state in synthetic workflow fixtures.
    def test_test_only_status(self):
        other=Path(self.temp.name)/'test_only'; cfg=dict(self.config,test_only=True); status=init_pilot(other,cfg)
        self.assertTrue(status['test_only'])


if __name__=='__main__': unittest.main()
