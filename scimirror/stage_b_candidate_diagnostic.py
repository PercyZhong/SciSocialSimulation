"""Fixed candidate-recall and reranking-regression diagnostic for Stage B."""
from __future__ import annotations

import csv
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

from .retrieval_evaluation import EVALUATOR_VERSION, ranked_metrics
from .retrieval_soft_ranker import soft_rank
from .semantic_fingerprint import combine_fingerprint, json_fingerprint, raw_sha256, semantic_sha256, source_fingerprint
from .stage_a import read_jsonl
from .stage_b_improvement import (_canonical_papers, _strict_metrics, load_json, safe_extract,
                                  verify_checksums, write_csv, write_json, write_jsonl)
from .stage_b_pilot import bm25_scores, read_records
from .v03_retrieval import load_retrieval_profiles

SCHEMA = "stage_b_candidate_diagnostic_1"
RANKERS = ("bm25", "soft_legacy_k20", "soft_legacy_all", "soft_aligned_k20", "soft_aligned_all")
CACHED = {"bm25": "bm25_lexical", "soft_legacy_k20": "soft_legacy_profile",
          "soft_aligned_k20": "soft_aligned_profile"}


def _locate_previous(path, scratch=None):
    path = Path(path).resolve()
    root = path
    if path.is_file():
        if path.suffix.lower() != ".zip": raise ValueError("Source archive must be zip")
        root = Path(scratch or path.parent/(path.stem+"_extracted"))
        if root.exists(): raise ValueError("Archive extraction target already exists")
        safe_extract(path, root)
    required = {"PLAN_FROZEN.json", "ranker_runs.jsonl", "metrics_by_query_ranker.csv", "reproduction"}
    candidates=[]
    for candidate in [root, *[p for p in root.rglob("*") if p.is_dir()]]:
        if candidate.exists() and required <= {p.name for p in candidate.iterdir()}: candidates.append(candidate)
    candidates=sorted(set(candidates))
    if len(candidates) != 1: raise ValueError("Expected exactly one previous improvement run: "+", ".join(map(str,candidates)))
    return candidates[0]


def _views(source):
    blind={row["blind_id"]:(row["query_id"],row["paper_id"]) for row in read_records(source/"private"/"blind_mapping.csv")}
    ratings=read_jsonl(source/"private"/"ratings_history.jsonl"); adjudications=read_jsonl(source/"private"/"adjudication_history.jsonl")
    reviewers=load_json(source/"private"/"reviewers.json")
    ids=[r["reviewer_id"] for r in reviewers["reviewers"] if r["role"]=="reviewer"]
    if len(ids)!=2: raise ValueError("Exactly two rating reviewers required")
    latest_rating={}; latest_adj={}
    for row in ratings:
        key=(row["blind_id"],row["reviewer_id"])
        if key not in latest_rating or int(row.get("revision",1))>int(latest_rating[key].get("revision",1)): latest_rating[key]=row
    for row in adjudications:
        key=row["blind_id"]
        if key not in latest_adj or int(row.get("revision",1))>int(latest_adj[key].get("revision",1)): latest_adj[key]=row
    views={"reviewer01":{},"reviewer02":{},"final":{}}
    for blind_id,pair in blind.items():
        values=[]
        for index,reviewer in enumerate(ids,1):
            row=latest_rating.get((blind_id,reviewer)); value=row.get("score") if row else None
            views[f"reviewer0{index}"][pair]=value; values.append(value)
        final=values[0] if None not in values and values[0]==values[1] else None
        adj=latest_adj.get(blind_id)
        if final is None and adj and not adj.get("unresolved"): final=adj.get("final_grade")
        views["final"][pair]=final
    return views, ratings, adjudications, reviewers


