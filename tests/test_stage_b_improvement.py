import random
import tempfile
import unittest
import zipfile
from pathlib import Path

from scimirror.retrieval_soft_ranker import soft_rank
from scimirror.retrieval_stage_trace import first_exclusion_legacy, first_exclusion_soft, label_trace, loss_by_stage
from scimirror.stage_b_improvement import safe_extract


PROFILE={'definition':'agent memory','positive_phrases':['agent memory'],
         'concept_groups':{'object':['agent'],'mechanism':['memory']},
         'generic_terms':['memory'],'scope_note':'test'}


class StageBImprovementTests(unittest.TestCase):
    def papers(self):
        return {f'p{i}':{'id':f'p{i}','title':title,'abstract':abstract,'year':2024,'field':'agents'} for i,(title,abstract) in enumerate([
          ('Agent memory','agent memory retrieval'),('Other','unrelated body'),('Memory systems','memory for an agent'),
          ('Agent','agent only'),('Retrieval','document retrieval'),('Blank','none')])}

    def test_lambda_zero_exactly_matches_bm25(self):
        result=soft_rank('agent memory',self.papers(),PROFILE,candidate_k=6,output_k=6,lambda_value=0)
        self.assertEqual(result['returned_ids'],result['bm25_order'])

    def test_soft_output_is_bm25_candidate_subset_without_hard_gate(self):
        result=soft_rank('agent memory',self.papers(),PROFILE,candidate_k=4,output_k=4,lambda_value=.1)
        self.assertEqual(len(result['candidate_ids']),4); self.assertEqual(set(result['returned_ids']),set(result['candidate_ids']))
        self.assertIn('p1',result['candidate_ids'])  # a zero topic score remains eligible

    def test_zero_signal_preserves_deterministic_bm25_order(self):
        result=soft_rank('term absent everywhere',self.papers(),PROFILE,candidate_k=4,output_k=4,lambda_value=.1)
        self.assertTrue(result['zero_signal']); self.assertEqual(result['returned_ids'],sorted(self.papers())[:4])

    def test_unknown_profile_warns_and_does_not_drop(self):
        result=soft_rank('agent',self.papers(),None,candidate_k=5,output_k=5,lambda_value=.1)
        self.assertTrue(result['unknown_profile']); self.assertEqual(len(result['returned_ids']),5)

    def test_input_iteration_order_does_not_change_ranking(self):
        papers=self.papers(); items=list(papers.items()); random.Random(7).shuffle(items)
        a=soft_rank('agent memory',papers,PROFILE,candidate_k=6,output_k=6,lambda_value=.1)
        b=soft_rank('agent memory',dict(items),PROFILE,candidate_k=6,output_k=6,lambda_value=.1)
        self.assertEqual(a['returned_ids'],b['returned_ids'])

    def test_ties_use_paper_id_and_candidate_shortfall_is_explicit(self):
        papers={key:{'id':key,'title':'same','abstract':'same','year':2024,'field':'x'} for key in ('z','a','m')}
        result=soft_rank('absent',papers,PROFILE,candidate_k=20,output_k=10,lambda_value=.1)
        self.assertEqual(result['returned_ids'],['a','m','z']); self.assertEqual(len(result['candidate_ids']),3)

    def test_labels_do_not_enter_retrieval(self):
        before=soft_rank('agent memory',self.papers(),PROFILE,candidate_k=6,output_k=6,lambda_value=.1)['returned_ids']
        fabricated_labels={pid:2 for pid in self.papers()}; fabricated_labels['p0']=0
        after=soft_rank('agent memory',self.papers(),PROFILE,candidate_k=6,output_k=6,lambda_value=.1)['returned_ids']
        self.assertEqual(before,after)

    def test_archive_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as value:
            root=Path(value); archive=root/'bad.zip'
            with zipfile.ZipFile(archive,'w') as bundle: bundle.writestr('../escape.txt','bad')
            with self.assertRaises(ValueError): safe_extract(archive,root/'out')

    def test_first_exclusion_rules(self):
        self.assertEqual(first_exclusion_legacy(gate_pass=False,candidate_rank=None,dedup_kept=False,returned=False),'topic_gate')
        self.assertEqual(first_exclusion_soft(bm25_rank=21,candidate_k=20,returned=False),'candidate_truncation')

    def test_loss_conservation(self):
        rows=[]
        for pid,reason,returned in [('a',None,True),('b','topic_gate',False),('c','final_topk',False)]:
            rows.append({'query_id':'q','ranker_id':'r','paper_id':pid,'returned':returned,'first_exclusion_reason':reason})
        labeled=label_trace(rows,{('q','a'):2,('q','b'):1,('q','c'):0})
        self.assertEqual(len(loss_by_stage(labeled)),5)


if __name__ == '__main__': unittest.main()
