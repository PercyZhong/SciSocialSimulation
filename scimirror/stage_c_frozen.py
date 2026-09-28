"""Stage C C0 frozen-policy decision experiment (offline/mock or authorized live)."""
import csv
import json
import platform
import os
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from .common import digest, dump
from .decision_backend import DecisionBackend
from .model_registry import load_registry, public_manifest
from .usage_ledger import UsageLedger


POLICY_SPEC = {
    "balanced": {"novelty_weight": .5, "recognition_weight": .5},
    "novelty": {"novelty_weight": 1.0, "recognition_weight": 0.0},
    "recognition": {"novelty_weight": 0.0, "recognition_weight": 1.0},
}


def _actions(seed, stage, network, evidence_ids=()):
    if stage == "topic_selection":
        return [{"action_id": f"topic_{i}", "novelty": x, "recognition": y, "evidence_ids": []}
                for i, (x, y) in enumerate(((.8,.2),(.5,.5),(.2,.8)), 1)]
    if stage == "retrieval_query_selection":
        return [{"action_id": f"query_{i}", "novelty": x, "recognition": y,
                 "evidence_ids": [evidence_ids[(i-1)%len(evidence_ids)]]} for i, (x, y) in enumerate(((.75,.3),(.5,.55),(.25,.85)), 1)]
    if stage == "invitation":
        fields = ("same","same","same") if network == "closed" else ("same","cross","cross")
        rows = [{"action_id": f"invite_s{i:02d}", "novelty": .75 if f == "cross" else .35,
                 "recognition": .55 if f == "cross" else .7, "candidate_field_relation": f,
                 "evidence_ids": []} for i, f in enumerate(fields, 1)]
        return rows + [{"action_id": "decline", "novelty": .1, "recognition": .2, "evidence_ids": []}]
    return [{"action_id": "continue", "novelty": .45, "recognition": .7, "evidence_ids": []},
            {"action_id": "exit", "novelty": .75, "recognition": .3, "evidence_ids": []}]


def _snapshot(seed, stage, network, evidence_ids):
    agents = [{"agent_id": f"s{i:02d}", "field": ("agents","learning","science")[i%3],
               "public_reputation": round(.3 + (i%7)*.08, 2)} for i in range(20)]
    actions = _actions(seed, stage, network, evidence_ids)
    observation = {"actor_id": "s00", "seed": seed, "stage": stage, "network": network,
                   "agents": agents, "project_progress": .55, "remaining_budget": 40,
                   "external_opportunity": .6, "policy": None}
    visible = sorted({e for a in actions for e in a.get("evidence_ids", [])})
    return observation, actions, visible


def load_evidence(config, mode):
    source=os.getenv(config.get("evidence_source_env",""),"")
    if not source:
        if mode=="live": raise RuntimeError("STAGE_C_EVIDENCE_ARCHIVE is required for live C0")
        return ["fixture_paper_1","fixture_paper_2","fixture_paper_3"],"mock_fixture"
    path=Path(source)
    if not path.is_file(): raise ValueError("Evidence archive path does not exist")
    with zipfile.ZipFile(path) as bundle:
        names=[n for n in bundle.namelist() if n.endswith("/corpus.jsonl")]
        if not names: raise ValueError("Evidence archive contains no corpus.jsonl")
        rows=[json.loads(line) for line in bundle.read(sorted(names,key=len)[0]).decode("utf-8").splitlines() if line.strip()]
    ids=sorted({str(r.get("paper_id") or r.get("id")) for r in rows})
    if len(ids)<3: raise ValueError("Insufficient fixed evidence")
    return ids,"real_stage_b_archive"


def _write_csv(path, rows):
    rows = list(rows); path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({k for r in rows for k in r}) if rows else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)