def _evaluate(runs, queries, views, config):
    topics={q["query_id"]:q["topic_id"] for q in queries}; metrics=[]
    for view,pairs in views.items():
        by_query=defaultdict(dict)
        for (qid,pid),grade in pairs.items(): by_query[qid][pid]=grade
        for item in runs:
            grades=by_query[item["query_id"]]; selected={pid:grades.get(pid) for pid in item["ranked_ids"]}
            for k in config["ks"]:
                row=ranked_metrics(item["ranked_ids"],int(k),selected,grades,"full_corpus")
                row.update(_strict_metrics(item["ranked_ids"],int(k),grades,2))
                returned=item["ranked_ids"][:int(k)]
                zeros=sum(item.get("bm25_scores",{}).get(pid,1.0)==0 for pid in returned)
                row.update({"label_view":view,"query_id":item["query_id"],"topic_id":topics[item["query_id"]],
                  "ranker_id":item["ranker_id"],"zero_score_returned":zeros,
                  "zero_score_returned_ratio":zeros/len(returned) if returned else None})
                metrics.append(row)
    def mean(rows,key):
        values=[r[key] for r in rows if r.get(key) is not None]
        return sum(values)/len(values) if values else None
    topic_groups=defaultdict(list); macro_groups=defaultdict(list)
    for row in metrics:
        topic_groups[(row["label_view"],row["topic_id"],row["ranker_id"],row["k"])].append(row)
        macro_groups[(row["label_view"],row["ranker_id"],row["k"])].append(row)
    topics_out=[{"label_view":key[0],"topic_id":key[1],"ranker_id":key[2],"k":key[3],"query_n":len(rows),
      "mean_precision_at_k":mean(rows,"precision_at_k"),"mean_ndcg_at_k":mean(rows,"ndcg_at_k"),
      "mean_recall_at_k":mean(rows,"recall_at_k"),"mean_zero_score_returned_ratio":mean(rows,"zero_score_returned_ratio"),
      "empty_rate":sum(r["empty"] for r in rows)/len(rows)} for key,rows in sorted(topic_groups.items())]
    macro=[{"label_view":key[0],"ranker_id":key[1],"k":key[2],"query_n":len(rows),
      "query_macro_precision_at_k":mean(rows,"precision_at_k"),"query_macro_ndcg_at_k":mean(rows,"ndcg_at_k"),
      "query_macro_recall_at_k":mean(rows,"recall_at_k"),"query_macro_zero_score_returned_ratio":mean(rows,"zero_score_returned_ratio"),
      "empty_rate":sum(r["empty"] for r in rows)/len(rows)} for key,rows in sorted(macro_groups.items())]
    return metrics,topics_out,macro


def _decision(macro, topics, tolerance):
    lookup={(r["label_view"],r["ranker_id"],r["k"]):r for r in macro}
    topic={(r["label_view"],r["topic_id"],r["ranker_id"],r["k"]):r for r in topics}
    topic_ids=sorted({r["topic_id"] for r in topics}); result={}
    for ranker in RANKERS[1:]:
        b5=lookup[("final","bm25",5)]; a5=lookup[("final",ranker,5)]
        b3=lookup[("final","bm25",3)]; a3=lookup[("final",ranker,3)]
        topic_ok=all(topic[("final",t,ranker,5)]["mean_ndcg_at_k"]+tolerance>=topic[("final",t,"bm25",5)]["mean_ndcg_at_k"] for t in topic_ids)
        result[ranker]={"ndcg_at_5_difference":a5["query_macro_ndcg_at_k"]-b5["query_macro_ndcg_at_k"],
          "p_at_3_difference":a3["query_macro_precision_at_k"]-b3["query_macro_precision_at_k"],
          "all_topics_noninferior":topic_ok,
          "candidate_for_future_validation":a5["query_macro_ndcg_at_k"]>b5["query_macro_ndcg_at_k"]+tolerance and a3["query_macro_precision_at_k"]+tolerance>=b3["query_macro_precision_at_k"] and topic_ok}
    return result


def _dcg(grade, rank):
    return 0.0 if rank is None or rank>5 or grade is None else (2**int(grade)-1)/math.log2(rank+1)


def _copy_reproduction(root, previous, output, config_path):
    rep=output/"reproduction"; source=rep/"source"; previous_copy=rep/"previous_run"
    source.mkdir(parents=True,exist_ok=True); previous_copy.mkdir(parents=True,exist_ok=True)
    for relative in ("execute_stage_b_candidate_diagnostic.py","requirements.txt","configs/stage_b_candidate_diagnostic.json",
                     "data/stage_b_query_routes_v1.json","data/retrieval_pilot_profiles_v1.json",
                     "data/retrieval_topic_profiles_repair_v1.json","data/topics.json"):
        target=source/relative; target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(root/relative,target)
    for path in sorted((root/"scimirror").glob("*.py")):
        target=source/"scimirror"/path.name; target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(path,target)
    needed=("PLAN_FROZEN.json","STATUS.json","BASELINE_REPLAY.json","ranker_runs.jsonl","metrics_by_query_ranker.csv",
            "metrics_by_topic.csv","metrics_query_macro.csv","REVIEW_PROVENANCE.json")
    for name in needed: shutil.copy2(previous/name,previous_copy/name)
    shutil.copytree(previous/"reproduction"/"source_run",previous_copy/"reproduction"/"source_run")
    files=sorted(p for p in previous_copy.rglob("*") if p.is_file())
    (previous_copy/"CHECKSUMS.sha256").write_text("".join(f"{raw_sha256(p)}  ./{p.relative_to(previous_copy).as_posix()}\n" for p in files),encoding="utf-8")
    (rep/"README_REPRODUCE.md").write_text("# 离线复现\n\n在 `reproduction/source` 中执行：\n\n`python execute_stage_b_candidate_diagnostic.py run --source-run ../previous_run --output <new-output>`\n",encoding="utf-8")


