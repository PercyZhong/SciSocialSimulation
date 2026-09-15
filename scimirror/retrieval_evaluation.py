"""Independent family/qrel evaluation for Stage A repair."""
import itertools


# Index document families and graded qrels without treating unjudged items as negatives.
def build_gold(document_map,qrels,available_ids):
    doc_family={row['paper_id']:row['gold_family_id'] for row in document_map if row['paper_id'] in available_ids}
    judged={(row['query_id'],row['gold_family_id']):int(row['query_relevance_grade']) for row in qrels}
    relevant={}
    for query,_ in judged:
        relevant[query]={family for (qid,family),grade in judged.items() if qid==query and grade>=1 and family in set(doc_family.values())}
    return doc_family,judged,relevant


# Calculate transparent document and family metrics from selected IDs only.
def evaluate_selection(topic_id,selected_ids,candidate_ids,doc_family,relevant):
    gold=relevant.get(topic_id,set()); selected_families={doc_family[x] for x in selected_ids if x in doc_family}
    candidate_families={doc_family[x] for x in candidate_ids if x in doc_family}; found=gold&selected_families
    relevant_documents=sum(doc_family.get(pid) in gold for pid in selected_ids)
    pairs=list(itertools.combinations(selected_ids,2)); duplicate_pairs=sum(doc_family.get(a)==doc_family.get(b) and a in doc_family and b in doc_family for a,b in pairs)
    return {'gold_family_count':len(gold),'capacity_coverage':len(found)/min(3,len(gold)) if gold else None,
      'family_recall':len(found)/len(gold) if gold else None,
      'document_precision':relevant_documents/len(selected_ids) if selected_ids else None,
      'candidate_family_recall':len(candidate_families&gold)/len(gold) if gold else None,
      'selected_count':len(selected_ids),'relevant_selected_count':relevant_documents,'empty_result':not selected_ids,
      'all_returned_irrelevant':bool(selected_ids) and relevant_documents==0,
      'duplicate_pair_ratio':duplicate_pairs/len(pairs) if pairs else 0.0,'selected_family_ids':sorted(selected_families)}


# Compare repaired topic means to the fixed baseline using frozen regression limits.
def regression_checks(topic_rows,max_coverage_drop,max_precision_drop):
    lookup={(row['dataset_id'],row['ranker'],row['topic_id']):row for row in topic_rows}; failures=[]
    for key,base in lookup.items():
        dataset,ranker,topic=key
        if dataset!='supplement_unchanged' or ranker!='stage_a_fixed': continue
        repaired=lookup.get((dataset,'stage_a_repaired_v1',topic))
        if not repaired: continue
        if base['mean_capacity_coverage'] is not None and base['mean_capacity_coverage']>=.8 and base['mean_capacity_coverage']-repaired['mean_capacity_coverage']>max_coverage_drop:
            failures.append({'topic_id':topic,'metric':'capacity_coverage','baseline':base['mean_capacity_coverage'],'repaired':repaired['mean_capacity_coverage']})
        if base['mean_document_precision'] is not None and repaired['mean_document_precision'] is not None and base['mean_document_precision']-repaired['mean_document_precision']>max_precision_drop:
            failures.append({'topic_id':topic,'metric':'document_precision','baseline':base['mean_document_precision'],'repaired':repaired['mean_document_precision']})
    return failures
