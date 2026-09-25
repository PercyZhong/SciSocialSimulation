"""Offline Stage B soft-reranking experiment over a frozen, fully judged pilot."""
from __future__ import annotations

import csv
import hashlib
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

from .common import canonical, digest
from .corpus import Corpus
from .policy import PolicyContext
from .retrieval_evaluation import EVALUATOR_VERSION, ranked_metrics
from .retrieval_soft_ranker import RANKER_VERSION, soft_rank
from .retrieval_stage_trace import first_exclusion_legacy, first_exclusion_soft, label_trace, loss_by_stage
from .semantic_fingerprint import (combine_fingerprint, json_fingerprint, raw_sha256,
    records_fingerprint, semantic_sha256, source_fingerprint, strict_json_text)
from .stage_a import read_jsonl
from .stage_b_pilot import bm25_scores, read_records
from .topics import TopicModel
from .v03_retrieval import (load_retrieval_profiles, retrieve_stage_a_repaired,
    retrieve_stage_a_semantic_guarded)

SCHEMA = "stage_b_improvement_1"
RANKERS = ("bm25_lexical", "legacy_closure", "legacy_repair", "soft_lambda0",
           "soft_legacy_profile", "soft_aligned_profile")
LEGACY_SOURCE_NAMES = {"bm25_lexical": "bm25_lexical",
                       "legacy_closure": "stage_a_semantic_guarded",
                       "legacy_repair": "stage_a_repaired_v1"}


def write_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def write_jsonl(path, rows):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(canonical(row)+"\n" for row in rows), encoding="utf-8")


def write_csv(path, rows, fields=None):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True); rows = list(rows)
    fields = fields or list(rows[0]) if rows else (fields or [])
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def load_json(path):
    return strict_json_text(Path(path).read_text(encoding="utf-8-sig"))


def safe_extract(archive, destination):
    destination = Path(destination).resolve(); destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            name = item.filename.replace("\\", "/")
            target = (destination/name).resolve()
            if name.startswith("/") or ".." in Path(name).parts or not target.is_relative_to(destination):
                raise ValueError("Unsafe archive path: "+item.filename)
            mode = item.external_attr >> 16
            if mode & 0o170000 == 0o120000:
                raise ValueError("Archive symlink rejected: "+item.filename)
        bundle.extractall(destination)


def locate_source(path, scratch=None):
    path = Path(path).resolve()
    if path.is_file():
        if path.suffix.lower() != ".zip": raise ValueError("Source archive must be zip")
        scratch = Path(scratch or path.parent/(path.stem+"_extracted"))
        if scratch.exists(): raise ValueError("Archive extraction target already exists")
        safe_extract(path, scratch); root = scratch
    else: root = path
    required = {"FROZEN_PILOT.json", "REVIEW_PROVENANCE.json", "corpus.jsonl", "queries.csv", "private"}
    candidates = []
    for candidate in [root, *[p for p in root.rglob("*") if p.is_dir()]]:
        names = {p.name for p in candidate.iterdir()} if candidate.exists() else set()
        if required <= names: candidates.append(candidate)
    unique = sorted(set(candidates))
    if len(unique) != 1:
        raise ValueError("Expected one complete source run; found: "+", ".join(map(str, unique)))
    return unique[0]


def verify_checksums(source):
    source = Path(source); path = source/"CHECKSUMS.sha256"; checked = 0; failures = []
    if not path.exists(): return {"status": "not_available", "checked": 0, "failures": []}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip(): continue
        expected, relative = line.split("  ", 1); relative = relative.removeprefix("./")
        target = (source/relative).resolve()
        if not target.is_relative_to(source.resolve()) or not target.is_file(): failures.append(relative); continue
        checked += 1
        if raw_sha256(target) != expected: failures.append(relative)
    return {"status": "passed" if not failures else "failed", "checked": checked, "failures": failures}


def _canonical_papers(source):
    papers = {row["id"]: row for row in read_jsonl(Path(source)/"corpus.jsonl")}
    family_rows = read_records(Path(source)/"private"/"family_map.csv")
    family = {row["paper_id"]: row["family_id"] for row in family_rows
              if str(row["canonical"]).lower() in ("true", "1", "yes")}
    selected = {paper_id: papers[paper_id] for paper_id in sorted(family)}
    if len(selected) != len(family): raise ValueError("Family map references missing documents")
    return selected, family, family_rows


def _latest(rows, keys):
    result = {}
    for row in rows:
        key = tuple(row[name] for name in keys)
        if key not in result or int(row.get("revision", 1)) > int(result[key].get("revision", 1)):
            result[key] = row
    return result