def run(config_path, source_path, output, root):
    config_path=Path(config_path); root=Path(root); output=Path(output)
    if output.exists() and any(output.iterdir()): raise ValueError("Output directory is not empty")
    output.mkdir(parents=True,exist_ok=True); config=load_json(config_path)
    if tuple(config["rankers"])!=RANKERS or config["lambda"]!=.10 or config["main_candidate_k"]!=20: raise ValueError("Frozen matrix changed")
    if config.get("allow_network") or config.get("allow_llm_calls"): raise ValueError("Offline diagnostic required")
    extract=output/"_source_extract" if Path(source_path).is_file() else None
    previous=_locate_previous(source_path,extract); before=raw_sha256(previous/"CHECKSUMS.sha256") if (previous/"CHECKSUMS.sha256").exists() else None
    audit=verify_checksums(previous); write_json(output/"INPUT_AUDIT.json",{**audit,"source":str(previous),"checksum_before":before,"historical_read_only":True})
    if audit["status"]!="passed": raise ValueError("Previous delivery checksum validation failed")
    original=previous/"reproduction"/"source_run"; original_audit=verify_checksums(original)
    if original_audit["status"]!="passed": raise ValueError("Frozen pilot snapshot checksum validation failed")
    papers,family,_=_canonical_papers(original); queries=read_records(original/"queries.csv")
    views,ratings,adjudications,reviewers=_views(original)
    routes=load_json(root/config["routes_path"]); route_by={r["query_id"]:r for r in routes["routes"]}
    aligned=load_json(root/config["aligned_profiles_path"]); legacy=load_retrieval_profiles(root/config["legacy_profiles_path"])
    previous_runs=read_jsonl(previous/"ranker_runs.jsonl"); previous_by={(r["query_id"],r["ranker_id"]):r for r in previous_runs}
    runs=[]; full_rows=[]; change_rows=[]; normalization=[]; counterfactual=[]; recomputed={}; cached_count=0; computed_count=0
    final_grades=defaultdict(dict); reviewer_grades={name:defaultdict(dict) for name in ("reviewer01","reviewer02")}
    for (qid,pid),grade in views["final"].items(): final_grades[qid][pid]=grade
    for view in reviewer_grades:
        for (qid,pid),grade in views[view].items(): reviewer_grades[view][qid][pid]=grade
    for query in queries:
        qid=query["query_id"]; route=route_by[qid]
        lexical=bm25_scores(query["query_text"],papers,config["bm25_k1"],config["bm25_b"])
        order=sorted(papers,key=lambda pid:(-lexical[pid],pid)); rank={pid:i+1 for i,pid in enumerate(order)}
        for pid in order:
            full_rows.append({"query_id":qid,"topic_id":query["topic_id"],"paper_id":pid,"family_id":family[pid],
              "bm25_score":lexical[pid],"bm25_rank":rank[pid],"zero_score":lexical[pid]==0,
              "final_grade":final_grades[qid].get(pid),"reviewer_01_grade":reviewer_grades["reviewer01"][qid].get(pid),
              "reviewer_02_grade":reviewer_grades["reviewer02"][qid].get(pid)})
        profiles={"legacy":legacy["topics"].get(query["topic_id"]),
                  "aligned":legacy["topics"].get(route["retrieval_topic_id"]) or aligned["topics"].get(route["retrieval_topic_id"])}
        soft={}
        for profile_name,profile in profiles.items():
            soft[(profile_name,20)]=soft_rank(query["query_text"],papers,profile,candidate_k=20,output_k=10,
              lambda_value=.10,k1=config["bm25_k1"],b=config["bm25_b"])
            soft[(profile_name,len(papers))]=soft_rank(query["query_text"],papers,profile,candidate_k=len(papers),output_k=10,
              lambda_value=.10,k1=config["bm25_k1"],b=config["bm25_b"])
            d20=soft[(profile_name,20)]["normalization_denominators"]; dall=soft[(profile_name,len(papers))]["normalization_denominators"]
            changed=any(abs(d20[k]-dall[k])>config["floating_tolerance"] for k in ("bm25","topic"))
            normalization.append({"query_id":qid,"topic_id":query["topic_id"],"profile":profile_name,
              "k20_bm25_denominator":d20["bm25"],"all_bm25_denominator":dall["bm25"],
              "k20_topic_denominator":d20["topic"],"all_topic_denominator":dall["topic"],
              "denominator_changed":changed,"comparison_interpretation":"candidate_size_and_normalization_joint_change" if changed else "candidate_size_only",
              "counterfactual_status":"computed" if changed else "not_needed"})
            if changed:
                cf=soft_rank(query["query_text"],papers,profile,candidate_k=len(papers),output_k=10,lambda_value=.10,
                  k1=config["bm25_k1"],b=config["bm25_b"],normalization_denominators=d20)
                counterfactual.append({"query_id":qid,"topic_id":query["topic_id"],"profile":profile_name,
                  "ranker_id":f"diagnostic_{profile_name}_all_fixed_k20_denominators","candidate_count":len(papers),
                  "ranked_ids":cf["returned_ids"],"normalization_denominators":d20,"scores":cf["scores"],
                  "diagnostic_only":True,"eligible_for_selection":False})
        specs=(("bm25",None,None),("soft_legacy_k20","legacy",20),("soft_legacy_all","legacy",len(papers)),
               ("soft_aligned_k20","aligned",20),("soft_aligned_all","aligned",len(papers)))
        for ranker_id,profile_name,candidate_k in specs:
            if ranker_id in CACHED:
                prior=previous_by[(qid,CACHED[ranker_id])]; ranked=list(prior["ranked_ids"]); candidates=list(prior["candidate_ids"]); cached_count+=1
                if ranker_id!="bm25":
                    check=soft[(profile_name,20)]
                    if ranked!=check["returned_ids"] or candidates!=check["candidate_ids"]: raise ValueError("K20 cached ranking mismatch")
            else:
                result=soft[(profile_name,candidate_k)]; ranked=result["returned_ids"]; candidates=result["candidate_ids"]; computed_count+=1
            result=None if ranker_id=="bm25" else soft[(profile_name,candidate_k)]
            runs.append({"query_id":qid,"topic_id":query["topic_id"],"ranker_id":ranker_id,"ranked_ids":ranked,
              "candidate_ids":candidates,"candidate_count":len(candidates),"returned_count":len(ranked),"empty":not ranked,
              "zero_signal":max(lexical.values(),default=0)==0,"bm25_scores":lexical,
              "normalization_denominators":result["normalization_denominators"] if result else None,
              "normalization_source":result["normalization_source"] if result else None,
              "retrieval_topic_id":route["retrieval_topic_id"] if "aligned" in ranker_id else query["topic_id"]})
            final_rank={pid:i+1 for i,pid in enumerate(ranked)}
            for pid in order:
                score=(result["scores"][pid] if result else {"bm25_normalized":lexical[pid]/max(lexical.values()) if max(lexical.values()) else 0,
                  "topic_score_raw":None,"topic_score_normalized":None,"rerank_score":lexical[pid]})
                change_rows.append({"query_id":qid,"topic_id":query["topic_id"],"ranker_id":ranker_id,"paper_id":pid,
                  "bm25_rank":rank[pid],"final_rank":final_rank.get(pid),"candidate_rank":rank[pid] if pid in candidates else None,
                  "exclusion_reason":None if pid in final_rank else ("outside_candidate" if pid not in candidates else "final_top10"),
                  "bm25_raw":lexical[pid],"bm25_normalized":score["bm25_normalized"],"topic_score_raw":score["topic_score_raw"],
                  "topic_score_normalized":score["topic_score_normalized"],"weighted_topic_bonus":None if score["topic_score_normalized"] is None else .10*score["topic_score_normalized"],
                  "final_score":score["rerank_score"],"final_grade":final_grades[qid].get(pid)})
    if len(runs)!=45 or len({(r["query_id"],r["ranker_id"]) for r in runs})!=45: raise ValueError("Main matrix incomplete")
    write_jsonl(output/"ranker_runs.jsonl",runs); write_csv(output/"bm25_full_ranking.csv",full_rows); write_csv(output/"ranking_changes.csv",change_rows)
    write_csv(output/"normalization_diagnostics.csv",normalization); write_jsonl(output/"normalization_counterfactual_runs.jsonl",counterfactual)
    metrics,topic_metrics,macro=_evaluate(runs,queries,views,config)
    write_csv(output/"metrics_by_query_ranker.csv",metrics); write_csv(output/"metrics_by_topic.csv",topic_metrics); write_csv(output/"metrics_query_macro.csv",macro)
    # Candidate recall is evaluated after retrieval and explicitly preserves unknown labels.
    recall_rows=[]
    for view,pairs in views.items():
        byq=defaultdict(dict)
        for (qid,pid),grade in pairs.items(): byq[qid][pid]=grade
        for query in queries:
            qid=query["query_id"]; grades=byq[qid]; complete=all(v is not None for v in grades.values())
            lexical=bm25_scores(query["query_text"],papers,config["bm25_k1"],config["bm25_b"]); order=sorted(papers,key=lambda pid:(-lexical[pid],pid))
            for threshold in (1,2):
                total=sum(v is not None and v>=threshold for v in grades.values())
                for k in (10,20,len(papers)):
                    candidates=order[:k]; hits=sum(grades.get(pid) is not None and grades[pid]>=threshold for pid in candidates)
                    recall_rows.append({"query_id":qid,"topic_id":query["topic_id"],"label_view":view,"relevance_rule":f"grade>={threshold}" if threshold==1 else "grade=2",
                      "candidate_k":"all" if k==len(papers) else k,"eligible_document_count":len(papers),"relevant_total":total,
                      "relevant_retrieved":hits,"recall":hits/total if complete and total else None,
                      "recall_status":"complete" if complete and total else ("no_relevant_supply" if complete else "partial"),
                      "unjudged_count":sum(v is None for v in grades.values()),"zero_score_candidate_count":sum(lexical[pid]==0 for pid in candidates)})
    write_csv(output/"candidate_recall.csv",recall_rows)
    # Per-query ranking changes and exact DCG conservation under the final view.
    metric_lookup={(r["label_view"],r["query_id"],r["ranker_id"],r["k"]):r for r in metrics}; run_by={(r["query_id"],r["ranker_id"]):r for r in runs}; regression=[]
    for query in queries:
        qid=query["query_id"]; base=run_by[(qid,"bm25")]; base_rank={p:i+1 for i,p in enumerate(base["ranked_ids"])}
        for treatment in RANKERS[1:]:
            treated=run_by[(qid,treatment)]; new_rank={p:i+1 for i,p in enumerate(treated["ranked_ids"])}; docs=[]
            for pid in sorted(papers):
                grade=final_grades[qid].get(pid); delta=_dcg(grade,new_rank.get(pid))-_dcg(grade,base_rank.get(pid))
                if delta or base_rank.get(pid)!=new_rank.get(pid): docs.append({"paper_id":pid,"grade":grade,"bm25_rank":base_rank.get(pid),"treatment_rank":new_rank.get(pid),"dcg_at_5_delta":delta})
            total=sum(d["dcg_at_5_delta"] for d in docs)
            a=metric_lookup[("final",qid,treatment,5)]; b=metric_lookup[("final",qid,"bm25",5)]
            regression.append({"query_id":qid,"topic_id":query["topic_id"],"treatment":treatment,
              "ndcg_at_5_difference":None if a["ndcg_at_k"] is None or b["ndcg_at_k"] is None else a["ndcg_at_k"]-b["ndcg_at_k"],
              "precision_at_3_difference":metric_lookup[("final",qid,treatment,3)]["precision_at_k"]-metric_lookup[("final",qid,"bm25",3)]["precision_at_k"],
              "entered_top3":json.dumps(sorted(set(treated["ranked_ids"][:3])-set(base["ranked_ids"][:3]))),
              "exited_top3":json.dumps(sorted(set(base["ranked_ids"][:3])-set(treated["ranked_ids"][:3]))),
              "entered_top5":json.dumps(sorted(set(treated["ranked_ids"][:5])-set(base["ranked_ids"][:5]))),
              "exited_top5":json.dumps(sorted(set(base["ranked_ids"][:5])-set(treated["ranked_ids"][:5]))),
              "document_dcg_contributions":json.dumps(docs,separators=(",",":")),"dcg_at_5_difference":total,
              "dcg_conservation_error":abs(total-(a["dcg_partial"]-b["dcg_partial"])),
              "loss_type":"candidate_and_ranking" if len(treated["candidate_ids"])<len(papers) else "ranking_only"})
    write_csv(output/"ranking_regression_analysis.csv",regression)
    # Baseline replay and reviewer sensitivity.
    previous_metrics=read_records(previous/"metrics_by_query_ranker.csv"); previous_metric={(r["label_view"],r["query_id"],r["ranker_id"],str(r["k"])):r for r in previous_metrics}
    mapping={"bm25":"bm25_lexical","soft_legacy_k20":"soft_legacy_profile","soft_aligned_k20":"soft_aligned_profile"}; differences=[]
    for row in metrics:
        if row["ranker_id"] not in mapping: continue
        old=previous_metric.get((row["label_view"],row["query_id"],mapping[row["ranker_id"]],str(row["k"])))
        for field in ("precision_at_k","ndcg_at_k"):
            expected=None if old is None or old[field]=="" else float(old[field]); actual=row[field]
            if (expected is None)!=(actual is None) or (expected is not None and abs(expected-actual)>config["floating_tolerance"]): differences.append({"query_id":row["query_id"],"ranker_id":row["ranker_id"],"k":row["k"],"field":field,"expected":expected,"actual":actual})
    baseline={"status":"passed" if not differences else "failed","cached_ranking_rows":cached_count,"computed_main_rows":computed_count,"ranking_cases":27,"metric_cases":243,"differences":differences}
    write_json(output/"BASELINE_COMPARISON.json",baseline)
    decisions=_decision(macro,topic_metrics,float(config["floating_tolerance"])); sensitivity=[]
    mlookup={(r["label_view"],r["ranker_id"],r["k"]):r for r in macro}
    for view in ("reviewer01","reviewer02"):
        for ranker in RANKERS[1:]:
            delta=mlookup[(view,ranker,5)]["query_macro_ndcg_at_k"]-mlookup[(view,"bm25",5)]["query_macro_ndcg_at_k"]
            sensitivity.append({"label_view":view,"ranker_id":ranker,"ndcg_at_5_difference_vs_bm25":delta,"direction":"positive" if delta>1e-12 else ("negative" if delta< -1e-12 else "tie")})
    write_csv(output/"reviewer_sensitivity.csv",sensitivity)
    dependencies={"previous_delivery_hash":before,"config_hash":json_fingerprint(config_path)["semantic_sha256"],
      "source_hash":combine_fingerprint([source_fingerprint(root/p)["source_sha256"] for p in ("scimirror/stage_b_candidate_diagnostic.py","scimirror/retrieval_soft_ranker.py")]),
      "candidate_sizes":[10,20,len(papers)],"normalization_rule":config["normalization_rule"],"counterfactual_rule":config["counterfactual_rule"]}
    diagnostic_run_id=semantic_sha256(dependencies)[:24]
    write_json(output/"PLAN_FROZEN.json",{"schema_version":SCHEMA,"matrix":{"queries":9,"rankers":list(RANKERS),"runs":45},"fixed_parameters":config,"dependencies":dependencies,"diagnostic_run_id":diagnostic_run_id,"network_requests":0,"llm_calls":0})
    shutil.copy2(previous/"REVIEW_PROVENANCE.json",output/"REVIEW_PROVENANCE.json")
    after=raw_sha256(previous/"CHECKSUMS.sha256") if (previous/"CHECKSUMS.sha256").exists() else None; post=verify_checksums(previous)
    write_json(output/"INPUT_AUDIT.json",{**audit,"source":str(previous),"checksum_before":before,"checksum_after":after,"post_run_status":post["status"],"source_unchanged":before==after and post["status"]=="passed","historical_read_only":True})
    status={"schema_version":SCHEMA,"engineering_complete":baseline["status"]=="passed","linux_authoritative_validation":"pending","exploratory_analysis_complete":True,"independent_human_validation":"deferred_by_user","evaluation_split":"exploratory_reused_pilot","review_scope":"exploratory_ai_assisted_review","ready_for_scientific_claims":False,"production_ranker_changed":False,"candidate_decisions":decisions,"network_requests":0,"llm_calls":0,"paid_api_calls":0}
    write_json(output/"STATUS.json",status)
    write_json(output/"RUNTIME_MANIFEST.json",{"schema_version":SCHEMA,"python":sys.version,"platform":platform.platform(),"git_commit":subprocess.run(["git","rev-parse","HEAD"],cwd=root,text=True,capture_output=True).stdout.strip(),"runtime_path":str(output.resolve()),"source_run":str(previous),"diagnostic_run_id":diagnostic_run_id})
    _write_report(output,queries,full_rows,recall_rows,macro,topic_metrics,regression,normalization,counterfactual,decisions,sensitivity)
    _copy_reproduction(root,previous,output,config_path)
    validation=validate(output,write=False); write_json(output/"DELIVERY_VALIDATION.json",validation)
    if extract and extract.exists(): shutil.rmtree(extract)
    return {"output":str(output.resolve()),"status":status,"baseline":baseline,"validation":validation,"counterfactual_runs":len(counterfactual)}


