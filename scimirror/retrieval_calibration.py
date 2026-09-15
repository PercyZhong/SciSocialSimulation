"""Measured, calibration-split-only parameter selection."""
import copy, statistics, time
from pathlib import Path

from .policy import PolicyContext
from .stage_a import read_jsonl, write_jsonl
from .v03_retrieval import load_retrieval_profiles, retrieve_stage_a_repaired
from .v03_review import write_csv


# Execute at most six frozen trials and select by feasible macro F1 then beta and ID.
def calibrate(config,root,output,corpus,topic_model,doc_family,relevant,calibration_families,negative_cases=()):
    root=Path(root); output=Path(output); profiles=load_retrieval_profiles(root/config['profiles_repaired']); cases=[]; summaries=[]
    for trial in config['calibration_grid']:
        started=time.perf_counter(); retrieval=copy.deepcopy(config['repaired_defaults']); retrieval.update(trial); retrieval['_profiles']=profiles
        topic_scores=[]; negative_passes=0; calls=0
        for topic in sorted(topic_model.topics):
            papers,audit=retrieve_stage_a_repaired(corpus.papers,topic_model,PolicyContext('balanced','retrieval',True,config['stage_lambda']),retrieval,topic,topic_model.topics[topic]['field'],[],[]); calls+=1
            gold={family for family in relevant.get(topic,set()) if family in calibration_families}; selected={doc_family.get(p['id']) for p in papers}; tp=len(gold&selected)
            precision=tp/len(selected) if selected else 0.0; recall=tp/len(gold) if gold else 0.0; f1=2*precision*recall/(precision+recall) if precision+recall else 0.0; topic_scores.append(f1)
            selected_ids=[p['id'] for p in papers]; diagnostic={row['paper_id']:row for row in audit['diagnostics']}
            cases.append({'trial_id':trial['trial_id'],'topic_id':topic,'selected_ids':selected_ids,
              'selected_scores':{pid:diagnostic[pid]['final_score'] for pid in selected_ids},'selected_families':sorted(x for x in selected if x),
              'calibration_gold_families':sorted(gold),'precision':precision,'recall':recall,'f1':f1,'negative_gate_passes':0,
              'measurement_status':'measured','gold_version':'stage_a_supplement_v2_calibration'})
        for case in negative_cases:
            from .v03_retrieval import repaired_evidence_score
            score,_=repaired_evidence_score(profiles['topics'][case['query_id']],case['paper'],int(retrieval['match_window_tokens']))
            passed=score>=float(retrieval['minimum_relevance']); negative_passes+=passed
            cases.append({'trial_id':trial['trial_id'],'topic_id':case['query_id'],'paper_id':case['paper']['id'],'negative_case':True,
              'gate_passed':passed,'evidence_score':score,'measurement_status':'measured','gold_version':'closure_v3_calibration_negative'})
        summaries.append({**trial,'measurement_status':'measured','production_retrieval_calls':calls,'macro_f1':statistics.mean(topic_scores),
          'negative_gate_passes':negative_passes,'elapsed_seconds':time.perf_counter()-started,'feasible':negative_passes==0})
    feasible=[row for row in summaries if row['feasible']]
    selected=sorted(feasible,key=lambda row:(-row['macro_f1'],row['beta_memory'],row['trial_id']))[0] if feasible else None
    for row in summaries: row['selected']=selected is not None and row['trial_id']==selected['trial_id']
    write_csv(output/'calibration_trials.csv',summaries); write_jsonl(output/'calibration_case_results.jsonl',cases)
    return {'status':'completed' if selected else 'no_feasible_trial','selected_trial':selected,'trial_count':len(summaries),'case_rows':len(cases),
      'production_retrieval_calls':sum(row['production_retrieval_calls'] for row in summaries),'test_families_used':False}