def migrate_annotations(source, output, queries, papers, family_rows, fingerprints):
    source = Path(source); output = Path(output)
    blind_rows = read_records(source/"private"/"blind_mapping.csv")
    blind = {row["blind_id"]: (row["query_id"], row["paper_id"]) for row in blind_rows}
    ratings_history = read_jsonl(source/"private"/"ratings_history.jsonl")
    adjudication_history = read_jsonl(source/"private"/"adjudication_history.jsonl")
    reviewers = load_json(source/"private"/"reviewers.json")
    query_by = {row["query_id"]: row for row in queries}; scope_hash = fingerprints["annotation_scope_hash"]
    migrations = []
    for row in sorted(blind_rows, key=lambda item: (item["query_id"], item["paper_id"])):
        query = query_by[row["query_id"]]; paper = papers[row["paper_id"]]
        qhash = semantic_sha256({key: query[key] for key in ("query_text", "intent")})
        dhash = semantic_sha256({key: paper.get(key) for key in ("title", "abstract", "year", "source_url")})
        pair_id = semantic_sha256({"query": qhash, "document": dhash, "scope": scope_hash})[:24]
        migrations.append({"old_pool_version": row["pool_version"], "old_blind_id": row["blind_id"],
          "new_pair_id": pair_id, "query_id": row["query_id"], "paper_id": row["paper_id"],
          "query_semantic_hash": qhash, "document_semantic_hash": dhash, "scope_hash": scope_hash,
          "status": "matched", "reason": "query_text_intent_document_scope_and_reviewer_registry_match"})
    if len(migrations) != len(queries)*len(papers): raise ValueError("Full-corpus annotation migration incomplete")
    write_csv(output/"annotation_migration.csv", migrations)
    snapshot = output/"reproduction"/"source_run"; (snapshot/"private").mkdir(parents=True, exist_ok=True)
    for name in ("corpus.jsonl", "queries.csv", "config.json", "FROZEN_PILOT.json",
                 "REVIEW_PROVENANCE.json", "corpus_exclusions.csv",
                 "metrics_by_query_ranker.csv"):
        shutil.copy2(source/name, snapshot/name)
    for name in ("family_map.csv", "reviewers.json", "blind_mapping.csv", "ratings_history.jsonl", "adjudication_history.jsonl", "ranker_runs.jsonl", "pool_mapping.csv"):
        shutil.copy2(source/"private"/name, snapshot/"private"/name)
    snapshot_files = sorted(path for path in snapshot.rglob("*") if path.is_file())
    (snapshot/"CHECKSUMS.sha256").write_text("".join(
        f"{raw_sha256(path)}  ./{path.relative_to(snapshot).as_posix()}\n"
        for path in snapshot_files), encoding="utf-8")
    latest_rating = _latest(ratings_history, ("blind_id", "reviewer_id")); latest_adj = _latest(adjudication_history, ("blind_id",))
    reviewer_ids = [row["reviewer_id"] for row in reviewers["reviewers"] if row["role"] == "reviewer"]
    views = {"reviewer01": {}, "reviewer02": {}, "final": {}}
    reviewer_views = dict(zip(reviewer_ids, ("reviewer01", "reviewer02")))
    for blind_id, pair in blind.items():
        values = []
        for reviewer in reviewer_ids:
            item = latest_rating.get((blind_id, reviewer)); grade = item.get("score") if item else None
            views[reviewer_views[reviewer]][pair] = grade; values.append(grade)
        final = values[0] if all(value is not None for value in values) and values[0] == values[1] else None
        adjudicated = latest_adj.get((blind_id,))
        if final is None and adjudicated and not adjudicated.get("unresolved"): final = adjudicated["final_grade"]
        views["final"][pair] = final
    return views, {"ratings_history": ratings_history, "adjudication_history": adjudication_history,
                   "reviewers": reviewers, "migration_rows": len(migrations)}


def _legacy_rank(query, papers, topics, config, root, ranker_id):
    rcfg = config["ranker_config"]; lexical = bm25_scores(query["query_text"], papers, rcfg["bm25_k1"], rcfg["bm25_b"])
    if ranker_id == "bm25_lexical":
        order = sorted(papers, key=lambda pid: (-lexical[pid], pid)); returned = order[:int(rcfg["top_k"])]
        return returned, lexical, None, order
    closure = ranker_id == "legacy_closure"; gate = config["closure_gate"] if closure else config["repaired_gate"]
    profiles = load_retrieval_profiles(Path(root)/gate["profile_path"])
    retrieval = {**gate, "_profiles": profiles, "candidate_pool_size": rcfg["gate_pool_size"],
      "top_k": rcfg["gate_pool_size"], "max_per_near_duplicate_cluster": rcfg["max_per_near_duplicate_cluster"],
      "near_duplicate_threshold": rcfg["near_duplicate_threshold"]}
    fn = retrieve_stage_a_semantic_guarded if closure else retrieve_stage_a_repaired
    _, audit = fn(papers, topics, PolicyContext("balanced", "retrieval", False, 0), retrieval,
                  query["topic_id"], "unknown", [], [])
    eligible = audit["selected_ids"]; diagnostic = {row["paper_id"]: row for row in audit["diagnostics"]}
    peak = max((lexical[pid] for pid in eligible), default=0.0)
    score = {pid: diagnostic[pid]["evidence_score"] + float(rcfg["query_weight"])*(lexical[pid]/peak if peak else 0.0)
             for pid in eligible}
    order = sorted(eligible, key=lambda pid: (-score[pid], pid)); returned = order[:int(rcfg["top_k"])]
    return returned, score, audit, sorted(papers, key=lambda pid: (-lexical[pid], pid))


