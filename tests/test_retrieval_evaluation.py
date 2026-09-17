"""Hand-calculated counterexamples for evaluator version 2."""
import unittest

from scimirror.retrieval_evaluation import build_gold, ranked_metrics, regression_checks


class RetrievalEvaluationTests(unittest.TestCase):
    # Detect one related-memory regression without overwriting empty/unrelated strata.
    def test_regression_uses_complete_memory_key(self):
        rows=[]
        for memory in ('empty','related','unrelated'):
            rows.append({'dataset_id':'supplement_unchanged','ranker':'stage_a_fixed','topic_id':'t','memory_condition':memory,'mean_capacity_coverage':1.0,'mean_document_precision':1.0,'n':9})
            rows.append({'dataset_id':'supplement_unchanged','ranker':'stage_a_repaired_v1','topic_id':'t','memory_condition':memory,'mean_capacity_coverage':0.5 if memory=='related' else 1.0,'mean_document_precision':1.0,'n':9})
        failures=regression_checks(rows,.05,.05)
        self.assertEqual(1,len(failures)); self.assertEqual('related',failures[0]['memory_condition'])

    # Reject duplicate summary keys and expose missing controls as incomplete.
    def test_regression_duplicate_and_missing(self):
        row={'dataset_id':'supplement_unchanged','ranker':'stage_a_fixed','topic_id':'t','memory_condition':'empty','mean_capacity_coverage':1.0,'mean_document_precision':1.0,'n':9}
        with self.assertRaises(ValueError): regression_checks([row,row],.05,.05)
        self.assertEqual('incomplete',regression_checks([row],.05,.05)[0]['status'])

    # Preserve unknown labels and report bounds instead of imputing zero.
    def test_unjudged_precision_bounds(self):
        result=ranked_metrics(['a','b','c'],3,{'a':2,'b':None,'c':0},{'a':2,'b':None,'c':0})
        self.assertIsNone(result['precision_at_k']); self.assertAlmostEqual(1/3,result['precision_at_k_lower']); self.assertAlmostEqual(2/3,result['precision_at_k_upper']); self.assertEqual(.5,result['precision_judged'])

    # Treat missing returned slots as shortfall rather than unknown labels.
    def test_shortfall_precision(self):
        result=ranked_metrics(['a','b'],3,{'a':2,'b':0},{'a':2,'b':0})
        self.assertAlmostEqual(1/3,result['precision_at_k']); self.assertEqual(.5,result['precision_returned']); self.assertEqual(1,result['shortfall'])

    # Match the hand-calculated graded nDCG example.
    def test_ndcg_hand_calculation(self):
        result=ranked_metrics(['a','b','c'],3,{'a':2,'b':0,'c':1},{'a':2,'b':0,'c':1})
        self.assertEqual(3.5,result['dcg_partial']); self.assertAlmostEqual(.9639404333166532,result['ndcg_at_k'])

    # Empty output is not perfect coverage and all-zero supply has null recall/nDCG.
    def test_empty_and_zero_gold(self):
        result=ranked_metrics([],3,{}, {'a':0,'b':0})
        self.assertEqual(0,result['precision_at_k']); self.assertIsNone(result['precision_returned']); self.assertIsNone(result['recall_at_k']); self.assertEqual('no_relevant_supply',result['ndcg_null_reason'])

    # Reject invalid grades, unknown families, and conflicting qrels.
    def test_gold_validation(self):
        mapping=[{'paper_id':'p','gold_family_id':'f'}]
        with self.assertRaises(ValueError): build_gold(mapping,[{'query_id':'q','gold_family_id':'f','query_relevance_grade':3}],{'p'})
        with self.assertRaises(ValueError): build_gold(mapping,[{'query_id':'q','gold_family_id':'x','query_relevance_grade':1}],{'p'})


if __name__=='__main__': unittest.main()