def _write_report(output, queries, full_rows, recall, macro, topics, regression, normalization, counterfactual, decisions, sensitivity):
    m={(r["label_view"],r["ranker_id"],r["k"]):r for r in macro}; t={(r["label_view"],r["topic_id"],r["ranker_id"],r["k"]):r for r in topics}
    missing=[r for r in full_rows if r["query_id"]=="q_science_evaluation_03" and r["paper_id"]=="openalex_W3015453090"][0]
    all_aligned=next(r for r in read_jsonl(Path(output)/"ranker_runs.jsonl") if r["query_id"]==missing["query_id"] and r["ranker_id"]=="soft_aligned_all")
    lines=["# Stage B 候选召回与排序退化诊断报告","","## 范围","","固定使用36篇文献、9条重复使用查询及既有AI辅助评分；未修改标签、画像、lambda或BM25参数。独立人工核查继续延期。","","## 上轮遗漏文献","",f"`{missing['paper_id']}` 在 `{missing['query_id']}` 的BM25完整排名为第{missing['bm25_rank']}，分数={missing['bm25_score']:.12g}，零分={str(missing['zero_score']).lower()}。K20不包含；all候选包含。all对齐软重排最终名次={all_aligned['ranked_ids'].index(missing['paper_id'])+1 if missing['paper_id'] in all_aligned['ranked_ids'] else 'top10之外'}。","","## 主指标（最终裁决视图）","","|臂|P@3|P@5|nDCG@5|Recall@10|候选规则通过|","|---|---:|---:|---:|---:|---|"]
    for ranker in RANKERS:
        r3=m[("final",ranker,3)]; r5=m[("final",ranker,5)]; r10=m[("final",ranker,10)]
        lines.append(f"|{ranker}|{r3['query_macro_precision_at_k']:.6f}|{r5['query_macro_precision_at_k']:.6f}|{r5['query_macro_ndcg_at_k']:.6f}|{r10['query_macro_recall_at_k']:.6f}|{decisions.get(ranker,{}).get('candidate_for_future_validation','基线')}|")
    lines += ["","## 退化主题证据",""]
    for topic_id in ("agent_memory","science_retrieval"):
        base=t[("final",topic_id,"bm25",5)]["mean_ndcg_at_k"]
        lines.append(f"### {topic_id}")
        for ranker in RANKERS[1:]:
            value=t[("final",topic_id,ranker,5)]["mean_ndcg_at_k"]
            bad=[r for r in regression if r["topic_id"]==topic_id and r["treatment"]==ranker and r["ndcg_at_5_difference"] is not None and r["ndcg_at_5_difference"]<0]
            evidence_parts=[]
            for row in bad:
                negative=[d for d in json.loads(row["document_dcg_contributions"]) if d["dcg_at_5_delta"]<0]
                documents=", ".join(f"{d['paper_id']}(grade={d['grade']}, {d['bm25_rank']}→{d['treatment_rank']}, ΔDCG={d['dcg_at_5_delta']:.6f})" for d in negative)
                evidence_parts.append(f"{row['query_id']}: {documents or '无负DCG文献'}")
            evidence="；".join(evidence_parts) or "无逐查询负差"
            lines.append(f"- {ranker}: topic nDCG@5差值={value-base:+.6f}；{evidence}")
        lines.append("")
    changed=[r for r in normalization if r["denominator_changed"]]
    lines += ["## 归一化混杂","",f"18个(query,profile)比较中，{len(changed)}个K20/all分母发生变化；生成{len(counterfactual)}条固定K20分母反事实。发生变化时，all主臂解释为候选规模与归一化尺度联合变化，固定分母臂仅用于拆解，不参与优选。","","## 原评审敏感性","", "以下方向仅是同一批评分的两个视图，不是独立重复："]
    for row in sensitivity:
        lines.append(f"- {row['label_view']} / {row['ranker_id']}: 相对BM25 nDCG@5差值={row['ndcg_at_5_difference_vs_bm25']:+.6f}，方向={row['direction']}")
    linux_status="pending（当前Windows预验收；本机WSL Python 3.10.12低于AGENTS.md要求的3.11）" if platform.system()=="Windows" else "需结合TEST_STATUS和MOCK_REPLAY_VALIDATION确认"
    lines += ["","## Linux验收","",f"linux_authoritative_validation={linux_status}。Windows结果不能替代Linux权威验收。","","## 决策","", "每个软臂均按预设三项规则独立判断。任何通过也不自动替换生产检索器；未通过时保留BM25且停止，不调lambda。", "", "## 限制","", "全语料候选Recall=1仅是集合覆盖的机械结果；36篇小语料不能支持可扩展性结论。三套标签视图不是独立重复，不做显著性声明。ready_for_scientific_claims=false。"]
    (Path(output)/"REPORT_ZH.md").write_text("\n".join(lines)+"\n",encoding="utf-8")


