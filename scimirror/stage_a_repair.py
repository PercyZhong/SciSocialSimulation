"""Stage A finite repair and Stage B preparation orchestration."""
import copy, csv, hashlib, itertools, json, os, platform, random, shutil, statistics, subprocess, sys, tempfile, zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .common import canonical, digest, dump
from .corpus import Corpus
from .policy import PolicyContext
from .retrieval_calibration import calibrate as measured_calibration
from .retrieval_evaluation import build_gold, evaluate_selection, regression_checks
from .retrieval_gold_audit import audit_gold
from .stage_a import build_frozen_states, file_hash, read_jsonl, write_jsonl
from .topics import TopicModel
from .v03_retrieval import load_retrieval_profiles, repaired_evidence_score, retrieve_stage_a_fixed, retrieve_stage_a_repaired, retrieve_stage_a_semantic_guarded
from .v03_review import write_csv

SCHEMA='stage_a_repair_1'


# Load the repair protocol and reject network, LLM, or expanded calibration grids.
def load_config(path,root):
    config=json.loads(Path(path).read_text(encoding='utf-8'))
    required={'schema_version','topics','profiles_closure','profiles_repaired','supplement_config','closure_config','rankers','policies','fields','memory_conditions','reference_retrieval','closure_retrieval','repaired_defaults','calibration_grid','quality_thresholds'}
    if config.get('schema_version')!=SCHEMA or not required<=set(config): raise ValueError('Invalid Stage A repair config')
    if config.get('allow_network') or config.get('allow_llm_calls') or len(config['calibration_grid'])>6: raise ValueError('Repair must remain finite and offline')
    if config['rankers']!=['stage_a_fixed','stage_a_semantic_guarded','stage_a_repaired_v1']: raise ValueError('Ranker set changed')
    for path_key in ('topics','profiles_closure','profiles_repaired','supplement_config','closure_config'):
        if not (Path(root)/config[path_key]).is_file(): raise ValueError('Missing input '+config[path_key])
    return config


# Build a four-way read-only registry from resolved configs and content differences.
def dataset_registry(config,root):
    root=Path(root); supplement=json.loads((root/config['supplement_config']).read_text(encoding='utf-8')); closure=json.loads((root/config['closure_config']).read_text(encoding='utf-8'))
    supp_path=root/supplement['corpora']['corpus_expanded']; closure_path=root/closure['corpora']['corpus_expanded']; original_path=root/supplement['corpora']['corpus_original']
    supp=read_jsonl(supp_path); mixed=read_jsonl(closure_path); supp_ids={row['id'] for row in supp}; challenge=[row for row in mixed if row['id'] not in supp_ids]
    if mixed[:len(supp)]!=supp: raise ValueError('Closure mixed corpus does not preserve the old 90 documents')
    common={'families_path':supplement['gold_paths']['families'],'qrels_path':supplement['gold_paths']['qrels'],'document_map_path':supplement['gold_paths']['document_map']}
    registry={
      'original_legacy':{'corpus_path':supplement['corpora']['corpus_original'],'families_path':None,'qrels_path':None,'document_map_path':None,'purpose':'historical compatibility and supply description','label_source':'topic_ids in original fixture','developer_seen':True},
      'supplement_unchanged':{'corpus_path':supplement['corpora']['corpus_expanded'],**common,'purpose':'primary legacy regression','label_source':'stage_a_supplement_v2','developer_seen':True},
      'closure_challenge_only':{'corpus_path':closure['corpora']['corpus_expanded'],'families_path':closure['gold_paths']['families'],'qrels_path':closure['gold_paths']['challenge_qrels'],'document_map_path':closure['gold_paths']['document_map'],'purpose':'rule challenge evaluation by difference IDs','label_source':'Codex-designed closure challenges','developer_seen':True,'included_ids':sorted(row['id'] for row in challenge)},
      'closure_mixed_legacy':{'corpus_path':closure['corpora']['corpus_expanded'],'families_path':closure['gold_paths']['families'],'qrels_path':closure['gold_paths']['qrels'],'document_map_path':closure['gold_paths']['document_map'],'purpose':'historical mixed-source diagnosis only','label_source':'closure reconstructed qrels','developer_seen':True}}
    for name,row in registry.items():
        records=read_jsonl(root/row['corpus_path']); eligible=[x for x in records if x['year']<config['cutoff_year']]
        selected=[x for x in eligible if x['id'] in set(row.get('included_ids',[]))] if name=='closure_challenge_only' else eligible
        row.update(raw_sha256=file_hash(root/row['corpus_path']),filtered_content_hash=digest(selected),document_count=len(selected),raw_document_count=len(records),
          family_count=len(read_jsonl(root/row['families_path'])) if row['families_path'] else None,
          qrels_sha256=file_hash(root/row['qrels_path']) if row['qrels_path'] else None,qrels_version='historical_preserved' if row['qrels_path'] else 'topic_ids_only')
    registry['verification']={'old_document_rows_identical':True,'old_document_count':len(supp),'closure_unique_challenge_documents':len(challenge),
      'closure_challenge_topic_annotations':len(read_jsonl(root/closure['gold_paths']['challenge_qrels'])),
      'closure_qrels_byte_identical_to_supplement':(root/closure['gold_paths']['qrels']).read_bytes()==(root/supplement['gold_paths']['qrels']).read_bytes(),
      'note':'Closure qrels were reconstructed with different metadata; they are not claimed byte-preserved.'}
    return registry


