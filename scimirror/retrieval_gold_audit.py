"""Traceable automatic proposals for legacy qrel semantic review."""
import json
from pathlib import Path

from .stage_a import file_hash, read_jsonl, write_jsonl
from .v03_review import write_csv


# Produce Codex-authored proposals while preserving every original qrel unchanged.
def audit_gold(config,root,output,registry):
    root=Path(root); output=Path(output); topics={row['topic_id']:row for row in json.loads((root/config['topics']).read_text(encoding='utf-8'))}
    target={'agent_tools','learning_exploration','science_collaboration','science_evaluation','science_retrieval','agent_communication','agent_planning','learning_reward'}
    corpus={row['id']:row for row in read_jsonl(root/registry['supplement_unchanged']['corpus_path'])}
    mapping={row['gold_family_id']:[] for row in read_jsonl(root/registry['supplement_unchanged']['families_path'])}
    for row in read_jsonl(root/registry['supplement_unchanged']['document_map_path']): mapping.setdefault(row['gold_family_id'],[]).append(row['paper_id'])
    qrels=read_jsonl(root/registry['supplement_unchanged']['qrels_path']); qhash=file_hash(root/registry['supplement_unchanged']['qrels_path']); rows=[]
    for qrel in qrels:
        topic=qrel['query_id']
        if topic not in target or int(qrel['query_relevance_grade'])<1: continue
        for pid in mapping.get(qrel['gold_family_id'],[]):
            if pid not in corpus: continue
            paper=corpus[pid]; text=(paper['title']+' '+paper['abstract']).lower(); keywords=topics[topic]['keywords']
            supported=all(word.lower() in text for word in keywords) if len(keywords)>1 else False
            status='supported' if supported else 'ambiguous'; proposed=int(qrel['query_relevance_grade']) if supported else None
            reason=('All frozen topic keywords occur in title/abstract.' if supported else
              'Short synthetic text does not establish the full frozen topic meaning; independent human review required.')
            rows.append({'topic_id':topic,'paper_id':pid,'gold_family_id':qrel['gold_family_id'],'old_grade':qrel['query_relevance_grade'],
              'topic_definition':topics[topic]['description'],'title':paper['title'],'abstract':paper['abstract'],
              'supporting_excerpt':paper['title'] if supported else '', 'contradicting_excerpt':'','proposed_grade':proposed,
              'review_status':status,'reviewer_type':'codex_automatic_proposal','reviewer_id':'codex_stage_a_repair',
              'reason':reason,'old_qrels_hash':qhash,'proposed_revision_version':'stage_a_repair_proposal_v1'})
    write_csv(output/'gold_audit.csv',rows); write_jsonl(output/'qrels_revision_proposal.jsonl',rows)
    status='needs_gold_review' if any(row['review_status']!='supported' for row in rows) else 'supported'
    return {'status':status,'rows':len(rows),'supported':sum(row['review_status']=='supported' for row in rows),
      'ambiguous':sum(row['review_status']=='ambiguous' for row in rows),'human_reviews':0,'qrels_replaced':False}