def validate(run_dir, write=True):
    run_dir=Path(run_dir); runs=read_jsonl(run_dir/"ranker_runs.jsonl"); full=read_records(run_dir/"bm25_full_ranking.csv"); recall=read_records(run_dir/"candidate_recall.csv"); regression=read_records(run_dir/"ranking_regression_analysis.csv"); baseline=load_json(run_dir/"BASELINE_COMPARISON.json"); status=load_json(run_dir/"STATUS.json")
    byq=defaultdict(dict)
    for row in full: byq[row["query_id"]][row["paper_id"]]=int(row["bm25_rank"])
    run_by={(r["query_id"],r["ranker_id"]):r for r in runs}
    nesting=all(set(run_by[(qid,"soft_legacy_k20")]["candidate_ids"])<=set(run_by[(qid,"soft_legacy_all")]["candidate_ids"]) and set(run_by[(qid,"soft_aligned_k20")]["candidate_ids"])<=set(run_by[(qid,"soft_aligned_all")]["candidate_ids"]) for qid in byq)
    recall_groups=defaultdict(list)
    for row in recall:
        if row["recall"]!="": recall_groups[(row["query_id"],row["label_view"],row["relevance_rule"])].append((36 if row["candidate_k"]=="all" else int(row["candidate_k"]),float(row["recall"])))
    monotonic=all(all(values[i][1]<=values[i+1][1]+1e-12 for i in range(len(values)-1)) for values in (sorted(v) for v in recall_groups.values()))
    checks={"baseline_k20_exact":baseline["status"]=="passed","main_matrix_45":len(runs)==45 and len({(r['query_id'],r['ranker_id']) for r in runs})==45,
      "bm25_full_324":len(full)==324 and len({(r['query_id'],r['paper_id']) for r in full})==324,"candidate_nesting":nesting,
      "candidate_recall_monotonic":monotonic,"all_candidates_cover_36":all(len(r["candidate_ids"])==36 for r in runs if r["ranker_id"].endswith("_all")),
      "dcg_conservation":all(float(r["dcg_conservation_error"])<=1e-12 for r in regression),
      "scientific_scope":status["ready_for_scientific_claims"] is False and status["production_ranker_changed"] is False}
    result={"schema_version":SCHEMA,"status":"passed" if all(checks.values()) else "failed","all_passed":all(checks.values()),"checks":checks,"run_rows":len(runs),"full_ranking_rows":len(full),"network_requests":0,"llm_calls":0}
    if write: write_json(run_dir/"DELIVERY_VALIDATION.json",result)
    return result