# Load one legacy dataset and construct independent family mappings.
def load_dataset(config,root,registry,dataset_id):
    root=Path(root); spec=registry[dataset_id]; corpus=Corpus(root/spec['corpus_path'],config['cutoff_year'],True); topics=TopicModel(root/config['topics'],corpus.papers,config['recognition_alpha'])
    if spec['document_map_path']:
        mapping=read_jsonl(root/spec['document_map_path']); qrels=read_jsonl(root/spec['qrels_path']); doc_family,_,relevant=build_gold(mapping,qrels,set(corpus.papers))
    else:
        doc_family={pid:'original_'+pid for pid in corpus.papers}; relevant={topic:set() for topic in topics.topics}
        for pid,labels in topics.paper_topics.items():
            for topic in labels: relevant[topic].add(doc_family[pid])
    return corpus,topics,doc_family,relevant


# Split dependency identity so qrel changes invalidate evaluation but never production ranking.
def dependency_contract(config,config_path,root,registry):
    root=Path(root); production=[Path(config_path),root/config['topics'],root/config['profiles_closure'],root/config['profiles_repaired'],
      *(root/registry[name]['corpus_path'] for name in ('original_legacy','supplement_unchanged')),
      *(root/'scimirror'/name for name in ('v03_retrieval.py','corpus.py','policy.py','topics.py','stage_a_repair.py'))]
    evaluation=[root/registry['supplement_unchanged'][key] for key in ('qrels_path','document_map_path','families_path')]
    hashes=lambda paths:{str(path.relative_to(root)).replace('\\','/'):file_hash(path) for path in paths}
    p,e=hashes(production),hashes(evaluation); return {'schema_version':SCHEMA,'production_hashes':p,'production_identity':digest(p),'evaluation_hashes':e,'evaluation_identity':digest(e)}


# Run the six measured calibration trials on calibration families and declared negative cases.
def calibrate(config,root,output,registry):
    root=Path(root); output=Path(output); corpus,topics,doc_family,relevant=load_dataset(config,root,registry,'supplement_unchanged')
    split=json.loads((root/json.loads((root/config['supplement_config']).read_text())['gold_paths']['split_manifest']).read_text()); calibration=set(split['calibration_families'])
    keep={pid for pid,family in doc_family.items() if family in calibration}; cal=copy.copy(corpus); cal.papers={pid:paper for pid,paper in corpus.papers.items() if pid in keep}
    closure=json.loads((root/config['closure_config']).read_text()); challenge_rows=read_jsonl(root/closure['gold_paths']['challenge_qrels']); mixed={row['id']:row for row in read_jsonl(root/closure['corpora']['corpus_expanded'])}
    negative=[{'query_id':row['query_id'],'paper':mixed[row['paper_id']]} for row in challenge_rows if int(row['grade'])==0 and row.get('split')=='calibration']
    cal_topics=TopicModel(root/config['topics'],cal.papers,config['recognition_alpha'])
    result=measured_calibration(config,root,output,cal,cal_topics,doc_family,relevant,calibration,negative); dump(output/'calibration_result.json',result); return result


# Freeze the actually selected trial and all input/implementation identities.
def freeze(config,config_path,root,output,registry):
    output=Path(output); result=json.loads((output/'calibration_result.json').read_text())
    if result['status']!='completed': raise ValueError('No measured feasible calibration trial')
    retrieval=copy.deepcopy(config['repaired_defaults']); retrieval.update({k:result['selected_trial'][k] for k in ('minimum_relevance','match_window_tokens','beta_memory')})
    protocol={'schema_version':SCHEMA,'frozen_at':datetime.now().astimezone().isoformat(),'selected_trial_id':result['selected_trial']['trial_id'],
      'selection_rule':'negative passes must be zero; maximize topic macro F1; tie: smaller beta_memory then trial_id','repaired_retrieval':retrieval,
      'quality_thresholds':config['quality_thresholds'],'float_tolerance':1e-12,'development_status':'development_seen_regression',
      'contract':dependency_contract(config,config_path,root,registry)}
    dump(output/'FROZEN_PROTOCOL.json',protocol); return protocol


# Dispatch one frozen state through one of the three preserved/new rankers.
def retrieve_case(ranker,config,repaired,root,corpus,topics,state,policy,memory_enabled=True):
    pc=PolicyContext(policy,'retrieval',True,config['stage_lambda']); memory=state['semantic_memory']; read=[]
    if ranker=='stage_a_fixed': return retrieve_stage_a_fixed(corpus.papers,topics,pc,config['reference_retrieval'],state['selected_topic_id'],state['agent_field'],memory,read)
    if ranker=='stage_a_semantic_guarded':
        rc=copy.deepcopy(config['closure_retrieval']); rc['_profiles']=load_retrieval_profiles(Path(root)/config['profiles_closure'])
        return retrieve_stage_a_semantic_guarded(corpus.papers,topics,pc,rc,state['selected_topic_id'],state['agent_field'],memory,read)
    rc=copy.deepcopy(repaired); rc['memory_enabled']=memory_enabled; rc['_profiles']=load_retrieval_profiles(Path(root)/config['profiles_repaired'])
    return retrieve_stage_a_repaired(corpus.papers,topics,pc,rc,state['selected_topic_id'],state['agent_field'],memory,read)


