"""Prepared C1 integration boundary; this round deliberately does not execute C1."""


def apply_model_decision(stage, world_projection, decision, allowed_actions):
    """Apply a validated model action without invoking or overriding it with rule choice."""
    allowed={a["action_id"] for a in allowed_actions}
    action=decision["validated_payload"]["action_id"]
    if action not in allowed: raise ValueError("Decision is outside legal action set")
    updated=dict(world_projection)
    updated.setdefault("decision_events",[])
    updated["decision_events"]=[*updated["decision_events"],{"stage":stage,"action_id":action,"decision_source":"llm"}]
    updated[f"selected_{stage}"]=action
    return updated


def c1_status():
    return {"tooling":"prepared","retrieval":"bm25_live_pilot","worlds":"one per model",
            "agents":20,"policy":"balanced","network":"open","ticks":12,
            "authorized":False,"executed":False,"quality_evaluation":"not_performed"}