def reproduce(run_dir, output):
    run_dir=Path(run_dir).resolve(); output=Path(output).resolve(); isolated=run_dir/"reproduction"/"source"; previous=run_dir/"reproduction"/"previous_run"; config=isolated/"configs"/"stage_b_candidate_diagnostic.json"
    command=[sys.executable,str(isolated/"execute_stage_b_candidate_diagnostic.py"),"run","--config",str(config),"--source-run",str(previous),"--output",str(output)]
    env=os.environ.copy(); env["PYTHONPATH"]=str(isolated)
    completed=subprocess.run(command,cwd=isolated,env=env,text=True,capture_output=True)
    if completed.returncode:
        record={"status":"failed","exit_code":completed.returncode,"stdout":completed.stdout,"stderr":completed.stderr}; output.mkdir(parents=True,exist_ok=True); write_json(output/"REPRODUCTION_VALIDATION.json",record); return record
    original=read_jsonl(run_dir/"ranker_runs.jsonl"); replay=read_jsonl(output/"ranker_runs.jsonl"); same_runs=original==replay
    same_metrics=read_records(run_dir/"metrics_by_query_ranker.csv")==read_records(output/"metrics_by_query_ranker.csv")
    probe=subprocess.run([sys.executable,"-c","import scimirror.stage_b_candidate_diagnostic as m; print(m.__file__)"],cwd=isolated,env=env,text=True,capture_output=True)
    loaded=probe.stdout.strip(); inside=probe.returncode==0 and Path(loaded).resolve().is_relative_to(isolated.resolve())
    record={"status":"passed" if same_runs and same_metrics and inside else "failed","ranking_semantic_equal":same_runs,"metrics_semantic_equal":same_metrics,"actual_loaded_module":loaded,"module_inside_isolated_source":inside,"exit_code":completed.returncode,"stderr":completed.stderr+probe.stderr}
    write_json(output/"REPRODUCTION_VALIDATION.json",record); return record


def finalize(run_dir):
    run_dir=Path(run_dir); excluded={"CHECKSUMS.sha256","stage_b_candidate_diagnostic_delivery.zip","ARCHIVE_SHA256.txt"}
    files=sorted(p for p in run_dir.rglob("*") if p.is_file() and p.name not in excluded)
    (run_dir/"CHECKSUMS.sha256").write_text("".join(f"{raw_sha256(p)}  {p.relative_to(run_dir).as_posix()}\n" for p in files),encoding="utf-8")
    archive=run_dir/"stage_b_candidate_diagnostic_delivery.zip"
    with zipfile.ZipFile(archive,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as bundle:
        for path in [*files,run_dir/"CHECKSUMS.sha256"]: bundle.write(path,Path("delivery")/path.relative_to(run_dir))
    digest=raw_sha256(archive); (run_dir/"ARCHIVE_SHA256.txt").write_text(f"{digest}  {archive.name}\n",encoding="utf-8")
    return {"archive":str(archive.resolve()),"sha256":digest,"files":len(files)+1}
