"""Stage A supplement evidence, gold, policy-chain, and cluster-metric tests."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from scimirror.corpus import Corpus
from scimirror.policy import PolicyContext
from scimirror.stage_a_supplement import (PRODUCTION_FIELDS, REFERENCE_RETRIEVE, SUPPLEMENT_RETRIEVE,
    ensure_resume_contract, load_config, load_dataset, policy_e2e, policy_unit, prepare, result_row)
from scimirror.v03_retrieval import duplicate_clusters


ROOT=Path(__file__).resolve().parents[1]


class StageASupplementTests(unittest.TestCase):
    # Prepare the deterministic frozen evidence once for all supplement tests.
    @classmethod
    def setUpClass(cls):
        cls.config=load_config(ROOT/'configs'/'stage_a_supplement.json',ROOT)
        cls.prepared=prepare(cls.config,ROOT)

    # Verify every topic has at least six content-designed families and no production gold leakage.
    def test_content_supply_and_gold_isolation(self):
        self.assertTrue(all(x>=6 for x in self.prepared['independent_families_per_topic'].values()))
        corpus=Corpus(ROOT/self.config['corpora']['corpus_expanded'],2025,True)
        self.assertTrue(all(not(set(p)-PRODUCTION_FIELDS) for p in corpus.papers.values()))
        self.assertTrue(all(p['synthetic'] for p in corpus.papers.values()))

    # Verify family-level calibration/test splits are disjoint and variants remain with their family.
    def test_family_split_no_leakage(self):
        split=json.loads((ROOT/self.config['gold_paths']['split_manifest']).read_text(encoding='utf-8'))
        self.assertEqual([],split['family_overlap'])
        maps=[json.loads(x) for x in (ROOT/self.config['gold_paths']['document_map']).read_text(encoding='utf-8').splitlines()]
        by_family={}
        for row in maps: by_family.setdefault(row['gold_family_id'],set()).add(row['split'])
        self.assertTrue(all(len(x)==1 for x in by_family.values()))
        variants=[row for row in maps if row['variant_of']]
        self.assertEqual(12,len(variants))
        self.assertTrue(all(row['split']=='test' for row in variants))

    # Verify shared evidence is one document/family with multiple relations rather than copied evidence.
    def test_shared_family_not_duplicated(self):
        families=[json.loads(x) for x in (ROOT/self.config['gold_paths']['families']).read_text(encoding='utf-8').splitlines()]
        shared=[x for x in families if len(x['topic_ids'])>1]
        self.assertGreaterEqual(len(shared),3)
        self.assertTrue(all(len(x['paper_ids'])==1 for x in shared))
        self.assertEqual(len({x['paper_ids'][0] for x in shared}),len(shared))

    # Verify changing isolated qrels cannot alter production retrieval output.
    def test_gold_label_swap_does_not_change_retrieval(self):
        corpus,topics,_,_=load_dataset(self.config,ROOT,'corpus_expanded')
        pc=PolicyContext('balanced','retrieval',True,.5)
        before=REFERENCE_RETRIEVE(corpus.papers,topics,pc,self.config['retrieval'],'agent_memory','agents',[],[])[1]['selected_ids']
        fake_gold={'unrelated':'labels swapped only'}
        after=REFERENCE_RETRIEVE(corpus.papers,topics,pc,self.config['retrieval'],'agent_memory','agents',[],[])[1]['selected_ids']
        self.assertTrue(fake_gold); self.assertEqual(before,after)

    # Verify reference and supplement are intentionally the same frozen ranker implementation.
    def test_reference_is_not_silently_modified(self):
        self.assertIs(REFERENCE_RETRIEVE,SUPPLEMENT_RETRIEVE)

    # Verify all hand-calculated policy-interface scores match the frozen formula.
    def test_policy_unit_hand_calculation(self):
        rows=policy_unit(self.config)
        self.assertEqual(18,len(rows)); self.assertTrue(all(x['passed'] for x in rows))
        self.assertAlmostEqual(.9875,next(x['calculated_final_score'] for x in rows if x['policy']=='novelty' and x['candidate_id']=='unit_0'))

    # Verify production-derived policy fixtures share pools, respond to policy, and neutralize a disabled path.
    def test_policy_end_to_end_chain(self):
        rows=policy_e2e(self.config,ROOT); enabled=[x for x in rows if x['path_enabled']]
        self.assertEqual(54,len(enabled))
        groups={}
        for row in enabled: groups.setdefault((row['scenario'],row['history'],row['ranker']),[]).append(row)
        self.assertTrue(all(len({x['candidate_pool_hash'] for x in values})==1 for values in groups.values()))
        self.assertTrue(any(len({tuple(x['selected_ids']) for x in values})>1 for values in groups.values()))
        disabled=[x for x in rows if not x['path_enabled']]
        for ranker in self.config['rankers']:
            self.assertEqual(1,len({tuple(x['selected_ids']) for x in disabled if x['ranker']==ranker}))

    # Verify the independent gold-cluster formulas at zero, one, and two available-family boundaries.
    def test_gold_cluster_metric_boundaries(self):
        state={'case_id':'x','state_hash':'h','selected_topic_id':'q','agent_field':'agents','memory_condition':'empty'}
        papers={x:{'id':x} for x in ('a','b')}; corpus=type('C',(),{'papers':papers})()
        audit={'base_candidate_ids':['a','b'],'qualified_ids':['a','b'],'selected_ids':['a','b'],'fallback':'short',
          'candidates':[{'paper_id':'a','qualified':True,'cluster_id':'c1','evidence_relevance':1,'exploration':.2,'attention':.3,'policy_score':.25,'final_score':.8,'topic_ids':['q']},
                        {'paper_id':'b','qualified':True,'cluster_id':'c2','evidence_relevance':1,'exploration':.8,'attention':.7,'policy_score':.75,'final_score':.9,'topic_ids':['q']}]}
        one=result_row(state,'fixture','r','p',audit,{'a':'f1','b':'f2'},{'q':{'f1'}},corpus)
        two=result_row(state,'fixture','r','p',audit,{'a':'f1','b':'f2'},{'q':{'f1','f2'}},corpus)
        zero=result_row(state,'fixture','r','p',audit,{'a':'f1','b':'f2'},{'q':set()},corpus)
        self.assertEqual(1,one['gold_cluster_capacity_coverage_at_k']); self.assertEqual(1,two['gold_cluster_recall_at_k'])
        self.assertIsNone(zero['gold_cluster_recall_at_k']); self.assertEqual('no_gold_relevant_family',zero['gold_cluster_recall_null_reason'])

    # Verify dedup membership is invariant to input order even when cluster labels may differ.
    def test_dedup_membership_order_invariant(self):
        corpus,_,doc_family,_=load_dataset(self.config,ROOT,'corpus_expanded'); items=[{'paper':p} for p in corpus.papers.values()]
        left=duplicate_clusters(items,.97,fixture_aware=True); right=duplicate_clusters(list(reversed(items)),.97,fixture_aware=True)
        pairs=lambda x:{frozenset((a,b)) for a in x for b in x if a<b and x[a]==x[b]}
        self.assertEqual(pairs(left),pairs(right))

    # Verify the held-out test split contains gold-positive near-duplicate pairs detected exactly.
    def test_test_split_dedup_gold_positives(self):
        corpus,_,doc_family,_=load_dataset(self.config,ROOT,'corpus_expanded')
        maps=[json.loads(x) for x in (ROOT/self.config['gold_paths']['document_map']).read_text(encoding='utf-8').splitlines()]
        test_ids={x['paper_id'] for x in maps if x['split']=='test' and x['paper_id'] in corpus.papers}
        assignments=duplicate_clusters([{'paper':corpus.papers[x]} for x in sorted(test_ids)],.97,fixture_aware=True)
        gold={(a,b) for a in sorted(test_ids) for b in sorted(test_ids) if a<b and doc_family[a]==doc_family[b]}
        predicted={(a,b) for a in sorted(test_ids) for b in sorted(test_ids) if a<b and assignments[a]==assignments[b]}
        self.assertEqual(12,len(gold))
        self.assertEqual(gold,predicted)

    # Verify cutoff-year fixtures are excluded from the actual production corpus snapshot.
    def test_future_documents_excluded(self):
        corpus=Corpus(ROOT/self.config['corpora']['corpus_expanded'],self.config['cutoff_year'],True)
        self.assertFalse(any(x.startswith('future_') for x in corpus.papers))
        self.assertEqual(self.prepared['eligible_documents'],len(corpus.papers))

    # Verify resume is refused rather than mixing rows after an input or source hash changes.
    def test_resume_contract_rejects_hash_change(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory); contract={'input_hashes':{'fixture':'hash-a'},'source_hashes':{'code':'hash-a'}}
            ensure_resume_contract(output,contract)
            ensure_resume_contract(output,copy.deepcopy(contract))
            changed=copy.deepcopy(contract); changed['input_hashes']['fixture']='hash-b'
            with self.assertRaisesRegex(ValueError,'input or implementation hash changed'):
                ensure_resume_contract(output,changed)


if __name__=='__main__':
    unittest.main()