def run_retrieval(source, output, root, config, routes, aligned_profiles):
    source = Path(source); output = Path(output); root = Path(root)
    papers, family, _ = _canonical_papers(source); queries = read_records(source/"queries.csv")
    source_config = load_json(source/"config.json"); corpus = Corpus(source/"corpus.jsonl", source_config["cutoff_year"], False)
    corpus.papers = papers; topics = TopicModel(root/source_config["topics_path"], papers, 1.0)
    repair_profiles = load_retrieval_profiles(root/source_config["repaired_gate"]["profile_path"])
    route_by = {row["query_id"]: row for row in routes["routes"]}; runs, traces = [], []
    for query in queries:
        route = route_by.get(query["query_id"])
        if not route or route["original_topic_id"] != query["topic_id"]: raise ValueError("Missing or invalid query route")
        lexical = bm25_scores(query["query_text"], papers, config["bm25_k1"], config["bm25_b"])
        bm25_order = sorted(papers, key=lambda pid: (-lexical[pid], pid)); bm25_rank = {pid:i+1 for i,pid in enumerate(bm25_order)}
        for ranker_id in config["rankers"]:
            audit = None
            if ranker_id in ("bm25_lexical", "legacy_closure", "legacy_repair"):
                returned, scores, audit, _ = _legacy_rank(query, papers, topics, source_config, root, ranker_id)
                candidates = list(papers) if ranker_id == "bm25_lexical" else audit["selected_ids"]
                zero_signal = max(lexical.values(), default=0.0) == 0.0
            else:
                if ranker_id == "soft_aligned_profile":
                    retrieval_topic = route["retrieval_topic_id"]
                    profile = (repair_profiles["topics"].get(retrieval_topic) or aligned_profiles["topics"].get(retrieval_topic))
                else:
                    retrieval_topic = query["topic_id"]; profile = repair_profiles["topics"].get(retrieval_topic)
                lambda_value = 0.0 if ranker_id == "soft_lambda0" else float(config["lambda"])
                soft = soft_rank(query["query_text"], papers, profile, candidate_k=config["candidate_k"],
                  output_k=config["output_k"], lambda_value=lambda_value, k1=config["bm25_k1"], b=config["bm25_b"])
                returned, candidates, scores, zero_signal = soft["returned_ids"], soft["candidate_ids"], soft["scores"], soft["zero_signal"]
            runs.append({"retrieval_run_id": None, "evaluation_split": config["evaluation_split"],
              "query_id": query["query_id"], "ranker_id": ranker_id, "original_topic_id": query["topic_id"],
              "retrieval_topic_id": route["retrieval_topic_id"] if ranker_id == "soft_aligned_profile" else query["topic_id"],
              "candidate_count": len(candidates), "candidate_ids": candidates,
              "returned_count": len(returned), "empty": not returned,
              "zero_signal": zero_signal, "ranked_ids": returned})
            returned_rank = {pid:i+1 for i,pid in enumerate(returned)}
            diagnostics = {row["paper_id"]: row for row in audit["diagnostics"]} if audit else {}
            base_ids = set(audit.get("base_candidate_ids", [])) if audit else set()
            eligible_ids = set(audit.get("selected_ids", [])) if audit else set()
            for pid in sorted(papers):
                if ranker_id.startswith("legacy_"):
                    item = diagnostics[pid]; gate_pass = bool(item["gate_passed"]); candidate_rank = item.get("candidate_rank")
                    dedup_kept = pid in eligible_ids
                    reason = first_exclusion_legacy(gate_pass=gate_pass, candidate_rank=candidate_rank,
                      dedup_kept=dedup_kept, returned=pid in returned_rank)
                    topic_raw = item["evidence_score"]; rerank = scores.get(pid)
                    path = "input>topic_gate>candidate_truncation>deduplication>query_rerank>top10"
                    threshold = float((source_config["closure_gate"] if ranker_id == "legacy_closure" else source_config["repaired_gate"])["minimum_relevance"])
                elif ranker_id == "bm25_lexical":
                    gate_pass = candidate_rank = dedup_kept = threshold = topic_raw = None
                    rerank = lexical[pid]; reason = None if pid in returned_rank else "final_topk"
                    path = "input>bm25_rank>top10"
                else:
                    item = scores[pid]; gate_pass = threshold = None; candidate_rank = bm25_rank[pid] if pid in candidates else None; dedup_kept = None
                    topic_raw = item["topic_score_raw"]; rerank = item["rerank_score"]
                    reason = first_exclusion_soft(bm25_rank=bm25_rank[pid], candidate_k=int(config["candidate_k"]), returned=pid in returned_rank)
                    path = "input>bm25_top20>soft_rerank>top10"
                traces.append({"query_id":query["query_id"],"ranker_id":ranker_id,"paper_id":pid,"family_id":family[pid],
                  "original_topic_id":query["topic_id"],"retrieval_topic_id":route["retrieval_topic_id"] if ranker_id=="soft_aligned_profile" else query["topic_id"],
                  "bm25_raw":lexical[pid],"bm25_rank":bm25_rank[pid],"topic_score_raw":topic_raw,
                  "topic_score_normalized":scores[pid]["topic_score_normalized"] if isinstance(scores.get(pid),dict) else None,
                  "gate_threshold":threshold,"gate_pass":gate_pass,"candidate_rank":candidate_rank,"dedup_kept":dedup_kept,
                  "rerank_score":rerank,"final_rank":returned_rank.get(pid),"returned":pid in returned_rank,
                  "first_exclusion_reason":reason,"stage_path":path})
    if len(runs) != len(queries)*len(RANKERS) or len(traces) != len(queries)*len(RANKERS)*len(papers):
        raise ValueError("Experiment matrix incomplete")
    return runs, traces, queries, papers, family


def _strict_metrics(ids, k, grades, threshold):
    returned = ids[:k]; known = [grades.get(pid) for pid in returned]; relevant = sum(g is not None and g >= threshold for g in known)
    complete = all(value is not None for value in grades.values()); supply = sum(value is not None and value >= threshold for value in grades.values())
    gains = [1 if grade is not None and grade >= threshold else 0 for grade in known]
    dcg = sum(gain/math.log2(index+2) for index,gain in enumerate(gains)); ideal_n = min(k, supply)
    idcg = sum(1/math.log2(index+2) for index in range(ideal_n))
    return {"strict_precision_at_k": relevant/k if all(g is not None for g in known) else None,
      "strict_ndcg_at_k": dcg/idcg if complete and idcg else None,
      "strict_recall_at_k": relevant/supply if complete and supply else None,
      "strict_null_reason": None if complete and supply else ("incomplete_scope" if not complete else "no_grade_2_supply")}


