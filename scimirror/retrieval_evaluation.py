"""Versioned retrieval evaluation with explicit unknown and incomplete states."""
import itertools
import math
from collections import defaultdict

EVALUATOR_VERSION='retrieval_evaluator_2'
VALID_GRADES={0,1,2}


# Index family qrels strictly while preserving judged negatives and unjudged relations.
def build_gold(document_map,qrels,available_ids):
    available=set(available_ids); doc_family={}; known_families=set()
    for row in document_map:
        pid=row['paper_id']; family=row['gold_family_id']; known_families.add(family)
        if pid not in available: continue
        if pid in doc_family and doc_family[pid]!=family: raise ValueError('Document has conflicting families: '+pid)
        doc_family[pid]=family
    judged={}
    for row in qrels:
        key=(row['query_id'],row['gold_family_id'])
        try: grade=int(row['query_relevance_grade'])
        except (TypeError,ValueError): raise ValueError('Invalid qrel grade')
        if grade not in VALID_GRADES: raise ValueError('Invalid qrel grade')
        if row['gold_family_id'] not in known_families: raise ValueError('Qrel references unknown family')
        if key in judged and judged[key]!=grade: raise ValueError('Conflicting qrel grade')
        judged[key]=grade
    relevant=defaultdict(set)
    for (query,family),grade in judged.items():
        if grade>=1 and family in set(doc_family.values()): relevant[query].add(family)
    return doc_family,judged,dict(relevant)


# Calculate family/document metrics without converting missing judgments to negative labels.
def evaluate_selection(topic_id,selected_ids,candidate_ids,doc_family,relevant,judged=None,document_judgments=None):
    gold=set(relevant.get(topic_id,set())); selected_families={doc_family[x] for x in selected_ids if x in doc_family}
    candidate_families={doc_family[x] for x in candidate_ids if x in doc_family}; found=gold&selected_families
    statuses=[]; grades=[]
    for pid in selected_ids:
        if document_judgments is not None:
            value=document_judgments.get((topic_id,pid)); status='unjudged' if value is None else value.get('status','unjudged'); grade=value.get('grade') if value else None
        else:
            family=doc_family.get(pid); grade=judged.get((topic_id,family)) if judged is not None and family is not None else (1 if family in gold else None)
            status='unjudged' if grade is None else ('judged_positive' if grade>=1 else 'judged_negative')
        statuses.append(status); grades.append(grade)
    relevant_documents=sum(status=='judged_positive' for status in statuses); numeric=sum(grade is not None for grade in grades)
    pairs=list(itertools.combinations(selected_ids,2)); duplicate_pairs=sum(doc_family.get(a)==doc_family.get(b) and a in doc_family and b in doc_family for a,b in pairs)
    capacity_denominator=min(3,len(gold)) if gold else 0
    return {'evaluator_version':EVALUATOR_VERSION,'gold_family_count':len(gold),'capacity_coverage':len(found)/capacity_denominator if capacity_denominator else None,
      'capacity_coverage_numerator':len(found),'capacity_coverage_denominator':capacity_denominator,
      'family_recall':len(found)/len(gold) if gold else None,'document_precision':relevant_documents/numeric if numeric else None,
      'document_precision_denominator':numeric,'unjudged_selected_count':sum(x=='unjudged' for x in statuses),
      'unsure_selected_count':sum(x=='unsure' for x in statuses),'pending_adjudication_count':sum(x=='pending_adjudication' for x in statuses),
      'candidate_family_recall':len(candidate_families&gold)/len(gold) if gold else None,'selected_count':len(selected_ids),
      'relevant_selected_count':relevant_documents,'empty_result':not selected_ids,'all_returned_irrelevant':bool(selected_ids) and numeric==len(selected_ids) and relevant_documents==0,
      'duplicate_pair_ratio':duplicate_pairs/len(pairs) if pairs else 0.0,'selected_family_ids':sorted(selected_families)}


