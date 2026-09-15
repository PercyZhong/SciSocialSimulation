"""Offline Stage B real-corpus and blind-annotation tool tests."""
import csv, json, tempfile, unittest
from pathlib import Path

from scimirror.stage_b_retrieval import analyze,bm25_scores,export_annotation,import_annotations,import_corpus,import_queries,init_pilot,normalize_identifier,pool

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
        self.assertEqual(0,len(list(csv.DictReader((self.run/'queries_template.csv').open(encoding='utf-8-sig')))))
        self.assertEqual('awaiting_human_labels',analyze(self.run)['status'])

    # Reject a Codex signature in a field reserved for independent human reviewers.
    def test_codex_cannot_impersonate_reviewer(self):
        (self.run/'annotation_public'/'candidates.csv').write_text('blind_id\nb1\n',encoding='utf-8-sig'); source=Path(self.temp.name)/'ratings.csv'; source.write_text('blind_id,reviewer_id,relevance_0_1_2,unsure,rationale,rated_at\nb1,codex,2,false,x,now\n')
        with self.assertRaises(ValueError): import_annotations(source,self.run)

    # Require human provenance fields instead of silently adding a synthetic timestamp or basis.
    def test_annotation_requires_basis_and_time(self):
        (self.run/'annotation_public'/'candidates.csv').write_text('blind_id\nb1\n',encoding='utf-8-sig'); source=Path(self.temp.name)/'ratings.csv'
        source.write_text('blind_id,reviewer_id,relevance_0_1_2,unsure,rationale,rated_at\nb1,human_a,2,false,,\n')
        with self.assertRaises(ValueError): import_annotations(source,self.run)


if __name__=='__main__': unittest.main()