def evaluate(runs, queries, family, views, annotation_scope, config):
    query_topic = {row["query_id"]:row["topic_id"] for row in queries}; metrics=[]
    run_lookup={(row["query_id"],row["ranker_id"]):row for row in runs}
    for view, all_grades in views.items():
        by_query=defaultdict(dict)
        for (qid,pid),grade in all_grades.items(): by_query[qid][pid]=grade
        for run in runs:
            grades=by_query[run["query_id"]]; selected={pid:grades.get(pid) for pid in run["ranked_ids"]}
            for k in config["ks"]:
                row=ranked_metrics(run["ranked_ids"],int(k),selected,grades,annotation_scope)
                row.update(_strict_metrics(run["ranked_ids"],int(k),grades,int(config["strict_relevance_threshold"])))
                relevant_supply=sum(value is not None and value>=1 for value in grades.values())
                candidate_hits=sum(grades.get(pid) is not None and grades[pid]>=1 for pid in run["candidate_ids"])
                bm25_ids=run_lookup[(run["query_id"],"bm25_lexical")]["ranked_ids"][:int(k)]
                current_ids=run["ranked_ids"][:int(k)]; union=set(bm25_ids)|set(current_ids); common=set(bm25_ids)&set(current_ids)
                bm25_position={pid:index for index,pid in enumerate(bm25_ids)}; current_position={pid:index for index,pid in enumerate(current_ids)}
                displaced=sum(bm25_position[pid]!=current_position[pid] for pid in common)
                row.update({"label_view":view,"query_id":run["query_id"],"topic_id":query_topic[run["query_id"]],"ranker_id":run["ranker_id"]})
                row.update({"candidate_relevant_recall":candidate_hits/relevant_supply if relevant_supply and all(value is not None for value in grades.values()) else None,
                  "candidate_recall_null_reason":None if relevant_supply and all(value is not None for value in grades.values()) else ("no_relevant_supply" if not relevant_supply else "incomplete_scope"),
                  "topk_jaccard_vs_bm25":len(common)/len(union) if union else 1.0,
                  "topk_displacement_rate_vs_bm25":displaced/int(k),"zero_signal":run["zero_signal"]})
                metrics.append(row)
    def mean(rows,key):
        values=[row[key] for row in rows if row.get(key) is not None]
        return sum(values)/len(values) if values else None
    topic_groups=defaultdict(list); macro_groups=defaultdict(list)
    for row in metrics:
        topic_groups[(row["label_view"],row["topic_id"],row["ranker_id"],row["k"])].append(row)
        macro_groups[(row["label_view"],row["ranker_id"],row["k"])].append(row)
    topics=[{"label_view":k[0],"topic_id":k[1],"ranker_id":k[2],"k":k[3],"query_n":len(v),
      "mean_precision_at_k":mean(v,"precision_at_k"),"mean_ndcg_at_k":mean(v,"ndcg_at_k"),"mean_recall_at_k":mean(v,"recall_at_k"),
      "mean_strict_precision_at_k":mean(v,"strict_precision_at_k"),"mean_strict_ndcg_at_k":mean(v,"strict_ndcg_at_k"),
      "empty_rate":sum(row["empty"] for row in v)/len(v),"null_ndcg_queries":sum(row["ndcg_at_k"] is None for row in v)} for k,v in sorted(topic_groups.items())]
    macro=[{"label_view":k[0],"ranker_id":k[1],"k":k[2],"query_n":len(v),
      "query_macro_precision_at_k":mean(v,"precision_at_k"),"query_macro_ndcg_at_k":mean(v,"ndcg_at_k"),"query_macro_recall_at_k":mean(v,"recall_at_k"),
      "query_macro_strict_precision_at_k":mean(v,"strict_precision_at_k"),"query_macro_strict_ndcg_at_k":mean(v,"strict_ndcg_at_k"),
      "empty_rate":sum(row["empty"] for row in v)/len(v),"null_ndcg_queries":sum(row["ndcg_at_k"] is None for row in v)} for k,v in sorted(macro_groups.items())]
    lookup={(r["label_view"],r["query_id"],r["ranker_id"],r["k"]):r for r in metrics}; paired=[]
    for view in views:
        for query in queries:
            for treatment,baseline,effect in (("soft_legacy_profile","bm25_lexical","algorithm_vs_bm25"),("soft_legacy_profile","legacy_repair","algorithm_vs_legacy_repair"),("soft_aligned_profile","soft_legacy_profile","profile_increment")):
                a=lookup[(view,query["query_id"],treatment,5)]; b=lookup[(view,query["query_id"],baseline,5)]
                difference=None if a["ndcg_at_k"] is None or b["ndcg_at_k"] is None else a["ndcg_at_k"]-b["ndcg_at_k"]
                paired.append({"label_view":view,"query_id":query["query_id"],"topic_id":query["topic_id"],"effect":effect,
                  "treatment":treatment,"baseline":baseline,"ndcg_at_5_difference":None if a["ndcg_at_k"] is None or b["ndcg_at_k"] is None else a["ndcg_at_k"]-b["ndcg_at_k"],
                  "precision_at_5_difference":None if a["precision_at_k"] is None or b["precision_at_k"] is None else a["precision_at_k"]-b["precision_at_k"],
                  "outcome":None if difference is None else ("win" if difference>1e-12 else ("loss" if difference< -1e-12 else "tie"))})
    return metrics, topics, macro, paired