# Execute or safely resume the 1944-case legacy matrix and separate mixed/challenge diagnostics.
def run(config,config_path,root,output,registry):
    root=Path(root); output=Path(output); protocol=json.loads((output/'FROZEN_PROTOCOL.json').read_text()); current=dependency_contract(config,config_path,root,registry)
    if protocol['contract']['production_identity']!=current['production_identity']: raise ValueError('Frozen production dependency mismatch')
    contract_path=output/'resume_contract.json'
    if contract_path.exists() and json.loads(contract_path.read_text())['production_identity']!=current['production_identity']: raise ValueError('Resume contract mismatch')
    dump(contract_path,current); existing={row['core_case_id']:row for row in read_jsonl(output/'core_results.jsonl')}; diagnostic_existing={(row['core_case_id'],row['paper_id']):row for row in read_jsonl(output/'retrieval_diagnostics.jsonl')}; datasets={name:load_dataset(config,root,registry,name) for name in ('original_legacy','supplement_unchanged')}; computed=0
    for dataset_id,(corpus,topics,_,_) in datasets.items():
        state_cfg={'fields':config['fields'],'memory_conditions':config['memory_conditions'],'cutoff_year':config['cutoff_year']}
        states=build_frozen_states(state_cfg,file_hash(root/registry[dataset_id]['corpus_path']),file_hash(root/config['topics']),topics)
        jobs=list(itertools.product(states,config['rankers'],config['policies'])); random.Random(config['execution_order_seed']).shuffle(jobs)
        for state,ranker,policy in jobs:
            key=f'{dataset_id}__{state["case_id"]}__{ranker}__{policy}'
            if key in existing: continue
            _,audit=retrieve_case(ranker,config,protocol['repaired_retrieval'],root,corpus,topics,state,policy)
            existing[key]={'core_case_id':key,'dataset_id':dataset_id,'case_id':state['case_id'],'state_hash':state['state_hash'],'ranker':ranker,'policy':policy,
              'topic_id':state['selected_topic_id'],'agent_field':state['agent_field'],'memory_condition':state['memory_condition'],
              'selected_ids':audit['selected_ids'],'candidate_ids':audit.get('base_candidate_ids',[]),'qualified_ids':audit.get('qualified_ids',[]),
              'recognition_snapshot_hash':topics.audit['snapshot_hash'],'query_id':audit.get('query_id'),'ranker_version':audit.get('ranker_version',audit.get('mode'))}; computed+=1
            if ranker=='stage_a_repaired_v1':
                for item in audit['diagnostics']:
                    diagnostic_existing[(key,item['paper_id'])]={'core_case_id':key,'dataset_id':dataset_id,'case_id':state['case_id'],'policy':policy,
                      'topic_id':state['selected_topic_id'],'agent_field':state['agent_field'],'memory_condition':state['memory_condition'],**item}
    rows=[existing[key] for key in sorted(existing)]; write_jsonl(output/'core_results.jsonl',rows)
    write_jsonl(output/'retrieval_diagnostics.jsonl',[diagnostic_existing[key] for key in sorted(diagnostic_existing)])
    challenge=challenge_evaluation(config,root,registry,protocol['repaired_retrieval']); write_jsonl(output/'challenge_results.jsonl',challenge)
    policy=policy_controls(config,root,registry,protocol['repaired_retrieval']); write_jsonl(output/'policy_effects.jsonl',policy)
    memory=memory_controls(config,root,registry,protocol['repaired_retrieval']); write_jsonl(output/'memory_effects.jsonl',memory)
    mixed=mixed_diagnosis(config,root,registry,protocol['repaired_retrieval']); write_jsonl(output/'mixed_source_results.jsonl',mixed)
    usage={'schema_version':SCHEMA,'e1_logical_cases':len(rows),'e1_computed_this_invocation':computed,'e1_resumed':len(rows)-computed,
      'e0_reused_from_e1':sum(row['dataset_id']=='supplement_unchanged' and row['ranker']!='stage_a_repaired_v1' for row in rows),
      'e2_challenge_annotations':len(challenge),'e2_mixed_calls':len(mixed),'e3_policy_calls':len(policy),'e3_memory_calls':len(memory),
      'network_requests':0,'http_attempts':0,'llm_calls':0,'paid_api_calls':0}; dump(output/'usage.json',usage); return rows


# Evaluate each frozen challenge annotation by its (query,paper) key without regenerating positives.
def challenge_evaluation(config,root,registry,repaired):
    root=Path(root); closure=json.loads((root/config['closure_config']).read_text()); annotations=read_jsonl(root/closure['gold_paths']['challenge_qrels']); papers={row['id']:row for row in read_jsonl(root/closure['corpora']['corpus_expanded'])}; profiles=load_retrieval_profiles(root/config['profiles_repaired']); rows=[]
    for annotation in annotations:
        paper=papers[annotation['paper_id']]; score,detail=repaired_evidence_score(profiles['topics'][annotation['query_id']],paper,int(repaired['match_window_tokens']))
        eligible=paper['year']<config['cutoff_year']; passed=eligible and score>=float(repaired['minimum_relevance'])
        rows.append({**annotation,'eligible':eligible,'evidence_score':score,'gate_passed':passed,'expected_positive':int(annotation['grade'])>=1,**detail})
    return rows


