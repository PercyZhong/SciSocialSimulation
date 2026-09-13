"""Stage A frozen retrieval, gate, memory, deduplication, and interface tests."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from scimirror.corpus import Corpus
from scimirror.policy import PolicyContext
from scimirror.stage_a import (build_frozen_states, engine_regression, file_hash,
                               frozen_state_hash, load_stage_a_config, targeted_probes)
from scimirror.topics import TopicModel
from scimirror.v03_retrieval import (build_stage_a_query, duplicate_clusters,
                                     retrieve_stage_a_fixed)


ROOT=Path(__file__).resolve().parents[1]


class StageATests(unittest.TestCase):
    # Load the frozen Stage A configuration and its unchanged local inputs.
    def setUp(self):
        self.config=load_stage_a_config(ROOT/'configs'/'stage_a_frozen_retrieval.json',ROOT)

    # Verify the declared 12 by 3 by 3 frozen-state design and treatment-independent hashes.
    def test_frozen_state_cardinality_and_hash(self):
        corpus=Corpus(ROOT/self.config['corpus'],self.config['cutoff_year'],True)
        topics=TopicModel(ROOT/self.config['topics'],corpus.papers,1.0)
        states=build_frozen_states(self.config,file_hash(ROOT/self.config['corpus']),
                                   file_hash(ROOT/self.config['topics']),topics)
        self.assertEqual(108,len(states)); self.assertEqual(108,len({s['state_hash'] for s in states}))
        self.assertTrue(all('policy' not in s and 'ranker' not in s for s in states))
        self.assertTrue(all(s['state_hash']==frozen_state_hash(s) for s in states))

    # Verify every explicitly specified A01-A15 behavioral probe passes.
    def test_targeted_probes(self):
        probes,_=targeted_probes(self.config)
        self.assertEqual([f'A{i:02d}' for i in range(1,16)],[p['probe_id'] for p in probes])
        self.assertEqual([],[(p['probe_id'],p['observed']) for p in probes if p['status']!='passed'])

    # Verify normalized semantic-memory identifiers expand to meaningful lexical evidence tokens.
    def test_memory_identifier_and_spaced_form_are_equivalent(self):
        corpus=Corpus(ROOT/self.config['corpus'],2025,True)
        topics=TopicModel(ROOT/self.config['topics'],corpus.papers,1.0)
        left=build_stage_a_query(topics,'agent_memory','agents',['agent_memory'],self.config['fixed_retrieval']['query_weights'])
        right=build_stage_a_query(topics,'agent_memory','agents',['agent memory'],self.config['fixed_retrieval']['query_weights'])
        self.assertEqual(left['components']['memory'],right['components']['memory'])
        self.assertIn('retrieval',left['components']['memory'])

    # Verify synthetic marker variants merge while a scientifically different method remains separate.
    def test_fixture_aware_dedup_preserves_meaningful_numbers(self):
        def item(pid,title,abstract):
            return {'paper':{'id':pid,'title':title,'abstract':abstract,'field':'agents','year':2020,'synthetic':True}}
        items=[item('a','Memory trial scenario 1','Variant 1 n 100'),
               item('b','Memory trial scenario 2','Variant 2 n 100'),
               item('c','Memory trial scenario 3','Variant 3 n 200')]
        clusters=duplicate_clusters(items,.97,fixture_aware=True)
        self.assertEqual(clusters['a'],clusters['b'])
        self.assertNotEqual(clusters['a'],clusters['c'])

    # Verify the actual production Corpus interface dispatches to the fixed ranker and accepts an empty result.
    def test_production_interface_regression(self):
        result=engine_regression(self.config,ROOT)
        self.assertTrue(result['passed']); self.assertEqual('stage_a_fixed',result['mode'])
        self.assertEqual(0,result['empty_selected_count'])

    # Verify the old v0.3 config remains explicitly bound to its historical ranker.
    def test_old_config_not_silently_changed(self):
        old=json.loads((ROOT/'configs'/'v03_mock_main.json').read_text(encoding='utf-8'))
        new=json.loads((ROOT/'configs'/'v03_mock_stage_a_fixed.json').read_text(encoding='utf-8'))
        self.assertEqual('relevance_gated',old['retrieval']['mode'])
        self.assertEqual('stage_a_fixed',new['retrieval']['mode'])


if __name__=='__main__':
    unittest.main()
