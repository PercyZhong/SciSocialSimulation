"""Label-blind BM25 recall followed by a fixed, non-gating semantic soft rerank."""
from __future__ import annotations

from .stage_b_pilot import bm25_scores
from .v03_retrieval import repaired_evidence_score

RANKER_VERSION = "stage_b_bm25_soft_rerank_1"


def topic_scores(papers, profile, window_size=18):
    """Return bounded non-negative profile scores; unknown profiles score zero."""
    if profile is None:
        return ({paper_id: 0.0 for paper_id in papers},
                {paper_id: {"warning": "unknown_profile", "raw": None} for paper_id in papers})
    scores, details = {}, {}
    for paper_id in sorted(papers):
        raw, detail = repaired_evidence_score(profile, papers[paper_id], int(window_size))
        raw = float(raw)
        if raw != raw or raw in (float("inf"), float("-inf")):
            raise ValueError("Topic score must be finite")
        scores[paper_id] = max(0.0, raw)
        details[paper_id] = {**detail, "raw": raw, "warning": None}
    return scores, details


def soft_rank(query_text, papers, profile, *, candidate_k=20, output_k=10,
              lambda_value=.10, k1=1.2, b=.75, window_size=18,
              normalization_denominators=None):
    """Rank only the deterministic BM25 top-K; never hard-filter by topic score."""
    if candidate_k < 1 or output_k < 1 or output_k > candidate_k or lambda_value < 0:
        raise ValueError("Invalid soft-ranker parameters")
    lexical = bm25_scores(query_text, papers, float(k1), float(b))
    bm25_order = sorted(papers, key=lambda paper_id: (-lexical[paper_id], paper_id))
    candidates = bm25_order[:min(int(candidate_k), len(bm25_order))]
    zero_signal = max(lexical.values(), default=0.0) == 0.0
    semantic, details = topic_scores(papers, profile, window_size)
    observed_bm25 = max((lexical[paper_id] for paper_id in candidates), default=0.0)
    observed_topic = max((semantic[paper_id] for paper_id in candidates), default=0.0)
    supplied = normalization_denominators or {}
    peak_bm25 = float(supplied.get("bm25", observed_bm25))
    peak_topic = float(supplied.get("topic", observed_topic))
    if peak_bm25 < 0 or peak_topic < 0:
        raise ValueError("Normalization denominators must be non-negative")
    rows = {}
    for paper_id in papers:
        bnorm = lexical[paper_id] / peak_bm25 if peak_bm25 else 0.0
        tnorm = semantic[paper_id] / peak_topic if peak_topic else 0.0
        rows[paper_id] = {
            "bm25_raw": lexical[paper_id], "bm25_normalized": bnorm,
            "topic_score_raw": details[paper_id]["raw"],
            "topic_score_normalized": tnorm,
            "rerank_score": bnorm + float(lambda_value) * tnorm,
            "profile_warning": details[paper_id]["warning"],
        }
    if zero_signal or float(lambda_value) == 0.0:
        ranked = list(candidates)
    else:
        ranked = sorted(candidates, key=lambda paper_id: (-rows[paper_id]["rerank_score"], paper_id))
    returned = ranked[:min(int(output_k), len(ranked))]
    return {
        "ranker_version": RANKER_VERSION,
        "candidate_ids": candidates,
        "ranked_candidate_ids": ranked,
        "returned_ids": returned,
        "bm25_order": bm25_order,
        "zero_signal": zero_signal,
        "unknown_profile": profile is None,
        "normalization_denominators": {"bm25": peak_bm25, "topic": peak_topic},
        "observed_candidate_maxima": {"bm25": observed_bm25, "topic": observed_topic},
        "normalization_source": "fixed_external" if normalization_denominators is not None else "candidate_maxima",
        "scores": rows,
    }