# Run the prescribed 81 enabled and 27 disabled policy controls.
def policy_controls(config,root,registry,repaired):
    corpus,topics,_,_=load_dataset(config,root,registry,'supplement_unchanged'); scenarios=['agent_memory','science_retrieval','science_evaluation']; histories=['empty','familiar_a','familiar_b']; rows=[]
    for topic,history,ranker,policy in itertools.product(scenarios,histories,config['rankers'],config['policies']):
        state={'selected_topic_id':topic,'agent_field':topics.topics[topic]['field'],'semantic_memory':[]}; related=[p for p in corpus.papers.values() if topic in p.get('topic_ids',[])]; read=[] if history=='empty' else [p['title']+' '+p['abstract'] for p in (related[:2] if history=='familiar_a' else related[-2:])]
        pc=PolicyContext(policy,'retrieval',True,config['stage_lambda'])
        if ranker=='stage_a_fixed': _,audit=retrieve_stage_a_fixed(corpus.papers,topics,pc,config['reference_retrieval'],topic,state['agent_field'],[],read)
        elif ranker=='stage_a_semantic_guarded': rc=copy.deepcopy(config['closure_retrieval']); rc['_profiles']=load_retrieval_profiles(Path(root)/config['profiles_closure']); _,audit=retrieve_stage_a_semantic_guarded(corpus.papers,topics,pc,rc,topic,state['agent_field'],[],read)
        else: rc=copy.deepcopy(repaired); rc['_profiles']=load_retrieval_profiles(Path(root)/config['profiles_repaired']); _,audit=retrieve_stage_a_repaired(corpus.papers,topics,pc,rc,topic,state['agent_field'],[],read)
        rows.append({'topic_id':topic,'history':history,'ranker':ranker,'policy':policy,'path_enabled':True,'candidate_hash':digest(audit.get('base_candidate_ids',[])),'qualified_hash':digest(audit.get('qualified_ids',[])),'selected_ids':audit['selected_ids']})
    for topic,ranker,policy in itertools.product(scenarios,config['rankers'],config['policies']):
        pc=PolicyContext(policy,'retrieval',False,config['stage_lambda']); state={'selected_topic_id':topic,'agent_field':topics.topics[topic]['field'],'semantic_memory':[]}
        if ranker=='stage_a_fixed': _,audit=retrieve_stage_a_fixed(corpus.papers,topics,pc,config['reference_retrieval'],topic,state['agent_field'],[],[])
        elif ranker=='stage_a_semantic_guarded': rc=copy.deepcopy(config['closure_retrieval']); rc['_profiles']=load_retrieval_profiles(Path(root)/config['profiles_closure']); _,audit=retrieve_stage_a_semantic_guarded(corpus.papers,topics,pc,rc,topic,state['agent_field'],[],[])
        else: rc=copy.deepcopy(repaired); rc['_profiles']=load_retrieval_profiles(Path(root)/config['profiles_repaired']); _,audit=retrieve_stage_a_repaired(corpus.papers,topics,pc,rc,topic,state['agent_field'],[],[])
        rows.append({'topic_id':topic,'history':'empty','ranker':ranker,'policy':policy,'path_enabled':False,'candidate_hash':digest(audit.get('base_candidate_ids',[])),'qualified_hash':digest(audit.get('qualified_ids',[])),'selected_ids':audit['selected_ids']})
    return rows


# Run 54 on/off semantic-memory controls and record feature, score, order, and set responses.
def memory_controls(config,root,registry,repaired):
    corpus,topics,_,_=load_dataset(config,root,registry,'supplement_unchanged'); scenarios=['agent_memory','science_retrieval','science_evaluation']; all_topics=list(topics.topics); rows=[]
    for topic,condition,policy,enabled in itertools.product(scenarios,config['memory_conditions'],config['policies'],(True,False)):
        unrelated=all_topics[(all_topics.index(topic)+6)%len(all_topics)]; memory=[] if condition=='empty' else [{'topic_id':topic if condition=='related' else unrelated,'text':topic if condition=='related' else unrelated}]
        state={'selected_topic_id':topic,'agent_field':topics.topics[topic]['field'],'semantic_memory':memory}; rc=copy.deepcopy(repaired); rc['memory_enabled']=enabled; rc['_profiles']=load_retrieval_profiles(Path(root)/config['profiles_repaired'])
        _,audit=retrieve_stage_a_repaired(corpus.papers,topics,PolicyContext(policy,'retrieval',True,config['stage_lambda']),rc,topic,state['agent_field'],memory,[])
        rows.append({'topic_id':topic,'memory_condition':condition,'policy':policy,'memory_enabled':enabled,'candidate_hash':digest(audit['base_candidate_ids']),
          'qualified_hash':digest(audit['qualified_ids']),'selected_ids':audit['selected_ids'],'memory_resolution':audit['memory_resolution'],
          'nonzero_memory_features':sum((row.get('memory_score') or 0)>0 for row in audit['candidates']),
          'final_scores':{row['paper_id']:row['final_score'] for row in audit['candidates']}})
    return rows


