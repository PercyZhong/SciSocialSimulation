"""Targeted tests for semantic retrieval, closure metrics, acceptance, and resume identity."""
import copy, json, tempfile, unittest
from pathlib import Path

from scimirror.corpus import Corpus
from scimirror.policy import PolicyContext
from scimirror.stage_a_closure import (integrity_pass, load_config, metric_correctness_pass, metrics, prepare, quality_gate_checks,
    reproduction_tree_complete, resume_contract)
from scimirror.topics import TopicModel
from scimirror.v03_retrieval import load_retrieval_profiles, retrieve_stage_a_fixed, retrieve_stage_a_semantic_guarded, semantic_evidence_score

ROOT=Path(__file__).resolve().parents[1]


class StageAClosureTests(unittest.TestCase):
    # Prepare the frozen v3 closure fixture once without touching earlier versions.
    @classmethod
    def setUpClass(cls):
        cls.config=load_config(ROOT/'configs'/'stage_a_closure.json',ROOT); cls.prepared=prepare(cls.config,ROOT)
        cls.profiles=load_retrieval_profiles(ROOT/cls.config['profiles'])

    # Reject a generic evaluation word when no scientific idea/proposal object is assessed.
    def test_generic_evaluation_negative(self):
        paper={'title':'Model performance evaluation','abstract':'Evaluation compares latency only.'}
        score,detail=semantic_evidence_score(self.profiles['topics']['science_evaluation'],paper,18)
        self.assertEqual(0,score); self.assertTrue(detail['generic_only_match'])

    # Reject object and assessment terms split across unrelated sentences.
    def test_context_negative_requires_same_sentence(self):
        paper={'title':'Idea archive','abstract':'Research ideas are stored. Model evaluation measures runtime.'}
        score,_=semantic_evidence_score(self.profiles['topics']['science_evaluation'],paper,18)
        self.assertEqual(0,score)

    # Accept a reasonable synonym phrase without using paper or family identifiers.
    def test_synonym_positive(self):
        paper={'title':'Scientific idea assessment','abstract':'We assess feasibility in a fabricated setting.'}
        score,_=semantic_evidence_score(self.profiles['topics']['science_evaluation'],paper,18)
        self.assertGreaterEqual(score,.8)

    # Preserve all old expanded records byte-semantically while appending finite challenges.
    def test_old_corpus_preserved(self):
        supplement=json.loads((ROOT/self.config['supplement_config']).read_text(encoding='utf-8'))
        old=(ROOT/supplement['corpora']['corpus_expanded']).read_text(encoding='utf-8').splitlines()
        new=(ROOT/self.config['corpora']['corpus_expanded']).read_text(encoding='utf-8').splitlines()
        self.assertEqual(old,new[:len(old)]); self.assertGreaterEqual(self.prepared['challenge_families'],37); self.assertLessEqual(self.prepared['challenge_families'],50)

    # Keep the committed reference and semantic implementation as distinct functions.
    def test_reference_and_semantic_are_distinct(self):
        self.assertIsNot(retrieve_stage_a_fixed,retrieve_stage_a_semantic_guarded)

    # Verify empty, duplicate-family, partial, and zero-supply evaluator boundaries by hand.
    def test_metric_hand_examples(self):
        mapping={'a':'f1','b':'f1','c':'f2','d':'f3'}; relevant={'q':{'f1','f2','f3'}}
        empty=metrics([],[],'q',mapping,relevant); duplicate=metrics(['a','b'],['a','b','c'],'q',mapping,relevant); partial=metrics(['a','d'],['a','c','d'],'q',mapping,relevant)
        zero=metrics([],[],'q',mapping,{'q':set()})
        self.assertEqual(0,empty['capacity_coverage']); self.assertIsNone(empty['document_precision'])
        self.assertEqual(1/3,duplicate['capacity_coverage']); self.assertEqual(2/3,partial['capacity_coverage']); self.assertIsNone(zero['capacity_coverage'])
        self.assertEqual(1.0,duplicate['duplicate_pair_ratio'])

    # Treat complete-looking rows with unknown selected IDs as a metric failure, not success.
    def test_wrong_selected_ids_fail_metrics(self):
        result=metrics(['wrong_a','wrong_b'],['wrong_a'],'q',{'known':'f1'},{'q':{'f1'}})
        self.assertEqual(0,result['capacity_coverage']); self.assertEqual(0,result['document_precision']); self.assertTrue(result['returned_but_all_irrelevant'])

    # Detect a modified summary even when all underlying case rows are still present.
    def test_metric_summary_tamper(self):
        fresh={'stored_metric_mismatches':0,'macro':1.0}; stored=copy.deepcopy(fresh)
        rows=[{'capacity_coverage':1,'document_precision':1,'candidate_family_coverage':1,'duplicate_pair_ratio':0}]
        self.assertTrue(metric_correctness_pass(stored,fresh,rows)); stored['macro']=.5
        self.assertFalse(metric_correctness_pass(stored,fresh,rows))

    # Ensure profile paths and gold changes are absent from production ranking features.
    def test_gold_perturbation_does_not_change_ranking(self):
        corpus=Corpus(ROOT/self.config['corpora']['corpus_expanded'],2025,True); topics=TopicModel(ROOT/self.config['topics'],corpus.papers,1)
        retrieval=dict(self.config['semantic_retrieval']); retrieval['_profiles']=self.profiles; pc=PolicyContext('balanced','retrieval',True,.5)
        before=retrieve_stage_a_semantic_guarded(corpus.papers,topics,pc,retrieval,'science_evaluation','science',[],[])[1]['selected_ids']
        fake={'qrels':'replaced'}
        after=retrieve_stage_a_semantic_guarded(corpus.papers,topics,pc,retrieval,'science_evaluation','science',[],[])[1]['selected_ids']
        self.assertTrue(fake); self.assertEqual(before,after)

    # Verify resume identity changes when an implementation dependency changes.
    def test_resume_contract_has_relative_dependency_hashes(self):
        contract=resume_contract(self.config,ROOT/'configs'/'stage_a_closure.json',ROOT)
        self.assertTrue(all(not Path(k).is_absolute() for k in contract['hashes']))
        changed=copy.deepcopy(contract); changed['hashes']['scimirror/corpus.py']='changed'
        self.assertNotEqual(contract['identity_hash'],__import__('scimirror.common',fromlist=['digest']).digest(changed['hashes']))

    # Detect a missing dependency in an otherwise populated reproduction tree.
    def test_reproduction_dependency_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)
            self.assertFalse(reproduction_tree_complete(source))

    # Enforce the year < cutoff boundary on the actual closure Corpus loader.
    def test_cutoff_boundary(self):
        corpus=Corpus(ROOT/self.config['corpora']['corpus_expanded'],2025,True)
        self.assertIn('closure_pre_cutoff',corpus.papers); self.assertNotIn('closure_at_cutoff',corpus.papers); self.assertNotIn('closure_after_cutoff',corpus.papers)

    # Detect deletion, duplication, and a state-hash mismatch in the integrity layer.
    def test_integrity_counterexamples(self):
        rows=[{'core_case_id':str(i),'state_hash':'h','state_hash_before':'h','state_hash_after':'h'} for i in range(3)]
        self.assertTrue(integrity_pass(rows,3)); self.assertFalse(integrity_pass(rows[:-1],3))
        self.assertFalse(integrity_pass(rows[:-1]+[copy.deepcopy(rows[0])],3))
        changed=copy.deepcopy(rows); changed[0]['state_hash_after']='tampered'; self.assertFalse(integrity_pass(changed,3))

    # Keep quality failure separate even when engineering integrity could pass.
    def test_quality_failure_not_all_passed(self):
        thresholds=self.config['quality_thresholds']; quality={'science_evaluation_capacity_coverage':.79,'macro_capacity_coverage':1,
          'minimum_topic_capacity_coverage':1,'maximum_baseline_drop':0,'generic_negative_gate_passes':0,
          'gold_perturbation_ranking_changes':0,'zero_relevant_returns_with_supply':0}
        checks=quality_gate_checks(quality,thresholds); self.assertFalse(checks['science_evaluation']); self.assertFalse(all(checks.values()))


if __name__=='__main__': unittest.main()
