import math
import unittest

from scimirror.retrieval_soft_ranker import soft_rank
from scimirror.stage_b_candidate_diagnostic import _dcg


PROFILE={"definition":"agent memory","positive_phrases":["agent memory"],
         "concept_groups":{"object":["agent"],"mechanism":["memory"]},
         "generic_terms":["memory"],"scope_note":"test"}


class StageBCandidateDiagnosticTests(unittest.TestCase):
    def papers(self,n=36):
        return {f"p{i:02d}":{"id":f"p{i:02d}","title":"agent memory" if i%7==0 else f"paper {i}",
                "abstract":"agent memory retrieval" if i%7==0 else "unrelated text", "year":2024,"field":"agents"}
                for i in range(n)}

    def test_full_bm25_prefix_matches_k20(self):
        papers=self.papers(); k20=soft_rank("agent memory",papers,PROFILE,candidate_k=20,output_k=10,lambda_value=.1)
        all_run=soft_rank("agent memory",papers,PROFILE,candidate_k=36,output_k=10,lambda_value=.1)
        self.assertEqual(k20["candidate_ids"],all_run["bm25_order"][:20])

    def test_candidate_sets_are_nested_and_all_covers_input(self):
        papers=self.papers(); a=soft_rank("agent memory",papers,PROFILE,candidate_k=10,output_k=10,lambda_value=.1)
        b=soft_rank("agent memory",papers,PROFILE,candidate_k=20,output_k=10,lambda_value=.1)
        c=soft_rank("agent memory",papers,PROFILE,candidate_k=36,output_k=10,lambda_value=.1)
        self.assertLessEqual(set(a["candidate_ids"]),set(b["candidate_ids"])); self.assertEqual(set(c["candidate_ids"]),set(papers))

    def test_fixed_denominator_is_exported_and_not_clipped(self):
        papers=self.papers(); result=soft_rank("agent memory",papers,PROFILE,candidate_k=36,output_k=10,lambda_value=.1,
            normalization_denominators={"bm25":.01,"topic":.01})
        self.assertEqual(result["normalization_source"],"fixed_external")
        self.assertGreater(max(row["topic_score_normalized"] for row in result["scores"].values()),1)

    def test_all_zero_and_short_corpus_are_deterministic(self):
        papers=self.papers(4); result=soft_rank("absent token",papers,PROFILE,candidate_k=20,output_k=10,lambda_value=.1)
        self.assertTrue(result["zero_signal"]); self.assertEqual(result["returned_ids"],sorted(papers))

    def test_dcg_contribution_outside_top5_is_zero(self):
        self.assertEqual(_dcg(2,None),0); self.assertEqual(_dcg(2,6),0)
        self.assertAlmostEqual(_dcg(2,1),3.0)

    def test_candidate_recall_cannot_decrease_for_nested_sets(self):
        grades={f"p{i:02d}":(2 if i in (0,15,30) else 0) for i in range(36)}
        order=[f"p{i:02d}" for i in range(36)]
        values=[sum(grades[p]>=1 for p in order[:k])/3 for k in (10,20,36)]
        self.assertEqual(values,sorted(values))


if __name__ == "__main__": unittest.main()