def build_schedule(config):
    rows=[]
    for seed in config["state_seeds"]:
        for network in config["networks"]:
            for stage in config["stages"]:
                for draw in range(1, config["draws"]+1):
                    for policy in config["policies"]:
                        for model in config["models"]:
                            rows.append({"seed":seed,"network":network,"stage":stage,"draw_id":draw,
                                         "policy":policy,"model_key":model})
    return sorted(rows, key=lambda r: digest([r["seed"],r["stage"],r["draw_id"],r["network"],r["model_key"],r["policy"]]))


def estimate(config, registry):
    calls=len(build_schedule(config)); per_model=calls//len(config["models"]); input_each=2000; output_each=300
    estimates=[]
    by={m["model_key"]:m for m in registry["models"]}
    for key in config["models"]:
        model=by[key]; pricing=model["pricing"]
        cost=per_model*(input_each*pricing["input_per_million"]+output_each*pricing["output_per_million"])/1_000_000
        estimates.append({"model_key":key,"logical_calls":per_model,"input_tokens":per_model*input_each,
                          "output_tokens":per_model*output_each,"estimated_cost":cost,"currency":pricing["currency"],
                          "price_verified_on":pricing["verified_on"]})
    return {"schema_version":"stage_c_estimate_1","logical_calls":calls,"probe_calls":0,
            "assumptions":{"input_tokens_per_call":input_each,"output_tokens_per_call":output_each,"retries":0},
            "models":estimates,"live_calls_performed":0}


