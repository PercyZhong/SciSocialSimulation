"""Strict Stage C decision payload validation."""


STAGES = ("topic_selection", "retrieval_query_selection", "invitation", "project_exit")


def validate_decision(payload, allowed_actions, visible_evidence):
    if not isinstance(payload, dict) or set(payload) != {"action_id", "reason_summary", "evidence_ids"}:
        raise ValueError("Decision must contain exactly action_id, reason_summary, evidence_ids")
    allowed_ids = {a["action_id"] for a in allowed_actions}
    if payload["action_id"] not in allowed_ids:
        raise ValueError("Invalid action_id")
    reason = payload["reason_summary"]
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise ValueError("Invalid reason_summary")
    evidence = payload["evidence_ids"]
    if not isinstance(evidence, list) or any(not isinstance(x, str) for x in evidence):
        raise ValueError("Invalid evidence_ids")
    if not set(evidence) <= set(visible_evidence):
        raise ValueError("Invisible evidence reference")
    return payload
