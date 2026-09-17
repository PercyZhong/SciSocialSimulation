"""Traceable automatic proposals for legacy qrel semantic review."""
import json
from collections import defaultdict
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


# Export a blank human-review packet for the four unresolved legacy topics.
def export_human_review(config,root,output,registry):
    root=Path(root); output=Path(output); output.mkdir(parents=True,exist_ok=True); targets={'learning_exploration','learning_reward','science_collaboration','science_evaluation'}
    topics={row['topic_id']:row for row in json.loads((root/config['topics']).read_text(encoding='utf-8'))}; corpus={row['id']:row for row in read_jsonl(root/registry['supplement_unchanged']['corpus_path'])}
    mapping={row['gold_family_id']:[] for row in read_jsonl(root/registry['supplement_unchanged']['families_path'])}
    for row in read_jsonl(root/registry['supplement_unchanged']['document_map_path']): mapping.setdefault(row['gold_family_id'],[]).append(row['paper_id'])
    qrels=read_jsonl(root/registry['supplement_unchanged']['qrels_path']); qhash=file_hash(root/registry['supplement_unchanged']['qrels_path']); rows=[]
    for qrel in qrels:
        if qrel['query_id'] not in targets or int(qrel['query_relevance_grade'])<1: continue
        for pid in mapping.get(qrel['gold_family_id'],[]):
            if pid not in corpus: continue
            paper=corpus[pid]; review_id=f"{qrel['query_id']}__{pid}"
            rows.append({'review_id':review_id,'topic_id':qrel['query_id'],'paper_id':pid,'gold_family_id':qrel['gold_family_id'],'topic_definition':topics[qrel['query_id']]['description'],
              'title':paper['title'],'abstract':paper['abstract'],'old_grade':qrel['query_relevance_grade'],'source_version':'stage_a_supplement_v2','decision':'','proposed_grade':'',
              'rationale':'','evidence_excerpt':'','reviewer_id':'','reviewed_at':'','base_qrels_hash':qhash,'review_version':'human_gold_review_v1'})
    write_csv(output/'human_gold_review_template.csv',rows); dump_path=output/'status.json'; dump_path.write_text(json.dumps({'status':'awaiting_human_review','requested_reviews':len(rows),'imported_reviews':0,'base_qrels_hash':qhash},indent=2)+'\n',encoding='utf-8')
    (output/'GUIDE_ZH.md').write_text('# 人工 Gold 审阅\n\n逐条阅读主题定义、标题和摘要。decision 只能为 retain、revise 或 insufficient_information；不得参考检索分数。evidence_excerpt 必须能在标题或摘要原文中定位。\n',encoding='utf-8')
    return {'status':'awaiting_human_review','requested_reviews':len(rows),'base_qrels_hash':qhash}


# Import traceable human decisions into a new proposal version without replacing legacy qrels.
def import_gold_decisions(input_path,review_dir,root,config,registry):
    import csv
    root=Path(root); review_dir=Path(review_dir)
    with Path(input_path).open(encoding='utf-8-sig',newline='') as stream: incoming=list(csv.DictReader(stream))
    with (review_dir/'human_gold_review_template.csv').open(encoding='utf-8-sig',newline='') as stream: template={row['review_id']:row for row in csv.DictReader(stream)}
    qhash=file_hash(root/registry['supplement_unchanged']['qrels_path']); accepted=[]
    for row in incoming:
        base=template.get(row.get('review_id'))
        if base is None or row.get('base_qrels_hash')!=qhash or row.get('review_version')!='human_gold_review_v1': raise ValueError('Unknown review or qrels version mismatch')
        decision=row.get('decision'); grade=str(row.get('proposed_grade','')).strip()
        if decision not in ('retain','revise','insufficient_information'): raise ValueError('Invalid gold decision')
        if decision=='retain': grade=str(base['old_grade'])
        if decision=='revise' and grade not in ('0','1','2'): raise ValueError('Revised grade must be 0, 1, or 2')
        if decision=='insufficient_information': grade=''
        if not all(str(row.get(key,'')).strip() for key in ('rationale','evidence_excerpt','reviewer_id','reviewed_at')): raise ValueError('Decision provenance incomplete')
        excerpt=row['evidence_excerpt'].strip()
        if excerpt.lower() not in (base['title']+' '+base['abstract']).lower(): raise ValueError('Evidence excerpt is not present in source text')
        accepted.append({**base,**row,'proposed_grade':int(grade) if grade else None})
    by_family=defaultdict(set)
    for row in accepted:
        if row['proposed_grade'] is not None: by_family[(row['topic_id'],row['gold_family_id'])].add(row['proposed_grade'])
    conflicts=[{'topic_id':key[0],'gold_family_id':key[1],'grades':sorted(values)} for key,values in by_family.items() if len(values)>1]
    write_jsonl(review_dir/'imported_decisions.jsonl',accepted); write_jsonl(review_dir/'family_conflicts.jsonl',conflicts)
    proposals=[{'query_id':topic,'gold_family_id':family,'query_relevance_grade':next(iter(values)),'version':'human_gold_review_v1'} for (topic,family),values in by_family.items() if len(values)==1]
    write_jsonl(review_dir/'qrels_human_revision_proposal.jsonl',proposals)
    legacy=read_jsonl(root/registry['supplement_unchanged']['qrels_path']); overlay={(row['query_id'],row['gold_family_id']):row['query_relevance_grade'] for row in proposals}; combined=[]
    for row in legacy:
        key=(row['query_id'],row['gold_family_id']); combined.append({**row,'query_relevance_grade':overlay.get(key,row['query_relevance_grade']),'base_qrels_hash':qhash,'review_version':'human_gold_review_v1','decision_status':'revised' if key in overlay else 'unreviewed_retained'})
    write_jsonl(review_dir/'qrels_human_revision_full.jsonl',combined); status='conflict' if conflicts else ('completed' if len(accepted)==len(template) else 'partial')
    (review_dir/'status.json').write_text(json.dumps({'status':status,'requested_reviews':len(template),'imported_reviews':len(accepted),'family_conflicts':len(conflicts),'qrels_replaced':False},indent=2)+'\n',encoding='utf-8')
    return {'status':status,'imported_reviews':len(accepted),'family_conflicts':len(conflicts)}