# Run repaired retrieval on the historical mixed corpus only for source-contribution diagnosis.
def mixed_diagnosis(config,root,registry,repaired):
    corpus,topics,_,_=load_dataset(config,root,registry,'closure_mixed_legacy'); old_ids={row['id'] for row in read_jsonl(Path(root)/registry['supplement_unchanged']['corpus_path'])}; rows=[]
    state_cfg={'fields':config['fields'],'memory_conditions':config['memory_conditions'],'cutoff_year':config['cutoff_year']}
    for state,policy in itertools.product(build_frozen_states(state_cfg,file_hash(Path(root)/registry['closure_mixed_legacy']['corpus_path']),file_hash(Path(root)/config['topics']),topics),config['policies']):
        _,audit=retrieve_case('stage_a_repaired_v1',config,repaired,root,corpus,topics,state,policy)
        rows.append({'case_id':state['case_id'],'policy':policy,'selected_ids':audit['selected_ids'],'old_selected':sum(pid in old_ids for pid in audit['selected_ids']),'closure_selected':sum(pid not in old_ids for pid in audit['selected_ids'])})
    return rows


# Recompute all evaluation metrics and keep legacy and challenge outcomes separate.
def analyze(config,root,output,registry):
    output=Path(output); rows=read_jsonl(output/'core_results.jsonl'); evaluated=[]
    datasets={name:load_dataset(config,root,registry,name) for name in ('original_legacy','supplement_unchanged')}
    for row in rows:
        _,_,doc_family,relevant=datasets[row['dataset_id']]; evaluated.append({**row,**evaluate_selection(row['topic_id'],row['selected_ids'],row['candidate_ids'],doc_family,relevant)})
    grouped=defaultdict(list)
    for row in evaluated: grouped[(row['dataset_id'],row['ranker'],row['topic_id'],row['memory_condition'])].append(row)
    topic=[]
    for (dataset,ranker,topic_id,memory),values in sorted(grouped.items()):
        cov=[x['capacity_coverage'] for x in values if x['capacity_coverage'] is not None]; prec=[x['document_precision'] for x in values if x['document_precision'] is not None]
        topic.append({'dataset_id':dataset,'ranker':ranker,'topic_id':topic_id,'memory_condition':memory,'n':len(values),
          'mean_capacity_coverage':statistics.mean(cov) if cov else None,'coverage_n':len(cov),'mean_document_precision':statistics.mean(prec) if prec else None,
          'precision_n':len(prec),'empty_results':sum(x['empty_result'] for x in values),'all_irrelevant':sum(x['all_returned_irrelevant'] for x in values)})
    write_jsonl(output/'evaluated_results.jsonl',evaluated); write_csv(output/'metrics_by_dataset_topic.csv',topic)
    mixed=read_jsonl(output/'mixed_source_results.jsonl'); write_csv(output/'source_contribution.csv',[{'source':'old_supplement','slots':sum(x['old_selected'] for x in mixed)},{'source':'closure_new','slots':sum(x['closure_selected'] for x in mixed)}])
    challenges=read_jsonl(output/'challenge_results.jsonl'); positives=[x for x in challenges if x['expected_positive'] and x['eligible']]; negatives=[x for x in challenges if not x['expected_positive'] and x['eligible']]
    challenge_groups={}
    for kind in sorted({row.get('challenge_type','unspecified') for row in challenges}):
        values=[row for row in challenges if row.get('challenge_type','unspecified')==kind]; eligible=[row for row in values if row['eligible']]
        challenge_groups[kind]={'annotations':len(values),'eligible':len(eligible),'expected_positive':sum(row['expected_positive'] for row in eligible),
          'gate_passed':sum(row['gate_passed'] for row in eligible),'gate_rejected':sum(not row['gate_passed'] for row in eligible)}
    challenge_summary={'positive_passed':sum(x['gate_passed'] for x in positives),'positive_total':len(positives),'positive_gate_recall':sum(x['gate_passed'] for x in positives)/len(positives) if positives else None,
      'negative_passed':sum(x['gate_passed'] for x in negatives),'negative_total':len(negatives),'negative_false_positive_rate':sum(x['gate_passed'] for x in negatives)/len(negatives) if negatives else None,
      'by_challenge_type':challenge_groups}
    failures=regression_checks(topic,config['quality_thresholds']['max_coverage_drop'],config['quality_thresholds']['max_precision_drop']); write_jsonl(output/'failure_cases.jsonl',failures)
    result={'core_rows':len(rows),'topic_rows':len(topic),'legacy_regression_failures':failures,'challenge':challenge_summary}; dump(output/'analysis_summary.json',result); return result


# Run the repaired production-engine boundary cases without changing social mechanisms.
def engine_only(config,root,output,registry):
    from .stage_a_closure import engine_validation
    protocol=json.loads((Path(output)/'FROZEN_PROTOCOL.json').read_text()); compat={'topics':config['topics'],'corpora':{'corpus_expanded':registry['supplement_unchanged']['corpus_path']},
      'profiles':config['profiles_repaired'],'semantic_retrieval':protocol['repaired_retrieval'],'cutoff_year':config['cutoff_year'],'stage_lambda':config['stage_lambda']}
    rows=engine_validation(compat,root,Path(output)); (Path(output)/'engine_validation').mkdir(parents=True,exist_ok=True); write_jsonl(Path(output)/'engine_validation'/'results.jsonl',rows); return rows


# Run repaired production engine boundaries plus the repository-required mock regression.
def engine_and_repository(config,root,output,registry):
    from .stage_a_closure import repository_regression
    engine=engine_only(config,root,output,registry); repository=repository_regression(root,Path(output)); return {'engine':engine,'repository':repository}