# Compare fixed and repaired rows on the complete dataset/topic/memory key and expose missing pairs.
def regression_checks(topic_rows,max_coverage_drop,max_precision_drop):
    lookup={}
    for row in topic_rows:
        key=(row['dataset_id'],row['ranker'],row['topic_id'],row['memory_condition'])
        if key in lookup: raise ValueError('Duplicate regression summary key: '+repr(key))
        lookup[key]=row
    failures=[]
    for (dataset,ranker,topic,memory),base in lookup.items():
        if dataset!='supplement_unchanged' or ranker!='stage_a_fixed': continue
        repaired=lookup.get((dataset,'stage_a_repaired_v1',topic,memory))
        if repaired is None:
            failures.append({'topic_id':topic,'memory_condition':memory,'metric':'comparison','status':'incomplete','reason':'missing_treatment','baseline_n':base.get('n'),'treatment_n':0}); continue
        for metric,threshold,minimum in (('mean_capacity_coverage',max_coverage_drop,.8),('mean_document_precision',max_precision_drop,None)):
            b=base.get(metric); t=repaired.get(metric)
            if b is None or t is None:
                if b is not None: failures.append({'topic_id':topic,'memory_condition':memory,'metric':metric,'status':'incomplete','reason':'null_treatment','baseline':b,'treatment':t,'baseline_n':base.get('n'),'treatment_n':repaired.get('n')})
                continue
            difference=b-t
            if (minimum is None or b>=minimum) and difference>threshold:
                failures.append({'topic_id':topic,'memory_condition':memory,'metric':metric.replace('mean_',''),'status':'failed','baseline':b,'treatment':t,
                  'difference':difference,'threshold':threshold,'baseline_n':base.get('n'),'treatment_n':repaired.get('n'),
                  'baseline_numerator':base.get(metric.replace('mean_','')+'_numerator'),'baseline_denominator':base.get(metric.replace('mean_','')+'_denominator'),
                  'treatment_numerator':repaired.get(metric.replace('mean_','')+'_numerator'),'treatment_denominator':repaired.get(metric.replace('mean_','')+'_denominator')})
    return failures


# Evaluate a ranked list at k with explicit shortfall, unknown bounds, and shared-scope nDCG.
def ranked_metrics(ranked_ids,k,resolved_grades,scope_grades,annotation_scope='full_corpus'):
    returned=list(ranked_ids[:k]); labels=[resolved_grades.get(pid) for pid in returned]; judged=[x for x in labels if x is not None]
    relevant=sum(x>=1 for x in judged); unresolved=sum(x is None for x in labels); complete_scope=bool(scope_grades) and all(x is not None for x in scope_grades.values())
    dcg=sum(((2**grade-1)/math.log2(index+2)) for index,grade in enumerate(labels) if grade is not None)
    ideal=sorted((grade for grade in scope_grades.values() if grade is not None),reverse=True)[:k]
    idcg=sum((2**grade-1)/math.log2(index+2) for index,grade in enumerate(ideal))
    formal_precision=(relevant/k if unresolved==0 else None) if returned else 0.0
    result={'evaluator_version':EVALUATOR_VERSION,'k':k,'returned_count':len(returned),'shortfall':k-len(returned),'empty':not returned,
      'judged_count':len(judged),'unjudged_count':unresolved,'judged_coverage_returned':len(judged)/len(returned) if returned else None,
      'precision_judged':relevant/len(judged) if judged else None,'precision_at_k':formal_precision,
      'precision_at_k_lower':relevant/k,'precision_at_k_upper':(relevant+unresolved)/k,
      'precision_returned':relevant/len(returned) if returned and unresolved==0 else None,'dcg_partial':dcg,
      'ndcg_at_k':dcg/idcg if complete_scope and idcg else None,'ndcg_null_reason':None if complete_scope and idcg else ('incomplete_scope' if not complete_scope else 'no_relevant_supply')}
    relevant_supply=sum(grade>=1 for grade in scope_grades.values() if grade is not None)
    result['recall_at_k' if annotation_scope=='full_corpus' else 'pooled_recall_at_k']=(relevant/relevant_supply if complete_scope and relevant_supply else None)
    result['recall_null_reason']=None if complete_scope and relevant_supply else ('incomplete_scope' if not complete_scope else 'no_relevant_supply')
    return result