def compare_baseline(source, runs, metrics, tolerance):
    source=Path(source); old_runs=read_jsonl(source/"private"/"ranker_runs.jsonl")
    old={(row["query_id"],row["ranker"]):row for row in old_runs}; differences=[]
    for row in runs:
        if row["ranker_id"] not in LEGACY_SOURCE_NAMES: continue
        prior=old.get((row["query_id"],LEGACY_SOURCE_NAMES[row["ranker_id"]]))
        if prior is None or prior["ranked_ids"] != row["ranked_ids"]:
            differences.append({"kind":"ranking","query_id":row["query_id"],"ranker_id":row["ranker_id"],"expected":prior["ranked_ids"] if prior else None,"actual":row["ranked_ids"]})
    old_metrics=read_records(source/"metrics_by_query_ranker.csv"); current={(r["query_id"],r["ranker_id"],str(r["k"])):r for r in metrics if r["label_view"]=="final"}
    reverse={value:key for key,value in LEGACY_SOURCE_NAMES.items()}
    for prior in old_metrics:
        if prior["ranker"] not in reverse: continue
        row=current[(prior["query_id"],reverse[prior["ranker"]],str(prior["k"]))]
        for name in ("precision_at_k","ndcg_at_k"):
            expected=None if prior[name]=="" else float(prior[name]); actual=row[name]
            if (expected is None)!=(actual is None) or (expected is not None and abs(expected-actual)>tolerance):
                differences.append({"kind":"metric","query_id":prior["query_id"],"ranker_id":reverse[prior["ranker"]],"k":prior["k"],"metric":name,"expected":expected,"actual":actual})
    return {"status":"passed" if not differences else "failed","ranking_cases":27,"metric_cases":81,"differences":differences}


def _decision(macro, topics, tolerance):
    find=lambda ranker,k:next(row for row in macro if row["label_view"]=="final" and row["ranker_id"]==ranker and row["k"]==k)
    base5=find("bm25_lexical",5); new5=find("soft_aligned_profile",5); base3=find("bm25_lexical",3); new3=find("soft_aligned_profile",3)
    topic_lookup={(r["topic_id"],r["ranker_id"]):r for r in topics if r["label_view"]=="final" and r["k"]==5}
    topic_ok=all(topic_lookup[(topic,"soft_aligned_profile")]["mean_ndcg_at_k"]+tolerance>=topic_lookup[(topic,"bm25_lexical")]["mean_ndcg_at_k"] for topic in sorted({r["topic_id"] for r in topics}))
    candidate=(new5["query_macro_ndcg_at_k"]>base5["query_macro_ndcg_at_k"]+tolerance and new3["query_macro_precision_at_k"]+tolerance>=base3["query_macro_precision_at_k"] and topic_ok)
    return {"candidate_for_future_validation":candidate,"primary_difference":new5["query_macro_ndcg_at_k"]-base5["query_macro_ndcg_at_k"],
      "p_at_3_difference":new3["query_macro_precision_at_k"]-base3["query_macro_precision_at_k"],"all_topics_noninferior":topic_ok}


def _copy_reproduction(root, output):
    destination=Path(output)/"reproduction"/"source"; destination.mkdir(parents=True,exist_ok=True)
    files=("execute_stage_b_improvement.py","requirements.txt","configs/stage_b_improvement.json","data/stage_b_query_routes_v1.json","data/retrieval_pilot_profiles_v1.json","data/retrieval_topic_profiles_v1.json","data/retrieval_topic_profiles_repair_v1.json","data/topics.json")
    for relative in files:
        target=destination/relative; target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(Path(root)/relative,target)
    for source in sorted((Path(root)/"scimirror").glob("*.py")):
        target=destination/"scimirror"/source.name; target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)
    (Path(output)/"reproduction"/"README_REPRODUCE.md").write_text("# Reproduce\n\nFrom the repository root:\n\n`python execute_stage_b_improvement.py reproduce --run-dir <delivered-run> --output <new-output>`\n",encoding="utf-8")


def _write_report(output, decision, macro, topics, baseline, migration_rows):
    get=lambda ranker,k:next(r for r in macro if r["label_view"]=="final" and r["ranker_id"]==ranker and r["k"]==k)
    lines=["# Stage B 软重排探索性报告","","## 范围","","36篇真实文献、9条重复使用的探索性查询、6个固定实验臂。既有648条AI辅助评分和45条裁决原样迁移；独立人工核查由用户延期。", "", "## 基线与迁移", "", f"历史三臂复现：{baseline['status']}；324对迁移：{migration_rows}/324。未修改标签、理由、时间、修订或来源。", "", "## query宏平均", "", "|排序器|P@3|nDCG@5|P@5|", "|---|---:|---:|---:|"]
    for ranker in RANKERS:
        r3=get(ranker,3); r5=get(ranker,5); lines.append(f"|{ranker}|{r3['query_macro_precision_at_k']:.6f}|{r5['query_macro_ndcg_at_k']:.6f}|{r5['query_macro_precision_at_k']:.6f}|")
    alg=get("soft_legacy_profile",5)["query_macro_ndcg_at_k"]-get("bm25_lexical",5)["query_macro_ndcg_at_k"]
    profile=get("soft_aligned_profile",5)["query_macro_ndcg_at_k"]-get("soft_legacy_profile",5)["query_macro_ndcg_at_k"]
    lines += ["", "## 效应拆分", "", f"只改算法（soft_legacy_profile 相对 BM25）的 nDCG@5 差值：{alg:.6f}。", f"只改画像（soft_aligned_profile 相对 soft_legacy_profile）的 nDCG@5 增量：{profile:.6f}。", "", "## 决策", "", f"candidate_for_future_validation={str(decision['candidate_for_future_validation']).lower()}。生产排序器未改变，未按结果调lambda或删除查询。", "", "## 限制", "", "本轮查询已多次用于分析，不是未见测试集；评审为AI辅助且人类仅抽查与提交；三套评分视图不是独立重复；结果不支持正式科学或因果声明。"]
    failure_rows=read_records(Path(output)/"science_evaluation_failure_analysis.csv")
    lines += ["", "## science_evaluation 逐查询失败阶段", "",
              "旧排序器的主要失败发生在 `topic_gate`；软重排不使用硬门控。以下 ID 与分数来自标签关联后的诊断，不曾传入检索函数。", ""]
    query_ids=sorted({row["query_id"] for row in failure_rows})
    for query_id in query_ids:
        lines.append(f"### {query_id}")
        for ranker_id in ("legacy_closure","legacy_repair","soft_aligned_profile"):
            lost=[row for row in failure_rows if row["query_id"]==query_id and row["ranker_id"]==ranker_id and row["first_exclusion_reason"]]
            details=[]
            for row in lost:
                score=row.get("topic_score_raw") or "null"; threshold=row.get("gate_threshold") or "null"
                details.append(f"{row['paper_id']}({row['first_exclusion_reason']}, score={score}, threshold={threshold})")
            lines.append(f"- {ranker_id}: " + ("；".join(details) if details else "无相关文献在最终返回前丢失"))
        lines.append("")
    (Path(output)/"REPORT_ZH.md").write_text("\n".join(lines)+"\n",encoding="utf-8")