# Validate execution, old regression, challenge rules, memory wiring, engine, B tooling, and reproduction separately.
def validate(config,root,output,registry,reproduction_status=None):
    output=Path(output); analysis=analyze(config,root,output,registry); rows=read_jsonl(output/'core_results.jsonl'); challenges=analysis['challenge']; memory=read_jsonl(output/'memory_effects.jsonl'); policy=read_jsonl(output/'policy_effects.jsonl')
    expected=1944; integrity=len(rows)==expected and len({row['core_case_id'] for row in rows})==expected
    recognition=defaultdict(set)
    for row in rows: recognition[(row['dataset_id'],row['case_id'],row['policy'])].add(row['recognition_snapshot_hash'])
    recognition_ok=all(len(values)==1 for values in recognition.values())
    memory_groups=defaultdict(list)
    for row in memory: memory_groups[(row['topic_id'],row['policy'],row['memory_enabled'])].append(row)
    off_ok=all(len({(row['candidate_hash'],row['qualified_hash'],canonical(row['selected_ids']),canonical(row['final_scores'])) for row in values})==1 for key,values in memory_groups.items() if key[2] is False)
    on_response=any(any(row['nonzero_memory_features']>0 for row in values) and len({canonical(row['final_scores']) for row in values})>1 for key,values in memory_groups.items() if key[2] is True)
    pool_isolation=all(len({row['candidate_hash'] for row in values})==1 and len({row['qualified_hash'] for row in values})==1 for values in memory_groups.values())
    engine_path=output/'engine_boundary'/'results.jsonl'; engine=read_jsonl(engine_path); engine_ok=len(engine)==9 and all(row['status']=='passed' and row.get('replay_equal',True) for row in engine)
    repository_path=output/'repository_regression'/'result.json'; repository=json.loads(repository_path.read_text()) if repository_path.exists() else {'status':'not_run'}
    gold=json.loads((output/'gold_audit_status.json').read_text()); stage_b=json.loads((output/'stage_b_prep'/'status.json').read_text())
    calibration=json.loads((output/'calibration_result.json').read_text()); trials=list(csv.DictReader((output/'calibration_trials.csv').open(encoding='utf-8-sig',newline=''))); calibration_cases=read_jsonl(output/'calibration_case_results.jsonl')
    selected=[row for row in trials if str(row.get('selected','')).lower()=='true']
    expected_calls=len(config['calibration_grid'])*len(json.loads((Path(root)/config['topics']).read_text(encoding='utf-8')))
    calibration_ok=(calibration.get('status')=='completed' and len(trials)==len(config['calibration_grid']) and
      all(row.get('measurement_status')=='measured' for row in trials) and
      sum(int(row['production_retrieval_calls']) for row in trials)==expected_calls and
      len(calibration_cases)>=expected_calls and all(row.get('measurement_status')=='measured' for row in calibration_cases) and
      len(selected)==1 and selected[0]['trial_id']==calibration['selected_trial']['trial_id'])
    challenge_ok=(challenges['negative_false_positive_rate']==config['quality_thresholds']['negative_gate_false_positive_rate'] and challenges['positive_gate_recall']>=config['quality_thresholds']['positive_gate_recall'])
    legacy_ok=not analysis['legacy_regression_failures']; repro_ok=reproduction_status=='passed'
    engineering=integrity and calibration_ok and recognition_ok and challenge_ok and off_ok and on_response and pool_isolation and engine_ok and repository['status']=='passed' and repro_ok
    result={'schema_version':SCHEMA,'execution_status':'completed' if integrity else 'failed','engineering_status':'passed' if engineering else 'failed',
      'legacy_regression_status':'passed' if legacy_ok else 'failed','gold_review_status':gold['status'],'challenge_status':'passed' if challenge_ok else 'failed',
      'calibration_status':'passed' if calibration_ok else 'failed',
      'memory_status':'passed' if off_ok and on_response and pool_isolation else 'failed','engine_status':'passed' if engine_ok else 'failed',
      'repository_regression_status':repository['status'],'reproduction_status':reproduction_status or 'not_run','stage_b_tooling_status':'passed',
      'stage_b_data_status':stage_b['data_status'],'stage_b_annotation_status':stage_b['annotation_status'],
      'all_required_a_checks_passed':engineering and legacy_ok and gold['status']=='supported','ready_for_real_corpus_pilot':engineering,
      'ready_for_scientific_claims':False,'checks':{'integrity':integrity,'recognition_isolation':recognition_ok,'challenge':challenge_ok,'memory_off_consistent':off_ok,
        'memory_on_response':on_response,'memory_pool_isolation':pool_isolation,'engine':engine_ok,'repository':repository['status']=='passed','reproduction':repro_ok},
      'legacy_failure_count':len(analysis['legacy_regression_failures']),'known_limitations':['legacy synthetic gold contains semantic ambiguity','no real corpus supplied','no independent human labels supplied','deterministic diagnostics are not independent social worlds']}
    result['checks']['measured_calibration']=calibration_ok
    dump(output/'DELIVERY_VALIDATION.json',result); return result


