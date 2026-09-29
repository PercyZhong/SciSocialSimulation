import json
import tempfile
import unittest
from pathlib import Path

from scimirror.decision_backend import DecisionBackend, OpenAICompatibleTransport
from scimirror.decision_schema import validate_decision
from scimirror.model_registry import load_registry
from scimirror.stage_c_frozen import _snapshot, build_schedule, run_c0
from scimirror.stage_c_delivery import finalize, replay_validate
from scimirror.stage_c_live import apply_model_decision
from scimirror.usage_ledger import UsageLedger


ROOT=Path(__file__).resolve().parents[1]


class StageCTests(unittest.TestCase):
    def test_registry_and_fixed_models(self):
        registry=load_registry(ROOT/"configs"/"models.stage_c.example.json")
        self.assertEqual([m["model_key"] for m in registry["models"]],["deepseek_flash","qwen_flash_snapshot"])
        self.assertEqual(registry["models"][0]["provider_options"]["thinking"]["type"],"disabled")
        self.assertIs(registry["models"][1]["provider_options"]["enable_thinking"],False)

    def test_schedule_is_exact_and_balanced(self):
        config=json.loads((ROOT/"configs"/"stage_c_mock.json").read_text(encoding="utf-8"))
        rows=build_schedule(config)
        self.assertEqual(len(rows),432)
        self.assertEqual(len({tuple(r.items()) for r in rows}),432)

    def test_snapshot_world_and_legal_candidates_are_policy_independent(self):
        closed,closed_actions,closed_visible=_snapshot(42,"invitation","closed",["p1","p2","p3"])
        opened,open_actions,open_visible=_snapshot(42,"invitation","open",["p1","p2","p3"])
        self.assertEqual(len(closed["agents"]),20); self.assertEqual(closed["agents"],opened["agents"])
        self.assertEqual(len({a["action_id"] for a in open_actions}),len(open_actions))
        self.assertFalse(any(a.get("candidate_field_relation")=="cross" for a in closed_actions))
        self.assertTrue(any(a.get("candidate_field_relation")=="cross" for a in open_actions))
        self.assertEqual(closed_visible,open_visible)

    def test_model_decision_enters_state_without_rule_override(self):
        decision={"validated_payload":{"action_id":"a2","reason_summary":"ok","evidence_ids":[]}}
        for stage in ("topic_selection","retrieval_query_selection","invitation","project_exit"):
            state=apply_model_decision(stage,{},decision,[{"action_id":"a1"},{"action_id":"a2"}])
            self.assertEqual(state[f"selected_{stage}"],"a2")
            self.assertEqual(state["decision_events"][0]["decision_source"],"llm")

    def test_invalid_output_has_no_mock_fallback(self):
        with self.assertRaises(ValueError):
            validate_decision({"action_id":"bad","reason_summary":"x","evidence_ids":[]},[{"action_id":"ok"}],[])
        with self.assertRaises(ValueError):
            validate_decision({"action_id":"ok","reason_summary":"x","evidence_ids":["hidden"]},[{"action_id":"ok"}],[])

    def test_cache_isolated_by_policy_model_and_draw(self):
        model=load_registry(ROOT/"configs"/"models.stage_c.example.json")["models"][0]
        limits={"max_logical_calls":4,"max_http_attempts":0,"max_input_tokens":10,"max_output_tokens":10,"max_total_cost_by_currency":{}}
        with tempfile.TemporaryDirectory() as temp:
            ledger=UsageLedger(limits); backend=DecisionBackend(model,"mock",temp,ledger)
            actions=[{"action_id":"a","novelty":1,"recognition":0,"evidence_ids":[]}]
            for draw in (1,2): backend.decide("topic_selection",{"policy":"novelty"},actions,{"draw":draw},[])
            self.assertEqual(len(list(Path(temp).glob("*.json"))),2)
            calls=ledger.logical_calls
            replay=backend.decide("topic_selection",{"policy":"novelty"},actions,{"draw":2},[])
            self.assertTrue(replay["cache_hit"]); self.assertEqual(ledger.logical_calls,calls)

    def test_failed_attempt_counts_against_budget(self):
        limits={"max_logical_calls":1,"max_http_attempts":1,"max_input_tokens":10,"max_output_tokens":10,"max_total_cost_by_currency":{"USD":1}}
        ledger=UsageLedger(limits); ledger.reserve(1,1,"USD",.1,http=True)
        with self.assertRaises(RuntimeError): ledger.reserve(1,1,"USD",.1,http=True)

    def test_probe_requests_json_object_and_reports_invalid_json_safely(self):
        model=load_registry(ROOT/"configs"/"models.stage_c.example.json")["models"][0]
        limits={"max_logical_calls":4,"max_http_attempts":4,"max_input_tokens":1000,"max_output_tokens":1000,
                "max_total_cost_by_currency":{"USD":1}}

        class FakeProbe(OpenAICompatibleTransport):
            def __init__(self, ledger, raw):
                super().__init__(ledger); self.raw=raw; self.request=None
            def _request(self, model, request, input_reserve=2000, output_reserve=None):
                self.request=request
                return {"raw_response":self.raw,"usage":{"prompt_tokens":8,"completion_tokens":4},
                        "provider_request_id":"probe-id","returned_model_id":"probe-model","finish_reason":"stop"}

        valid=FakeProbe(UsageLedger(limits),'{"ok":true}')
        result=valid.probe(model)
        self.assertEqual(valid.request["response_format"],{"type":"json_object"})
        self.assertEqual(result["status"],"passed")

        invalid=FakeProbe(UsageLedger(limits),"not strict json")
        result=invalid.probe(model)
        self.assertEqual(result["status"],"failed")
        self.assertEqual(result["error_category"],"invalid_json")
        self.assertNotIn("raw_response",result)

    def test_one_format_repair_is_budgeted_and_preserves_first_failure(self):
        class RepairingTransport:
            def __call__(self,*args):
                return {"raw_response":"not-json","usage":{"input_tokens":1,"output_tokens":1},"returned_model_id":"test"}
            def repair(self,*args):
                return {"raw_response":json.dumps({"action_id":"a","reason_summary":"fixed","evidence_ids":[]}),
                        "usage":{"input_tokens":1,"output_tokens":1},"returned_model_id":"test"}
        model=load_registry(ROOT/"configs"/"models.stage_c.example.json")["models"][0]
        limits={"max_logical_calls":2,"max_http_attempts":0,"max_input_tokens":10,"max_output_tokens":10,"max_total_cost_by_currency":{}}
        with tempfile.TemporaryDirectory() as temp:
            ledger=UsageLedger(limits); backend=DecisionBackend(model,"live",temp,ledger,RepairingTransport())
            result=backend.decide("topic_selection",{"policy":"balanced"},[{"action_id":"a"}],{"draw":1},[])
            self.assertTrue(result["repair_attempted"]); self.assertFalse(result["first_pass"])
            self.assertEqual(result["initial_invalid_raw_response"],"not-json")
            self.assertEqual(ledger.logical_calls,2)

    def test_fake_transport_failure_and_unknown_usage_paths(self):
        model=load_registry(ROOT/"configs"/"models.stage_c.example.json")["models"][0]
        limits={"max_logical_calls":4,"max_http_attempts":0,"max_input_tokens":10,"max_output_tokens":10,"max_total_cost_by_currency":{}}
        class Fake:
            def __init__(self,result=None,error=None): self.result,self.error=result,error
            def __call__(self,*args):
                if self.error: raise self.error
                return self.result
        for error in (TimeoutError("timeout"),RuntimeError("refusal"),RuntimeError("truncated")):
            with self.subTest(error=str(error)), tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(type(error)):
                    DecisionBackend(model,"live",temp,UsageLedger(limits),Fake(error=error)).decide(
                        "topic_selection",{"policy":"balanced"},[{"action_id":"a"}],{"draw":1},[])
        with tempfile.TemporaryDirectory() as temp:
            ledger=UsageLedger(limits)
            result={"raw_response":json.dumps({"action_id":"a","reason_summary":"ok","evidence_ids":[]}),
                    "usage":None,"returned_model_id":"test"}
            DecisionBackend(model,"live",temp,ledger,Fake(result=result)).decide(
                "topic_selection",{"policy":"balanced"},[{"action_id":"a"}],{"draw":1},[])
            self.assertEqual(ledger.usage_unknown,1)

    def test_complete_mock_c0_and_offline_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/"run"; result=run_c0(ROOT/"configs"/"stage_c_mock.json",out,"mock")
            self.assertEqual(result["decisions"],432); self.assertEqual(result["errors"],0)
            rows=(out/"DECISIONS.csv").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows),433)
            self.assertTrue((out/"CHECKPOINTS.json").is_file())
            self.assertTrue((out/"PROTOCOL_METRICS.json").is_file())

    def test_connection_pilot_matrix_replays_without_432_assumption(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/"pilot"
            result=run_c0(ROOT/"configs"/"stage_c_pilot.example.json",out,"mock")
            self.assertEqual(result["decisions"],8)
            replay=replay_validate(out)
            self.assertEqual(replay["status"],"passed")
            self.assertEqual(replay["expected_rows"],8)
            plan=json.loads((out/"PLAN_FROZEN.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["matrix"],{"logical_calls":8,"models":2,"seeds":1,"policies":1,
                                             "networks":1,"stages":4,"draws":1})

    def test_final_zip_replays_from_extracted_source(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/"run"; run_c0(ROOT/"configs"/"stage_c_mock.json",out,"mock")
            result=finalize(out,ROOT)
            self.assertEqual(result["zip_reproduction"]["status"],"passed")
            self.assertTrue(result["zip_reproduction"]["module_inside_extracted_tree"])


if __name__=="__main__": unittest.main()