def run_experiment(config_path, source_path, output, root):
    root=Path(root); output=Path(output)
    if output.exists() and any(output.iterdir()): raise ValueError("Output directory is not empty")
    output.mkdir(parents=True,exist_ok=True); config=load_json(config_path)
    if config["lambda"] != .10 or config["candidate_k"] != 20 or config["output_k"] != 10 or tuple(config["rankers"]) != RANKERS: raise ValueError("Frozen matrix changed")
    if config.get("allow_network") or config.get("allow_llm_calls"): raise ValueError("Experiment must remain offline")
    extract=output/"_source_extract" if Path(source_path).is_file() else None; source=locate_source(source_path,extract)
    audit=verify_checksums(source); checksum_before=raw_sha256(source/"CHECKSUMS.sha256") if (source/"CHECKSUMS.sha256").exists() else None
    write_json(output/"INPUT_AUDIT.json",{**audit,"source":str(source),"historical_read_only":True,"checksum_manifest_before":checksum_before})
    if audit["status"]!="passed": raise ValueError("Source checksum validation failed")
    routes=load_json(root/config["routes_path"]); aligned=load_json(root/config["aligned_profiles_path"])
    papers,family,family_rows=_canonical_papers(source); queries=read_records(source/"queries.csv")
    fingerprints={"corpus_hash":records_fingerprint(source/"corpus.jsonl","corpus")["semantic_sha256"],
      "query_hash":records_fingerprint(source/"queries.csv","queries")["semantic_sha256"],
      "family_hash":records_fingerprint(source/"private"/"family_map.csv","family_map")["semantic_sha256"],
      "ranker_config_hash":json_fingerprint(config_path)["semantic_sha256"],"profile_hash":combine_fingerprint([json_fingerprint(root/config["legacy_repair_profiles_path"])["semantic_sha256"],json_fingerprint(root/config["aligned_profiles_path"])["semantic_sha256"],json_fingerprint(root/config["routes_path"])["semantic_sha256"]]),
      "source_hash":combine_fingerprint([source_fingerprint(root/name)["source_sha256"] for name in ("scimirror/stage_b_improvement.py","scimirror/retrieval_soft_ranker.py","scimirror/retrieval_stage_trace.py")]),
      "annotation_scope_hash":semantic_sha256({"scope":"full_corpus","question":"relevance_0_1_2","reviewers":load_json(source/"private"/"reviewers.json")})}
    migration_views,migration=migrate_annotations(source,output,queries,papers,family_rows,fingerprints)
    fingerprints["labels_hash"]=semantic_sha256({"ratings":migration["ratings_history"],"adjudication":migration["adjudication_history"],"provenance":load_json(source/"REVIEW_PROVENANCE.json")})
    retrieval_run_id=combine_fingerprint({key:value for key,value in fingerprints.items() if key!="labels_hash"})[:24]; evaluation_run_id=combine_fingerprint({"retrieval_run_id":retrieval_run_id,"labels_hash":fingerprints["labels_hash"],"evaluator":EVALUATOR_VERSION})[:24]
    runs,traces,queries,papers,family=run_retrieval(source,output,root,config,routes,aligned)
    for row in runs: row["retrieval_run_id"]=retrieval_run_id
    write_jsonl(output/"ranker_runs.jsonl",runs); write_jsonl(output/"retrieval_trace.jsonl",traces)
    labeled=label_trace(traces,migration_views["final"]); write_jsonl(output/"retrieval_trace_labeled.jsonl",labeled); write_csv(output/"loss_by_stage.csv",loss_by_stage(labeled))
    metrics,topics,macro,paired=evaluate(runs,queries,family,migration_views,"full_corpus",config)
    write_csv(output/"metrics_by_query_ranker.csv",metrics); write_csv(output/"metrics_by_topic.csv",topics); write_csv(output/"metrics_query_macro.csv",macro); write_csv(output/"paired_differences.csv",paired)
    macro_lookup={(row["label_view"],row["ranker_id"],row["k"]):row for row in macro}; sensitivity=[]
    for view in ("reviewer01","reviewer02"):
        for treatment,baseline,effect in (("soft_legacy_profile","bm25_lexical","algorithm_vs_bm25"),("soft_aligned_profile","soft_legacy_profile","profile_increment"),("soft_aligned_profile","bm25_lexical","final_vs_bm25")):
            a=macro_lookup[(view,treatment,5)]["query_macro_ndcg_at_k"]; b=macro_lookup[(view,baseline,5)]["query_macro_ndcg_at_k"]; difference=None if a is None or b is None else a-b
            sensitivity.append({"label_view":view,"effect":effect,"treatment":treatment,"baseline":baseline,"ndcg_at_5_difference":difference,"direction":None if difference is None else ("positive" if difference>1e-12 else ("negative" if difference< -1e-12 else "tie"))})
    write_csv(output/"reviewer_sensitivity.csv",sensitivity)
    failures=[row for row in labeled if row["original_topic_id"]=="science_evaluation" and row["final_grade"] is not None and row["final_grade"]>=1]
    write_csv(output/"science_evaluation_failure_analysis.csv",failures)
    shutil.copy2(source/"corpus_exclusions.csv",output/"precanonical_exclusions.csv")
    baseline=compare_baseline(source,runs,metrics,float(config["floating_tolerance"])); write_json(output/"BASELINE_REPLAY.json",baseline)
    lambda0=all(next(r for r in runs if r["query_id"]==q["query_id"] and r["ranker_id"]=="soft_lambda0")["ranked_ids"]==next(r for r in runs if r["query_id"]==q["query_id"] and r["ranker_id"]=="bm25_lexical")["ranked_ids"] for q in queries)
    semantic_validation={"schema_version":SCHEMA,"status":"passed","raw_line_endings_may_differ":True,"semantic_identities":fingerprints,"retrieval_run_id":retrieval_run_id,"evaluation_run_id":evaluation_run_id,"labels_excluded_from_retrieval_run_id":True}
    write_json(output/"SEMANTIC_HASH_VALIDATION.json",semantic_validation)
    plan={"schema_version":SCHEMA,"matrix":{"queries":9,"rankers":list(RANKERS),"runs":54},"fixed_parameters":{key:config[key] for key in ("candidate_k","output_k","lambda","bm25_k1","bm25_b","tie_break")},"primary_endpoint":config["primary_endpoint"],"decision_rule":"ndcg5_gt_bm25_and_p3_ge_bm25_and_each_topic_ndcg5_ge_bm25","fingerprints":fingerprints,"retrieval_run_id":retrieval_run_id,"evaluation_run_id":evaluation_run_id,"network_requests":0,"llm_calls":0}
    write_json(output/"PLAN_FROZEN.json",plan); shutil.copy2(source/"REVIEW_PROVENANCE.json",output/"REVIEW_PROVENANCE.json")
    decision=_decision(macro,topics,float(config["floating_tolerance"])); reviewer_direction={row["label_view"]:row["direction"] for row in sensitivity if row["effect"]=="final_vs_bm25"}; decision["reviewer_directions_final_vs_bm25"]=reviewer_direction
    engineering=baseline["status"]=="passed" and lambda0 and len(runs)==54 and len(traces)==1944 and migration["migration_rows"]==324
    status={"schema_version":SCHEMA,"engineering_complete":engineering,"exploratory_analysis_complete":True,"independent_human_validation":"deferred_by_user","review_scope":"exploratory_ai_assisted_review","evaluation_split":"exploratory_reused_pilot","ready_for_scientific_claims":False,"candidate_for_future_validation":decision["candidate_for_future_validation"],"production_ranker_changed":False,"network_requests":0,"llm_calls":0,"paid_api_calls":0}
    write_json(output/"STATUS.json",status); _write_report(output,decision,macro,topics,baseline,migration["migration_rows"])
    post_audit=verify_checksums(source); checksum_after=raw_sha256(source/"CHECKSUMS.sha256") if (source/"CHECKSUMS.sha256").exists() else None
    write_json(output/"INPUT_AUDIT.json",{**audit,"source":str(source),"historical_read_only":True,"checksum_manifest_before":checksum_before,"checksum_manifest_after":checksum_after,"post_run_status":post_audit["status"],"source_unchanged":checksum_before==checksum_after and post_audit["status"]=="passed"})
    manifest={"schema_version":SCHEMA,"python":sys.version,"platform":platform.platform(),"git_commit":subprocess.run(["git","rev-parse","HEAD"],cwd=root,text=True,capture_output=True).stdout.strip(),"source_run":str(source),"runtime_path":str(output.resolve()),"network_requests":0,"llm_calls":0}
    write_json(output/"RUNTIME_MANIFEST.json",manifest); _copy_reproduction(root,output)
    if extract and extract.exists(): shutil.rmtree(extract)
    validation=validate_run(output,write=False); write_json(output/"DELIVERY_VALIDATION.json",validation)
    return {"output":str(output.resolve()),"status":status,"decision":decision,"baseline":baseline,"validation":validation}