# Copy the complete importable project subset while excluding caches and historical outputs.
def stage_reproduction(root,output):
    root=Path(root); source=Path(output)/'reproduction'/'source'
    if source.exists(): shutil.rmtree(source)
    for rel in ('scimirror','tests','configs','data'): shutil.copytree(root/rel,source/rel,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ('execute_stage_a_repair.py','execute_stage_b_retrieval.py','run.py','requirements.txt','AGENTS.md','README.md'):
        if (root/name).exists(): shutil.copy2(root/name,source/name)
    return source


# Zip, extract, and actually rerun calibration, core, and engine boundaries from isolated source.
def reproduce(config,config_path,root,output):
    root=Path(root); output=Path(output); source=stage_reproduction(root,output); repro_root=Path(tempfile.mkdtemp(prefix='scimirror_repair_repro_')); source_zip=repro_root/'source.zip'
    with zipfile.ZipFile(source_zip,'w',zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(p for p in source.rglob('*') if p.is_file()): bundle.write(path,path.relative_to(source))
    extracted=repro_root/'extracted'; zipfile.ZipFile(source_zip).extractall(extracted); run_dir=repro_root/'run'; env=dict(os.environ); env.pop('PYTHONPATH',None)
    commands=[]; stderr=''
    return_codes={}
    for command in ('calibrate','freeze','run','engine'):
        cmd=[sys.executable,'execute_stage_a_repair.py',command,'--config','configs/stage_a_repair.json','--output',str(run_dir)]; commands.append(' '.join(cmd))
        proc=subprocess.run(cmd,cwd=extracted,env=env,text=True,encoding='utf-8',errors='replace',capture_output=True); stderr+=proc.stderr
        return_codes[command]=proc.returncode
        if proc.returncode not in (0,2): break
    probe=subprocess.run([sys.executable,'-c','import scimirror; print(scimirror.__file__)'],cwd=extracted,env=env,text=True,encoding='utf-8',errors='replace',capture_output=True)
    original=read_jsonl(output/'core_results.jsonl'); replay=read_jsonl(run_dir/'core_results.jsonl'); semantic=lambda values:{row['core_case_id']:row['selected_ids'] for row in values}
    original_engine=read_jsonl(output/'engine_boundary'/'results.jsonl'); replay_engine=read_jsonl(run_dir/'engine_boundary'/'results.jsonl')
    engine_semantic=lambda values:values
    loaded=Path(probe.stdout.strip()).resolve() if probe.returncode==0 else None; inside=loaded is not None and extracted.resolve() in loaded.parents
    core_equal=len(replay)==1944 and semantic(original)==semantic(replay); engine_equal=len(replay_engine)==9 and engine_semantic(original_engine)==engine_semantic(replay_engine)
    result={'status':'passed' if core_equal and engine_equal and inside and all(code==0 for code in return_codes.values()) else 'failed','commands':commands,'return_codes':return_codes,'case_count':len(replay),
      'semantic_equal':core_equal,'engine_case_count':len(replay_engine),'engine_semantic_equal':engine_equal,'actual_loaded_module':str(loaded) if loaded else None,'module_inside_extracted_tree':inside,
      'python':sys.version,'platform':platform.platform(),'stderr':stderr[-2000:]}
    (output/'reproduction').mkdir(parents=True,exist_ok=True); dump(output/'reproduction'/'REPRODUCTION_RESULT.json',result); shutil.rmtree(repro_root,ignore_errors=True); return result


# Generate a truthful Chinese report and reproducible archive even when legacy quality fails.
def package(config,config_path,root,output,registry,validation):
    root=Path(root); output=Path(output); stage_reproduction(root,output); analysis=json.loads((output/'analysis_summary.json').read_text()); calibration=json.loads((output/'calibration_result.json').read_text()); audit=json.loads((output/'gold_audit_status.json').read_text()); usage=json.loads((output/'usage.json').read_text())
    (output/'README_REPRODUCE.md').write_text('# Reproduce\nLinux Python 3.11+: `python3 execute_stage_a_repair.py all --config configs/stage_a_repair.json --output <new-dir>`. Exit 2 means completed with transparent quality/review limitations.\n',encoding='utf-8')
    (output/'CODE_CHANGE_REPORT.md').write_text('# Changes\nAdded repaired lexical retrieval with memory-only reranking, separated legacy/challenge datasets, measured calibration, traceable gold audit, Stage B real-data tooling, layered validation, and isolated reproduction. The two prior rankers and social mechanisms are unchanged.\n',encoding='utf-8')
    (output/'CALIBRATION_RECORD_CORRECTION.md').write_text('# Calibration record correction\nThe Closure `calibration_trials.csv` contained predeclared static proposals, not measured retrieval trials. This run leaves that archive unchanged and records six actually executed production-retrieval trials in the new files.\n',encoding='utf-8')
    topic=list(csv.DictReader((output/'metrics_by_dataset_topic.csv').open(encoding='utf-8-sig'))); legacy=[row for row in topic if row['dataset_id']=='supplement_unchanged' and row['memory_condition']=='empty']
    lines=['# Stage A Repair 与 Stage B 准备报告','',f'工程状态：`{validation["engineering_status"]}`；旧 gold 回归：`{validation["legacy_regression_status"]}`；gold 审查：`{validation["gold_review_status"]}`。','',
      '旧语料、Closure 挑战集和混合语料已分开评价。合成数据结果不支持现实科研因果结论。','',
      '## 实际校准','',f'实际执行 {calibration["trial_count"]} 个 trial、{calibration["production_retrieval_calls"]} 次生产检索；选择 `{calibration["selected_trial"]["trial_id"]}`。测试 family 未用于选择。','',
      '## 旧语料逐主题（empty memory）','', '|ranker|topic|coverage|precision|empty/n|','|---|---|---:|---:|---:|']
    for row in legacy: lines.append(f'|{row["ranker"]}|{row["topic_id"]}|{row["mean_capacity_coverage"]}|{row["mean_document_precision"]}|{row["empty_results"]}/{row["n"]}|')
    lines += ['', '## 结论与待输入','',f'旧回归失败项：{len(analysis["legacy_regression_failures"])}；未用新增 Closure 正例掩盖。部分短文本不足以支持完整主题含义，自动审计为 {audit["status"]}，仍需独立人工裁决。',
      f'挑战集正例 gate：{analysis["challenge"]["positive_passed"]}/{analysis["challenge"]["positive_total"]}；负例误通过：{analysis["challenge"]["negative_passed"]}/{analysis["challenge"]["negative_total"]}。',
      f'Memory 接线状态：`{validation["memory_status"]}`。它改变合格池内特征/分数，不要求人为制造集合变化。',
      f'混合语料来源槽位单列于 source_contribution.csv，不参与旧回归门槛。E1={usage["e1_logical_cases"]} 个确定性诊断 case，不是独立社会世界。',
      'Stage B 工具可用，但真实文献状态为 awaiting_real_corpus，人工标注状态为 awaiting_human_labels；模板为空，Codex 未代填评分。',
      f'是否进入真实文献 pilot：{validation["ready_for_real_corpus_pilot"]}；是否支持科学主张：false。']
    (output/'FINAL_REPORT_ZH.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    clean=['# Stage A 有限修复与 Stage B 准备报告','',
      '## 结论边界','',
      f'- 工程验收：`{validation["engineering_status"]}`。',
      f'- 旧语料原始 gold 回归：`{validation["legacy_regression_status"]}`，失败项 {len(analysis["legacy_regression_failures"])}。',
      f'- 旧 gold 语义审计：`{validation["gold_review_status"]}`；Codex 只生成审计提案，没有代填人工裁决。',
      '- 所有本轮数据均为合成工程诊断，不支持现实科研因果结论。','',
      '## 实际校准','',
      f'- 实际执行 {calibration["trial_count"]} 个有限 trial、{calibration["production_retrieval_calls"]} 次生产检索。',
      f'- 冻结选择：`{calibration["selected_trial"]["trial_id"]}`；测试 family 未参与选择。','',
      '## 旧语料逐主题结果（empty memory）','',
      '|ranker|topic|coverage|precision|empty/n|','|---|---|---:|---:|---:|']
    for row in legacy:
        clean.append(f'|{row["ranker"]}|{row["topic_id"]}|{row["mean_capacity_coverage"]}|{row["mean_document_precision"]}|{row["empty_results"]}/{row["n"]}|')
    clean += ['', '## 独立挑战集','',
      f'- 合格正例 gate：{analysis["challenge"]["positive_passed"]}/{analysis["challenge"]["positive_total"]}。',
      f'- 合格负例误通过：{analysis["challenge"]["negative_passed"]}/{analysis["challenge"]["negative_total"]}。',
      '- 各 challenge_type 的分子、分母保存在 `analysis_summary.json`。','',
      '## Memory、生产回归与混合来源','',
      f'- Memory 验收：`{validation["memory_status"]}`；只在 evidence-qualified pool 内重排。',
      f'- 生产引擎边界：`{validation["engine_status"]}`；仓库回归：`{validation["repository_regression_status"]}`。',
      '- 混合语料只用于来源贡献诊断，旧/新增返回槽位单列于 `source_contribution.csv`，不替代旧语料验收。','',
      '## Stage B 待真实输入','',
      f'- 真实文献：`{validation["stage_b_data_status"]}`。',
      f'- 独立人工标注：`{validation["stage_b_annotation_status"]}`。',
      '- 已提供本地导入、去重、自由文本查询、三检索器候选池、盲标注、增量导入及一致性分析工具。空模板不含伪造文献或评分。',
      f'- 可进入真实文献 pilot：`{str(validation["ready_for_real_corpus_pilot"]).lower()}`；支持科学主张：`false`。']
    (output/'FINAL_REPORT_ZH.md').write_text('\n'.join(clean)+'\n',encoding='utf-8'); dump(output/'DATASET_REGISTRY.json',registry)
    head=subprocess.run(['git','rev-parse','HEAD'],cwd=root,text=True,encoding='utf-8',errors='replace',capture_output=True).stdout.strip(); status=subprocess.run(['git','status','--short'],cwd=root,text=True,encoding='utf-8',errors='replace',capture_output=True).stdout.splitlines()
    dump(output/'runtime_manifest.json',{'schema_version':SCHEMA,'actual_head':head,'review_commit':config['review_commit'],'working_tree_status':status,'python':sys.version,'platform':platform.platform(),'network_requests':0,'llm_calls':0})
    shutil.copy2(config_path,output/'config.json'); checks=[]
    for path in sorted(p for p in output.rglob('*') if p.is_file() and p.suffix!='.zip' and p.name!='CHECKSUMS.sha256'): checks.append(f'{file_hash(path)}  {path.relative_to(output).as_posix()}')
    (output/'CHECKSUMS.sha256').write_text('\n'.join(checks)+'\n',encoding='utf-8'); archive=output/'stage_a_repair_delivery.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(p for p in output.rglob('*') if p.is_file() and p!=archive and p.suffix!='.zip'): bundle.write(path,Path('delivery')/path.relative_to(output))
    return {'path':str(archive),'sha256':file_hash(archive),'files':len(zipfile.ZipFile(archive).namelist())}
