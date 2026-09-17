"""End-to-end test-only checks for the frozen small real-pilot workflow."""
import csv
import json
import tempfile
import unittest
from pathlib import Path

from scimirror.stage_a import read_jsonl
from scimirror.stage_b_retrieval import (agreement_metrics, analyze, coverage_status, export_annotation, freeze,
    import_annotations, import_corpus, import_family_map, import_queries, import_reviewers,
    init_pilot, pool)

ROOT=Path(__file__).resolve().parents[1]


class StageBSmallPilotTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.base=Path(self.temp.name); self.run=self.base/'run'
        self.config=json.loads((ROOT/'configs/stage_b_retrieval_small.json').read_text()); self.config['test_only']=True; init_pilot(self.run,self.config)

    def tearDown(self): self.temp.cleanup()

    def prepare(self):
        papers=[]
        for index in range(30):
            topic='episodic memory retrieval'
            papers.append({'paper_id':f'p{index:02d}','title':f'{topic} study {index}','abstract':f'Evidence about {topic} and methods {index}.','year':2024,'source_url':f'https://example.org/{index}','synthetic':False,'field':'unknown'})
        corpus=self.base/'papers.jsonl'; corpus.write_text(''.join(json.dumps(x)+'\n' for x in papers)); self.assertEqual('ready',import_corpus(corpus,self.run)['data_status'])
        family=self.base/'family.csv'
        with family.open('w',encoding='utf-8',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=['paper_id','family_id','canonical','split','reviewer_id','rationale','updated_at']); writer.writeheader()
            for index in range(30): writer.writerow({'paper_id':f'p{index:02d}','family_id':f'f{index:02d}','canonical':'true','split':'pilot','reviewer_id':'mapper','rationale':'distinct work','updated_at':'2026-09-17'})
        import_family_map(family,self.run)
        queries=self.base/'queries.csv'
        with queries.open('w',encoding='utf-8',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=['query_id','topic_id','query_text','intent','author_type','split']); writer.writeheader()
            for topic in self.config['topics']:
                for index in range(2): writer.writerow({'query_id':f'{topic}_{index}','topic_id':topic,'query_text':f'{topic.replace("_"," ")} evidence {index}','intent':'diagnostic','author_type':'test_fixture','split':'pilot'})
        import_queries(queries,self.run)
        reviewers=self.base/'reviewers.json'; reviewers.write_text(json.dumps({'reviewers':[{'reviewer_id':'A','role':'reviewer'},{'reviewer_id':'B','role':'reviewer'},{'reviewer_id':'J','role':'adjudicator'}]})); import_reviewers(reviewers,self.run)
        freeze(self.run,ROOT); pool(self.run,None,ROOT); export_annotation(self.run)

    # Exercise the complete test-only workflow and retain empty gated ranker rows.
    def test_complete_tool_flow_and_empty_run_rows(self):
        self.prepare(); runs=read_jsonl(self.run/'private'/'ranker_runs.jsonl')
        with (self.run/'annotation_public'/'candidates.csv').open(encoding='utf-8-sig') as stream: candidates=list(csv.DictReader(stream))
        self.assertEqual(18,len(runs)); self.assertEqual({False,True},{row['empty'] for row in runs}); self.assertEqual(180,len(candidates))
        memory_repaired=[row for row in runs if row['ranker']=='stage_a_repaired_v1' and row['query_id'].startswith('agent_memory_')]
        self.assertNotEqual(memory_repaired[0]['ranked_ids'],memory_repaired[1]['ranked_ids'])
        self.assertEqual('partial_analysis',analyze(self.run)['status']); self.assertTrue(json.loads((self.run/'status.json').read_text())['test_only'])
        for reviewer in ('A','B'):
            source=self.run/'annotation_public'/f'ratings_{reviewer}.csv'
            with source.open(encoding='utf-8-sig') as stream: rows=list(csv.DictReader(stream))
            for row in rows: row.update(relevance_0_1_2='1',unsure='false',rationale='test-only fixture judgment',rated_at='2026-09-17',revision='1')
            completed=self.base/f'completed_{reviewer}.csv'
            with completed.open('w',encoding='utf-8',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
            import_annotations(completed,self.run)
        result=analyze(self.run); self.assertEqual('analysis_completed',result['status']); self.assertEqual(1.0,result['resolved_coverage']); self.assertIsNone(result['linear_weighted_kappa']); self.assertEqual(54,result['metric_rows'])

    # Reject family split leakage and preserve one canonical per family.
    def test_family_cross_split_rejected(self):
        papers=self.base/'papers.jsonl'; papers.write_text(''.join(json.dumps({'paper_id':f'p{i}','title':f'T{i}','abstract':f'Abstract {i}','year':2024,'source_url':f'https://x/{i}','synthetic':False})+'\n' for i in range(30))); import_corpus(papers,self.run)
        mapping=self.base/'family.csv'; mapping.write_text('paper_id,family_id,canonical,split,reviewer_id,rationale,updated_at\n'+'\n'.join(f'p{i},f,true,{"pilot" if i==0 else "test"},m,x,now' for i in range(30))+'\n')
        with self.assertRaises(ValueError): import_family_map(mapping,self.run)

    # Count null abstracts, future years, and DOI/arXiv/text aliases without fabrication.
    def test_import_counts_and_aliases(self):
        rows=[{'paper_id':'a','title':'A','abstract':'Body','year':2024,'source_url':'x','synthetic':False,'doi':'10/x'},
          {'paper_id':'b','title':'B','abstract':'Other','year':2024,'source_url':'x','synthetic':False,'doi':'https://doi.org/10/X'},
          {'paper_id':'c','title':'A','abstract':'Body','year':2024,'source_url':'x','synthetic':False,'arxiv_id':'1v2'},
          {'paper_id':'d','title':'D','abstract':None,'year':2024,'source_url':'x','synthetic':False},
          {'paper_id':'e','title':'E','abstract':'Future','year':2025,'source_url':'x','synthetic':False}]
        source=self.base/'inputs.jsonl'; source.write_text(''.join(json.dumps(x)+'\n' for x in rows)); status=import_corpus(source,self.run)
        self.assertEqual(1,status['eligible_deduplicated_documents']); self.assertEqual(4,status['excluded_documents']); self.assertEqual(2,status['version_aliases'])

    # Keep one completed pair partial and require a revision reason for changed scores.
    def test_partial_state_and_append_only_revision(self):
        self.prepare()
        with (self.run/'annotation_public'/'ratings_A.csv').open(encoding='utf-8-sig') as stream: template=list(csv.DictReader(stream))
        row=template[0]
        fields=['pool_version','blind_id','reviewer_id','relevance_0_1_2','unsure','rationale','rated_at','revision','change_reason']; source=self.base/'rating.csv'
        def save(score,revision,reason=''):
            with source.open('w',encoding='utf-8',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=fields); writer.writeheader(); writer.writerow({'pool_version':row['pool_version'],'blind_id':row['blind_id'],'reviewer_id':'A','relevance_0_1_2':score,'unsure':'false','rationale':'source supports decision','rated_at':'2026-09-17','revision':revision,'change_reason':reason})
        save(2,1); import_annotations(source,self.run); self.assertEqual('partially_labeled',coverage_status(self.run)['annotation_status'])
        save(1,1)
        with self.assertRaises(ValueError): import_annotations(source,self.run)
        save(1,2,'corrected transcription'); result=import_annotations(source,self.run); self.assertEqual(1,result['appended']); self.assertEqual(2,len(read_jsonl(self.run/'private'/'ratings_history.jsonl')))

    # N=10 with only 19/20 submissions cannot become complete.
    def test_nineteen_of_twenty_is_partial(self):
        (self.run/'annotation_public').mkdir(exist_ok=True); (self.run/'private').mkdir(exist_ok=True)
        write=lambda path,rows: path.write_text('blind_id\n'+'\n'.join(rows)+'\n',encoding='utf-8')
        ids=[f'b{i}' for i in range(10)]; write(self.run/'annotation_public'/'candidates.csv',ids); (self.run/'private'/'reviewers.json').write_text(json.dumps({'reviewers':[{'reviewer_id':'A','role':'reviewer'},{'reviewer_id':'B','role':'reviewer'}]}))
        history=[{'blind_id':blind,'reviewer_id':reviewer,'score':1,'unsure':False} for blind in ids for reviewer in ('A','B')][:-1]; (self.run/'private'/'ratings_history.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in history))
        state=coverage_status(self.run); self.assertEqual(19,state['submitted_ratings']); self.assertEqual('partially_labeled',state['annotation_status'])

    # Report null kappa when both fixed reviewers use one constant category.
    def test_constant_ratings_have_null_kappa(self):
        result=agreement_metrics([(1,1),(1,1)]); self.assertIsNone(result['linear_weighted_kappa']); self.assertEqual('constant_ratings',result['kappa_null_reason'])


if __name__=='__main__': unittest.main()