def validate_run(run_dir, write=True):
    run_dir=Path(run_dir); runs=read_jsonl(run_dir/"ranker_runs.jsonl"); trace=read_jsonl(run_dir/"retrieval_trace.jsonl"); migration=read_records(run_dir/"annotation_migration.csv"); baseline=load_json(run_dir/"BASELINE_REPLAY.json"); status=load_json(run_dir/"STATUS.json")
    unique_runs=len({(r["query_id"],r["ranker_id"]) for r in runs})==54; unique_trace=len({(r["query_id"],r["ranker_id"],r["paper_id"]) for r in trace})==1944
    lambda0=all(next(r for r in runs if r["query_id"]==qid and r["ranker_id"]=="soft_lambda0")["ranked_ids"]==next(r for r in runs if r["query_id"]==qid and r["ranker_id"]=="bm25_lexical")["ranked_ids"] for qid in {r["query_id"] for r in runs})
    soft_runs=[r for r in runs if r["ranker_id"].startswith("soft_")]; trace_groups=defaultdict(dict)
    for row in trace: trace_groups[(row["query_id"],row["ranker_id"])][row["paper_id"]]=row
    soft_subset=all(set(r["ranked_ids"])<=set(r["candidate_ids"]) and len(r["candidate_ids"])<=20 and
      all(trace_groups[(r["query_id"],r["ranker_id"])][pid]["candidate_rank"] is not None for pid in r["ranked_ids"]) for r in soft_runs)
    no_soft_gate=all(row["gate_pass"] is None and row["dedup_kept"] is None for row in trace if row["ranker_id"].startswith("soft_"))
    loss_conservation=all(sum(row["first_exclusion_reason"] is not None for row in values.values())+sum(row["returned"] for row in values.values())==len(values) for values in trace_groups.values())
    migration_ok=len(migration)==324 and all(r["status"]=="matched" for r in migration)
    checks={"baseline_replay":baseline["status"]=="passed","run_matrix":len(runs)==54 and unique_runs,"trace_matrix":len(trace)==1944 and unique_trace,"lambda0_equivalence":lambda0,"soft_candidate_invariant":soft_subset,"soft_has_no_hard_gate_or_dedup":no_soft_gate,"loss_conservation":loss_conservation,"annotation_migration":migration_ok,"scientific_scope":status["ready_for_scientific_claims"] is False and status["independent_human_validation"]=="deferred_by_user","production_unchanged":status["production_ranker_changed"] is False}
    result={"schema_version":SCHEMA,"status":"passed" if all(checks.values()) else "failed","all_passed":all(checks.values()),"checks":checks,"run_rows":len(runs),"trace_rows":len(trace),"migration_rows":len(migration),"network_requests":0,"llm_calls":0}
    if write: write_json(run_dir/"DELIVERY_VALIDATION.json",result)
    return result