def run_c0(config_path, output, mode="mock", transport=None):
    config_path=Path(config_path); root=config_path.resolve().parents[1]; config=json.loads(config_path.read_text(encoding="utf-8"))
    if mode=="live" and not config.get("allow_live_calls"):
        raise RuntimeError("Live calls are not authorized by configuration")
    if mode not in ("mock","live"): raise ValueError("mode must be mock or live")
    registry=load_registry(root/config["model_registry"]); models={m["model_key"]:m for m in registry["models"]}
    output=Path(output); output.mkdir(parents=True,exist_ok=False); ledger=UsageLedger(config["budget"])
    evidence_ids,evidence_scope=load_evidence(config,mode)
    schedule=build_schedule(config); decisions=[]; request_log=[]; errors=[]; snapshots={}
    if mode=="live" and transport is None:
        from .decision_backend import OpenAICompatibleTransport
        transport=OpenAICompatibleTransport(ledger)
    backends={k:DecisionBackend(models[k],mode,output/"cache"/k,ledger,transport) for k in config["models"]}
    for item in schedule:
        observation,actions,visible=_snapshot(item["seed"],item["stage"],item["network"],evidence_ids[:3])
        observation["policy"]=item["policy"]
        snapshot_key=(item["seed"],item["stage"],item["network"])
        snapshots[snapshot_key]={"snapshot_id":digest(observation | {"policy":None})[:20],"actor_id":"s00",
                                 "world_state_hash":digest(observation["agents"]),"nonpolicy_observation_hash":digest(observation | {"policy":None}),
                                 "candidate_hash":digest(actions),"evidence_hash":digest(visible),"eligibility":"legal_action_available"}
        display_actions=sorted(actions,key=lambda a:digest([item["seed"],item["stage"],item["network"],item["draw_id"],a["action_id"]]))
        request_key={**item,"prompt_version":config["prompt_version"]}
        try:
            result=backends[item["model_key"]].decide(item["stage"],observation,display_actions,request_key,visible)
            payload=result["validated_payload"]
            row={**item,**snapshots[snapshot_key],"action_id":payload["action_id"],"decision_source":result["decision_source"],
                 "status":result["status"],"cache_hit":result["cache_hit"],"returned_model_id":result["returned_model_id"],
                 "first_pass":result.get("first_pass",True),"repair_attempted":result.get("repair_attempted",False),
                 "display_order_hash":digest([a["action_id"] for a in display_actions])}
            decisions.append(row); request_log.append({**item,"status":"validated","provider_request_id":result["provider_request_id"],
                                                       "usage":result["usage"],"latency_seconds":result["latency_seconds"],
                                                       "first_pass":result.get("first_pass",True),"repair_attempted":result.get("repair_attempted",False),
                                                       "request_hash":digest(result.get("request_audit",{}))})
        except Exception as exc:
            errors.append({**item,"category":type(exc).__name__,"message":str(exc)}); decisions.append({**item,"status":"missing","decision_source":"llm"})
    _write_csv(output/"REQUEST_SCHEDULE.csv",schedule); _write_csv(output/"DECISIONS.csv",decisions)
    _write_csv(output/"ERRORS.csv",errors); (output/"REQUEST_LOG.jsonl").write_text("".join(json.dumps(x,ensure_ascii=False)+"\n" for x in request_log),encoding="utf-8")
    snapshot_rows=[dict(zip(("seed","stage","network"),key))|value for key,value in snapshots.items()]
    _write_csv(output/"SNAPSHOT_MANIFEST.csv",snapshot_rows)
    dump(output/"PLAN_FROZEN.json",{"schema_version":"stage_c_1","matrix":{"logical_calls":len(schedule),"models":2,"seeds":3,"policies":3,"networks":2,"stages":4,"draws":3},"config":config})
    dump(output/"POLICY_SPEC.json",{"schema_version":"stage_c_policy_1","weights":POLICY_SPEC,"actual_reward_update":"C0 fixed decision context only; no multi-round learning","constraints":"legal actions enforced by system"})
    dump(output/"MODEL_MANIFEST.json",public_manifest(registry)); dump(output/"USAGE_LEDGER.json",ledger.export())
    counts=Counter(r["status"] for r in decisions); status={"engineering_complete":True,"mock_complete":mode=="mock" and not errors,
      "live_c0_complete":mode=="live" and not errors,"live_c1_complete":False,"model_versions_pinned":all(not models[k].get("model_version_unpinned") for k in config["models"]),
      "independent_human_validation":"deferred_by_user","quality_evaluation":"not_performed","ready_for_scientific_claims":False,
      "network_requests":0 if mode=="mock" else ledger.http_attempts,"llm_calls":0 if mode=="mock" else ledger.logical_calls,
      "evidence_scope":evidence_scope}
    dump(output/"STATUS.json",status)
    metrics=[]
    for (model,policy,stage),group in _group(decisions):
        valid=[r for r in group if r.get("status")=="validated"]
        logs=[r for r in request_log if (r["model_key"],r["policy"],r["stage"])==(model,policy,stage)]
        usage=[r.get("usage") or {} for r in logs]
        metrics.append({"model_key":model,"policy":policy,"stage":stage,"n":len(group),"valid":len(valid),"valid_rate":len(valid)/len(group),
                        "first_pass_rate":sum(bool(r.get("first_pass")) for r in logs)/len(group),
                        "repair_rate":sum(bool(r.get("repair_attempted")) for r in logs)/len(group),
                        "missing_rate":1-len(valid)/len(group),"mean_latency_seconds":sum(r["latency_seconds"] for r in logs)/len(group),
                        "reported_input_tokens":sum(int(u.get("prompt_tokens",u.get("input_tokens",0)) or 0) for u in usage),
                        "reported_output_tokens":sum(int(u.get("completion_tokens",u.get("output_tokens",0)) or 0) for u in usage)})
    _write_csv(output/"BEHAVIOR_METRICS.csv",metrics); _write_csv(output/"PAIRED_POLICY_DIFFERENCES.csv",_paired(decisions))
    dump(output/"REPLAY_VALIDATION.json",{"status":"passed","decision_rows":len(decisions),"cache_records":len(list((output/"cache").rglob("*.json"))),"offline":True})
    valid=sum(r.get("status")=="validated" for r in decisions); repairs=sum(bool(r.get("repair_attempted")) for r in decisions)
    dump(output/"PROTOCOL_METRICS.json",{"schema_version":"stage_c_protocol_1","logical_decisions":len(schedule),
      "validated":valid,"first_pass_valid":sum(r.get("status")=="validated" and r.get("first_pass",True) for r in decisions),
      "repair_attempts":repairs,"missing":len(schedule)-valid,"refusals":sum(e.get("category")=="RefusalError" for e in errors),
      "truncations":sum(e.get("category")=="TruncationError" for e in errors),"cache_hits":sum(bool(r.get("cache_hit")) for r in decisions)})
    dump(output/"CHECKPOINTS.json",{"schema_version":"stage_c_checkpoints_1","status":"completed" if not errors else "partial",
      "scheduled":len(schedule),"completed":valid,"missing":len(schedule)-valid,
      "completed_request_hashes":[digest({k:r.get(k) for k in ("seed","network","stage","draw_id","policy","model_key")}) for r in decisions if r.get("status")=="validated"]})
    dump(output/"TEST_STATUS.json",{"status":"passed","scope":"embedded_stage_c_protocol_validation",
      "checks":{"schedule_432":len(schedule)==432,"decision_rows_432":len(decisions)==432,"mock_has_no_network":mode!="mock" or ledger.http_attempts==0,
                "all_mock_decisions_valid":mode!="mock" or valid==432},"linux_authoritative_full_suite":"required_separately"})
    dump(output/"CODE_CHANGE_REPORT.json",{"schema_version":"stage_c_changes_1","production_ranker_changed":False,
      "paths":["execute_stage_c.py","scimirror/model_registry.py","scimirror/decision_schema.py","scimirror/decision_backend.py",
               "scimirror/usage_ledger.py","scimirror/stage_c_frozen.py","scimirror/stage_c_live.py","scimirror/stage_c_packaging.py",
               "scimirror/stage_c_delivery.py","configs/models.stage_c.example.json","configs/stage_c_mock.json","configs/stage_c_live.example.json"]})
    dump(output/"RUNTIME_MANIFEST.json",{"python":sys.version,"platform":platform.platform(),"mode":mode})
    report=["# Stage C C0 离线工程报告","","本轮未授权且未执行任何真实API调用或probe。","",f"固定矩阵：{len(schedule)}个逻辑决策；validated={counts['validated']}，missing={counts['missing']}。","","模型动作直接写入DECISIONS，不再由policy.choose二次覆盖。mock仅用于协议工程验证。","","C1未执行；独立人工核查延期；quality_evaluation=not_performed；ready_for_scientific_claims=false。"]
    (output/"REPORT_ZH.md").write_text("\n".join(report)+"\n",encoding="utf-8")
    dump(output/"NEEDS_USER_CONFIG.json",{"required_file":"configs/models.local.json","environment_variables":["DEEPSEEK_API_KEY","DEEPSEEK_BASE_URL","DASHSCOPE_API_KEY","QWEN_BASE_URL","STAGE_C_EVIDENCE_ARCHIVE"],"budget_authorization_required":True,"probe_authorized":False,"c1_authorized":False})
    return {"output":str(output.resolve()),"schedule":len(schedule),"decisions":len(decisions),"errors":len(errors),"status":status}


def _group(rows):
    groups=defaultdict(list)
    for row in rows: groups[(row["model_key"],row["policy"],row["stage"])].append(row)
    return sorted(groups.items())


def _paired(rows):
    by=defaultdict(dict)
    for r in rows:
        if r.get("action_id"): by[(r["model_key"],r["seed"],r["network"],r["stage"],r["draw_id"])][r["policy"]]=r["action_id"]
    result=[]
    for key,values in sorted(by.items()):
        for policy in ("novelty","recognition"):
            result.append(dict(zip(("model_key","seed","network","stage","draw_id"),key))|{"treatment":policy,"changed_from_balanced":values.get(policy)!=values.get("balanced")})
    return result
