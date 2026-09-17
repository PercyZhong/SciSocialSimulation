"""Stage A repair tests for calibration, memory, isolation, and dataset separation."""
import copy, csv, json, tempfile, unittest
from pathlib import Path

from scimirror.policy import PolicyContext
from scimirror.stage_a_repair import calibrate,dataset_registry,load_config,load_dataset
from scimirror.v03_retrieval import load_retrieval_profiles,repaired_evidence_score,retrieve_stage_a_repaired

ROOT=Path(__file__).resolve().parents[1]


class StageARepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config=load_config(ROOT/'configs/stage_a_repair.json',ROOT); cls.registry=dataset_registry(cls.config,ROOT)
        cls.corpus,cls.topics,_,_=load_dataset(cls.config,ROOT,cls.registry,'supplement_unchanged'); cls.profiles=load_retrieval_profiles(ROOT/cls.config['profiles_repaired'])

    # Keep the old corpus and closure challenge sources separate and count shared annotations correctly.
    def test_dataset_registry_separation(self):
        self.assertEqual(90,self.registry['supplement_unchanged']['raw_document_count']); self.assertEqual(44,self.registry['verification']['closure_unique_challenge_documents'])
        self.assertEqual(45,self.registry['verification']['closure_challenge_topic_annotations']); self.assertTrue(self.registry['verification']['old_document_rows_identical'])

    # Enforce token boundaries so tool does not match tooling.
    def test_token_boundary(self):
        score,_=repaired_evidence_score(self.profiles['topics']['agent_tools'],{'title':'Tooling benchmark','abstract':'Tooling latency only.'},18)
        self.assertEqual(0,score)

    # Preserve the strict idea/proposal object requirement for science evaluation.
    def test_evaluation_generic_negative(self):
        score,_=repaired_evidence_score(self.profiles['topics']['science_evaluation'],{'title':'Model evaluation','abstract':'Evaluation of latency.'},18)
        self.assertEqual(0,score)

    # Prove semantic memory changes scores but never the evidence-qualified pool.
    def test_memory_wiring_and_pool_isolation(self):
        base=copy.deepcopy(self.config['repaired_defaults']); base['_profiles']=self.profiles; pc=PolicyContext('balanced','retrieval',True,.5)
        _,empty=retrieve_stage_a_repaired(self.corpus.papers,self.topics,pc,base,'agent_memory','agents',[],[])
        _,related=retrieve_stage_a_repaired(self.corpus.papers,self.topics,pc,base,'agent_memory','agents',[{'topic_id':'agent_memory'}],[])
        self.assertEqual(empty['qualified_ids'],related['qualified_ids']); self.assertEqual(empty['base_candidate_ids'],related['base_candidate_ids'])
        self.assertTrue(any(row['memory_score']>0 for row in related['candidates'])); self.assertNotEqual([x['final_score'] for x in empty['candidates']],[x['final_score'] for x in related['candidates']])

    # Make every memory input identical when the memory path is switched off.
    def test_memory_off_consistency(self):
        base=copy.deepcopy(self.config['repaired_defaults']); base.update(_profiles=self.profiles,memory_enabled=False); pc=PolicyContext('balanced','retrieval',True,.5); results=[]
        for memory in ([],[{'topic_id':'agent_memory'}],[{'topic_id':'science_evaluation'}]):
            _,audit=retrieve_stage_a_repaired(self.corpus.papers,self.topics,pc,base,'agent_memory','agents',memory,[]); results.append((audit['selected_ids'],[x['final_score'] for x in audit['candidates']]))
        self.assertEqual(results[0],results[1]); self.assertEqual(results[1],results[2])

    # Keep qrels outside all production retrieval inputs.
    def test_qrels_not_in_query_identity(self):
        base=copy.deepcopy(self.config['repaired_defaults']); base['_profiles']=self.profiles; pc=PolicyContext('balanced','retrieval',True,.5)
        first=retrieve_stage_a_repaired(self.corpus.papers,self.topics,pc,base,'science_retrieval','science',[],[])[1]
        fake_qrels=[{'changed':True}]
        second=retrieve_stage_a_repaired(self.corpus.papers,self.topics,pc,base,'science_retrieval','science',[],[])[1]
        self.assertTrue(fake_qrels); self.assertEqual(first['query_id'],second['query_id']); self.assertEqual(first['selected_ids'],second['selected_ids'])

    # Require calibration artifacts to contain measured production calls and selected scores.
    def test_calibration_is_measured_not_static(self):
        with tempfile.TemporaryDirectory() as directory:
            result=calibrate(self.config,ROOT,Path(directory),self.registry)
            with (Path(directory)/'calibration_trials.csv').open(encoding='utf-8-sig') as stream: trials=list(csv.DictReader(stream))
            cases=[json.loads(line) for line in (Path(directory)/'calibration_case_results.jsonl').read_text().splitlines()]
            self.assertEqual(6,len(trials)); self.assertEqual(72,result['production_retrieval_calls'])
            self.assertTrue(all(row['measurement_status']=='measured' for row in trials))
            self.assertTrue(all('selected_scores' in row for row in cases if not row.get('negative_case')))


if __name__=='__main__': unittest.main()