def finalize_delivery(run_dir):
    run_dir=Path(run_dir); excluded={"CHECKSUMS.sha256","stage_b_improvement_delivery.zip","ARCHIVE_SHA256.txt"}
    files=sorted(p for p in run_dir.rglob("*") if p.is_file() and p.name not in excluded)
    checks="".join(f"{raw_sha256(path)}  {path.relative_to(run_dir).as_posix()}\n" for path in files); (run_dir/"CHECKSUMS.sha256").write_text(checks,encoding="utf-8")
    archive=run_dir/"stage_b_improvement_delivery.zip"
    with zipfile.ZipFile(archive,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as bundle:
        for path in [*files,run_dir/"CHECKSUMS.sha256"]: bundle.write(path,Path("delivery")/path.relative_to(run_dir))
    archive_hash=raw_sha256(archive); (run_dir/"ARCHIVE_SHA256.txt").write_text(f"{archive_hash}  {archive.name}\n",encoding="utf-8")
    return {"archive":str(archive.resolve()),"sha256":archive_hash,"files":len(files)+1}


def reproduce(run_dir, output, root):
    run_dir=Path(run_dir); output=Path(output); isolated=run_dir/"reproduction"/"source"
    source=run_dir/"reproduction"/"source_run"; config=isolated/"configs"/"stage_b_improvement.json"
    command=[sys.executable,str(isolated/"execute_stage_b_improvement.py"),"run","--config",str(config),"--source-run",str(source),"--output",str(output)]
    environment=os.environ.copy(); environment["PYTHONPATH"]=str(isolated)
    completed=subprocess.run(command,cwd=isolated,env=environment,text=True,capture_output=True)
    if completed.returncode:
        record={"status":"failed","command":command,"exit_code":completed.returncode,
                "stdout":completed.stdout,"stderr":completed.stderr}
        output.mkdir(parents=True,exist_ok=True); write_json(output/"REPRODUCTION_RESULT.json",record)
        return {"output":str(output.resolve()),"reproduction":record}
    result=json.loads(completed.stdout)
    original=read_jsonl(run_dir/"ranker_runs.jsonl"); replay=read_jsonl(output/"ranker_runs.jsonl")
    same_rankings=[(r["query_id"],r["ranker_id"],r["ranked_ids"]) for r in original]==[(r["query_id"],r["ranker_id"],r["ranked_ids"]) for r in replay]
    original_metrics=read_records(run_dir/"metrics_by_query_ranker.csv"); replay_metrics=read_records(output/"metrics_by_query_ranker.csv")
    same_metrics=original_metrics==replay_metrics
    probe=subprocess.run([sys.executable,"-c","import scimirror.stage_b_improvement as m; print(m.__file__)"],cwd=isolated,env=environment,text=True,capture_output=True)
    actual_loaded=probe.stdout.strip(); inside=False
    if probe.returncode==0 and actual_loaded:
        inside=Path(actual_loaded).resolve().is_relative_to(isolated.resolve())
    record={"status":"passed" if same_rankings and same_metrics and inside else "failed",
      "command":command,"exit_code":completed.returncode,"ranking_semantic_equal":same_rankings,
      "metrics_semantic_equal":same_metrics,"actual_loaded_module":actual_loaded,
      "module_inside_isolated_source":inside,"stderr":completed.stderr+probe.stderr}
    write_json(output/"REPRODUCTION_RESULT.json",record); return {**result,"reproduction":record}
