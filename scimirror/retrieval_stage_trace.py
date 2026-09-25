"""Stage-level trace and post-hoc loss attribution for Stage B retrieval."""
from __future__ import annotations

from collections import defaultdict


STAGES = ("input", "topic_gate", "candidate_truncation", "deduplication", "final_topk")


def first_exclusion_legacy(*, gate_pass, candidate_rank, dedup_kept, returned):
    if not gate_pass:
        return "topic_gate"
    if candidate_rank is None:
        return "candidate_truncation"
    if not dedup_kept:
        return "deduplication"
    if not returned:
        return "final_topk"
    return None


def first_exclusion_soft(*, bm25_rank, candidate_k, returned):
    if bm25_rank is None or bm25_rank > candidate_k:
        return "candidate_truncation"
    if not returned:
        return "final_topk"
    return None


def label_trace(trace_rows, grades):
    result = []
    for row in trace_rows:
        grade = grades.get((row["query_id"], row["paper_id"]))
        result.append({**row, "final_grade": grade,
                       "relevant_grade_ge_1": None if grade is None else grade >= 1,
                       "strict_relevant_grade_2": None if grade is None else grade >= 2})
    return result


def loss_by_stage(labeled_rows):
    """Aggregate mutually exclusive first losses with explicit null denominators."""
    grouped = defaultdict(list)
    for row in labeled_rows:
        grouped[(row["query_id"], row["ranker_id"])].append(row)
    output = []
    for (query_id, ranker_id), rows in sorted(grouped.items()):
        known = [row for row in rows if row["final_grade"] is not None]
        relevant = [row for row in known if row["final_grade"] >= 1]
        retained_ids = {row["paper_id"] for row in rows if row["returned"]}
        lost_so_far = set()
        for stage in STAGES:
            if stage == "input":
                lost = []
            elif stage == "final_topk":
                lost = [row for row in rows if row["first_exclusion_reason"] == stage]
            else:
                lost = [row for row in rows if row["first_exclusion_reason"] == stage]
            lost_so_far.update(row["paper_id"] for row in lost)
            entering = [row for row in rows if row["paper_id"] not in lost_so_far or row in lost]
            entering_rel = [row for row in entering if row["final_grade"] is not None and row["final_grade"] >= 1]
            lost_rel = [row for row in lost if row["final_grade"] is not None and row["final_grade"] >= 1]
            denom = len(entering_rel)
            output.append({
                "query_id": query_id, "ranker_id": ranker_id, "stage": stage,
                "entered_count": len(entering), "retained_count": len(entering)-len(lost),
                "lost_count": len(lost), "entered_relevant_count": denom,
                "lost_relevant_count": len(lost_rel),
                "conditional_relevant_loss_rate": len(lost_rel)/denom if denom else None,
                "conditional_loss_null_reason": None if denom else "no_entering_relevant_documents",
                "cumulative_relevant_retention": (
                    sum(row["paper_id"] not in lost_so_far for row in relevant)/len(relevant)
                    if relevant else None),
                "cumulative_null_reason": None if relevant else "no_relevant_documents",
                "final_returned_count": len(retained_ids),
            })
        first_loss = sum(row["first_exclusion_reason"] is not None for row in rows)
        if first_loss + len(retained_ids) != len(rows):
            raise ValueError(f"Loss conservation failed for {query_id}/{ranker_id}")
    return output
