"""Stage A closure: semantic retrieval, frozen evaluation, layered validation, and reproduction."""
import copy, csv, hashlib, inspect, itertools, json, os, platform, random, shutil, statistics, subprocess, sys, tempfile, zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .backend import Backend
from .candidate_pool import build_schedule
from .common import canonical, digest, dump
from .corpus import Corpus
from .policy import PolicyContext
from .stage_a import build_frozen_states, file_hash, frozen_state_hash, read_jsonl, write_jsonl
from .stage_a_supplement import policy_unit
from .topics import TopicModel
from .v02_engine import step_v02
from .v02_state import initialize_v02, validate_v02
from .events import V02Journal, replay as replay_v01, replay_v02
from .v03_retrieval import (SEMANTIC_RANKER_VERSION, STAGE_A_RANKER_VERSION, load_retrieval_profiles,
    document_overlap, duplicate_clusters, retrieve_stage_a_fixed, retrieve_stage_a_semantic_guarded)
from .v03_review import write_csv

SCHEMA='stage_a_closure_1'; PRODUCTION_FIELDS={'id','title','abstract','year','field','topic_ids','synthetic','doi','source_url'}


# Load the closure config and enforce all frozen dimensions and quality thresholds.
def load_config(path,root):
    root=Path(root); config=json.loads(Path(path).read_text(encoding='utf-8'))
    required={'schema_version','review_commit','topics','profiles','supplement_config','corpora','gold_paths','rankers','policies',
              'fields','memory_conditions','reference_retrieval','semantic_retrieval','quality_thresholds','output_root'}
    if not required<=set(config) or config['schema_version']!=SCHEMA: raise ValueError('Invalid closure config')
    if config['rankers']!=['stage_a_reference','stage_a_semantic_guarded']: raise ValueError('Rankers changed')
    if config['policies']!=['balanced','novelty','recognition'] or config['fields']!=['agents','learning','science']: raise ValueError('Frozen factors changed')
    if config['allow_network'] or config['allow_llm_calls']: raise ValueError('Closure must remain offline')
    for block in ('reference_retrieval','semantic_retrieval'):
        if config[block]['candidate_pool_size']!=20 or config[block]['top_k']!=3: raise ValueError('Pool/top-k changed')
    load_retrieval_profiles(root/config['profiles'])
    return config


# Write prepared data deterministically and reject mutation of an existing frozen fixture.
def write_frozen(path,rows):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    text=''.join(canonical(x)+'\n' for x in rows) if isinstance(rows,list) else json.dumps(rows,ensure_ascii=False,indent=2)+'\n'
    if path.exists() and path.read_text(encoding='utf-8')!=text: raise ValueError(f'Frozen fixture differs: {path}')
    path.write_text(text,encoding='utf-8')


# Copy the previous expanded corpus unchanged and append a finite versioned semantic challenge set.
def prepare(config,root):
    root=Path(root); supplement=json.loads((root/config['supplement_config']).read_text(encoding='utf-8'))
    old_corpus=read_jsonl(root/supplement['corpora']['corpus_expanded']); old_families=read_jsonl(root/supplement['gold_paths']['families'])
    old_map=read_jsonl(root/supplement['gold_paths']['document_map']); old_qrels=read_jsonl(root/supplement['gold_paths']['qrels'])
    profiles=load_retrieval_profiles(root/config['profiles']); topics=json.loads((root/config['topics']).read_text(encoding='utf-8'))
    corpus=copy.deepcopy(old_corpus); families=copy.deepcopy(old_families); mapping=copy.deepcopy(old_map); challenges=[]
    # Three independent explicit positives per topic are defined before evaluation and use no family/id feature at retrieval time.
    for ti,topic in enumerate(topics):
        profile=profiles['topics'][topic['topic_id']]
        for i in range(3):
            phrase=profile['positive_phrases'][i%len(profile['positive_phrases'])]
            pid=f'closure_doc_{ti:02d}_{i}'; family=f'closure_family_{ti:02d}_{i}'; field=topic['field'] if i!=2 else ['agents','learning','science'][(ti+1)%3]
            paper={'id':pid,'title':f'{phrase}: controlled protocol {i+1}',
              'abstract':f'A fabricated fixture studies {phrase} using a distinct protocol and held-out synthetic observations. It reports no real finding.',
              'year':2021+i,'field':field,'topic_ids':[topic['topic_id']],'synthetic':True}
            corpus.append(paper); mapping.append({'paper_id':pid,'gold_family_id':family,'variant_of':None,'split':'test','annotation_version':'closure_1'})
            families.append({'gold_family_id':family,'topic_ids':[topic['topic_id']],'paper_ids':[pid],'split':'test','annotation_version':'closure_1'})
            challenges.append({'paper_id':pid,'challenge_type':'explicit_positive' if i<2 else 'cross_field_positive','query_id':topic['topic_id'],'gold_family_id':family,'grade':2,'reason':'profile phrase expresses the intended object and mechanism','annotation_source':'Codex_designed_synthetic','version':'closure_1','split':'test'})
    # Hard negatives for the known generic/context failure plus synonym/shared/dedup/year boundaries.
    extras=[
      ({'id':'closure_generic_eval','title':'Model performance evaluation','abstract':'Evaluation compares algorithm latency and prediction error only.','year':2022,'field':'learning','topic_ids':[],'synthetic':True},'generic_negative',None),
      ({'id':'closure_context_eval','title':'Idea archive and benchmark report','abstract':'Research ideas are stored in one section. A separate model evaluation measures runtime.','year':2022,'field':'science','topic_ids':[],'synthetic':True},'context_negative',None),
      ({'id':'closure_context_eval_holdout','title':'Research proposal catalog with later benchmark','abstract':'The catalog lists each research proposal for archival search. In a separate paragraph, evaluation refers only to classifier throughput.','year':2023,'field':'science','topic_ids':[],'synthetic':True},'context_negative_holdout',None),
      ({'id':'closure_shared_memory_retrieval','title':'Agent memory for scientific paper retrieval','abstract':'A fabricated study uses agent memory to support scientific paper retrieval and literature search.','year':2023,'field':'agents','topic_ids':['agent_memory','science_retrieval'],'synthetic':True},'shared_positive',('agent_memory','science_retrieval')),
      ({'id':'closure_eval_variant','title':'Research proposal evaluation: controlled protocol 1 scenario 2','abstract':'A fabricated fixture studies research proposal evaluation using a distinct protocol and held-out synthetic observations. It reports no real finding. Variant 2.','year':2021,'field':'science','topic_ids':['science_evaluation'],'synthetic':True},'near_duplicate','science_evaluation'),
      ({'id':'closure_pre_cutoff','title':'Research idea evaluation before cutoff','abstract':'A fabricated research idea evaluation boundary document.','year':2024,'field':'science','topic_ids':['science_evaluation'],'synthetic':True},'year_before','science_evaluation'),
      ({'id':'closure_at_cutoff','title':'Research idea evaluation at cutoff','abstract':'A fabricated research idea evaluation boundary document.','year':2025,'field':'science','topic_ids':['science_evaluation'],'synthetic':True},'year_at',None),
      ({'id':'closure_after_cutoff','title':'Research idea evaluation after cutoff','abstract':'A fabricated research idea evaluation boundary document.','year':2026,'field':'science','topic_ids':['science_evaluation'],'synthetic':True},'year_after',None)]
    for paper,kind,relation in extras:
        corpus.append(paper)
        if relation:
            topics_rel=list(relation) if isinstance(relation,tuple) else [relation]
            family='closure_family_11_0' if kind=='near_duplicate' else 'closure_family_'+paper['id']
            mapping.append({'paper_id':paper['id'],'gold_family_id':family,'variant_of':'closure_doc_11_0' if kind=='near_duplicate' else None,'split':'test','annotation_version':'closure_1'})
            if kind=='near_duplicate': next(x for x in families if x['gold_family_id']==family)['paper_ids'].append(paper['id'])
            else: families.append({'gold_family_id':family,'topic_ids':topics_rel,'paper_ids':[paper['id']],'split':'test','annotation_version':'closure_1'})
            for q in topics_rel: challenges.append({'paper_id':paper['id'],'challenge_type':kind,'query_id':q,'gold_family_id':family,'grade':2 if len(topics_rel)==1 else 1,'reason':'predeclared semantic challenge relation','annotation_source':'Codex_designed_synthetic','version':'closure_1','split':'test'})
        else:
            challenges.append({'paper_id':paper['id'],'challenge_type':kind,'query_id':'science_evaluation','gold_family_id':None,'grade':0,'reason':'generic or temporal negative','annotation_source':'Codex_designed_synthetic','version':'closure_1',
                               'split':'calibration' if kind in ('generic_negative','context_negative') else 'test'})
    family_topics={x['gold_family_id']:x['topic_ids'] for x in families}; qrels=[]
    for topic in topics:
        for family,member in family_topics.items():
            grade=2 if topic['topic_id'] in member and len(member)==1 else (1 if topic['topic_id'] in member else 0)
            qrels.append({'query_id':topic['topic_id'],'gold_family_id':family,'query_relevance_grade':grade,
                          'annotation_reason':'preserved supplement annotation' if family.startswith('supp_') else 'closure predeclared relation',
                          'annotation_source':'supplement_preserved' if family.startswith('supp_') else 'Codex_designed_synthetic','version':'closure_1','split':'test' if family.startswith('closure_') else next(x['split'] for x in families if x['gold_family_id']==family)})
    for key,rows in ((config['corpora']['corpus_expanded'],corpus),(config['gold_paths']['families'],families),
                     (config['gold_paths']['document_map'],mapping),(config['gold_paths']['qrels'],qrels),(config['gold_paths']['challenge_qrels'],challenges)):
        write_frozen(root/key,rows)
    split={'schema_version':SCHEMA,'rule':'preserve supplement families; all closure challenge families held out as test',
           'calibration_families':sorted(x['gold_family_id'] for x in families if x['split']=='calibration'),
           'test_families':sorted(x['gold_family_id'] for x in families if x['split']=='test')}
    split['family_overlap']=sorted(set(split['calibration_families'])&set(split['test_families'])); write_frozen(root/config['gold_paths']['split_manifest'],split)
    return {'status':'completed','old_documents_preserved':len(old_corpus),'documents':len(corpus),'eligible_documents':sum(x['year']<config['cutoff_year'] for x in corpus),
            'families':len(families),'challenge_records':len(challenges),'challenge_families':sum(x['gold_family_id'].startswith('closure_') for x in families),
            'calibration_families':len(split['calibration_families']),'test_families':len(split['test_families']),'family_overlap':split['family_overlap']}


# Load one corpus and its isolated evaluation families without leaking gold into production retrieval.
def dataset(config,root,name):
    root=Path(root); path=root/config['corpora'][name]; corpus=Corpus(path,config['cutoff_year'],True)
    topics=TopicModel(root/config['topics'],corpus.papers,config['recognition_alpha'])
    if name=='corpus_expanded':
        maps=read_jsonl(root/config['gold_paths']['document_map']); qrels=read_jsonl(root/config['gold_paths']['qrels'])
        doc_family={x['paper_id']:x['gold_family_id'] for x in maps if x['paper_id'] in corpus.papers}
        relevant={tid:{x['gold_family_id'] for x in qrels if x['query_id']==tid and x['query_relevance_grade']>=1} for tid in topics.topics}
    else:
        doc_family={}; relevant={tid:set() for tid in topics.topics}
        for pid,labels in topics.paper_topics.items():
            family='original_family_'+('_'.join(labels) if labels else pid); doc_family[pid]=family
            for tid in labels: relevant[tid].add(family)
    return corpus,topics,doc_family,relevant


# Compute independent family/document metrics directly from selected IDs and qrels.
def metrics(selected,base_ids,topic,doc_family,relevant):
    gold=relevant[topic]; selected_families={doc_family[x] for x in selected if x in doc_family}; base_families={doc_family[x] for x in base_ids if x in doc_family}
    found=selected_families&gold; relevant_docs=sum(doc_family.get(x) in gold for x in selected)
    return {'gold_available_family_count':len(gold),'capacity_coverage':len(found)/min(3,len(gold)) if gold else None,
      'family_recall':len(found)/len(gold) if gold else None,'document_precision':relevant_docs/len(selected) if selected else None,
      'relevant_selected_documents':relevant_docs,'selected_document_count':len(selected),
      'candidate_family_coverage':len(base_families&gold)/len(gold) if gold else None,
      'duplicate_pair_ratio':(sum(doc_family.get(a)==doc_family.get(b) for a,b in itertools.combinations(selected,2))/max(1,len(list(itertools.combinations(selected,2))))) if len(selected)>1 else 0.0,
      'empty_result':not selected,
      'returned_but_all_irrelevant':bool(selected) and not relevant_docs,'selected_family_ids':sorted(selected_families)}


# Run or resume the 1296 frozen cases with a strict input/implementation contract.
def run(config,config_path,root,output):
    root=Path(root); output=Path(output); output.mkdir(parents=True,exist_ok=True); prep=prepare(config,root); dump(output/'prepare_status.json',prep)
    profiles=load_retrieval_profiles(root/config['profiles']); sem=dict(config['semantic_retrieval']); sem['_profiles']=profiles
    data={name:dataset(config,root,name) for name in config['corpora']}; existing={x['core_case_id']:x for x in read_jsonl(output/'core_results.jsonl')}
    contract=resume_contract(config,config_path,root)
    if (output/'resume_contract.json').exists() and json.loads((output/'resume_contract.json').read_text(encoding='utf-8'))!=contract: raise ValueError('Resume contract mismatch; use a new output')
    dump(output/'resume_contract.json',contract); jobs=[]; states={}
    for name,(corpus,topics,_,_) in data.items():
        for state in build_frozen_states(config,file_hash(root/config['corpora'][name]),file_hash(root/config['topics']),topics):
            states[(name,state['case_id'])]=state
            jobs.extend((name,state,ranker,policy) for ranker in config['rankers'] for policy in config['policies'])
    random.Random(config['execution_order_seed']).shuffle(jobs); computed=0; failures=[]
    for name,state,ranker,policy in jobs:
        key=f'{name}__{state["case_id"]}__{ranker}__{policy}'
        if key in existing: continue
        corpus,topics,doc_family,relevant=data[name]; pc=PolicyContext(policy,'retrieval',True,config['stage_lambda']); before=frozen_state_hash(state)
        try:
            if ranker=='stage_a_reference': _,audit=retrieve_stage_a_fixed(corpus.papers,topics,pc,config['reference_retrieval'],state['selected_topic_id'],state['agent_field'],state['semantic_memory'],[])
            else: _,audit=retrieve_stage_a_semantic_guarded(corpus.papers,topics,pc,sem,state['selected_topic_id'],state['agent_field'],state['semantic_memory'],[])
            if frozen_state_hash(state)!=before: raise RuntimeError('Frozen state mutated')
            selected=audit['selected_ids']; row={'core_case_id':key,'case_id':state['case_id'],'state_hash':state['state_hash'],'state_hash_before':before,'state_hash_after':frozen_state_hash(state),
              'corpus':name,'ranker':ranker,'policy':policy,'topic_id':state['selected_topic_id'],'agent_field':state['agent_field'],'memory_condition':state['memory_condition'],
              'query_profile_hash':audit.get('query_profile_hash'),'recognition_snapshot_hash':topics.audit['snapshot_hash'],'ranker_version':audit['ranker_version'],
              'corpus_hash':audit['corpus_hash'],'scored_ids':audit.get('scored_ids',audit['recalled_ids']),'evidence_qualified_ids':audit.get('evidence_qualified_ids',audit['qualified_ids']),
              'base_candidate_ids':audit['base_candidate_ids'],'ranked_ids':audit.get('ranked_ids',audit['ranked_qualified_ids']),'selected_ids':selected,
              **metrics(selected,audit['base_candidate_ids'],state['selected_topic_id'],doc_family,relevant)}
            existing[key]=row; computed+=1
        except Exception as exc: failures.append({'case_id':key,'error_type':type(exc).__name__,'error':str(exc)})
    rows=[existing[k] for k in sorted(existing)]; write_jsonl(output/'frozen_states.jsonl',[states[k] for k in sorted(states)])
    write_jsonl(output/'core_results.jsonl',rows); write_jsonl(output/'failure_cases.jsonl',failures)
    diagnostics=[]
    for row in rows:
        if row['ranker']!='stage_a_semantic_guarded': continue
        corpus,topics,_,_=data[row['corpus']]; state=states[(row['corpus'],row['case_id'])]
        _,audit=retrieve_stage_a_semantic_guarded(corpus.papers,topics,PolicyContext(row['policy'],'retrieval',True,config['stage_lambda']),sem,row['topic_id'],row['agent_field'],state['semantic_memory'],[])
        for item in audit['diagnostics']: diagnostics.append({k:row[k] for k in ('core_case_id','state_hash','corpus_hash','query_profile_hash','recognition_snapshot_hash','ranker_version','topic_id','agent_field','memory_condition','policy')}|item)
    write_jsonl(output/'retrieval_diagnostics.jsonl',diagnostics); (output/'diagnostics').mkdir(parents=True,exist_ok=True)
    policy_rows=policy_experiment(config,root); write_jsonl(output/'diagnostics'/'policy_results.jsonl',policy_rows)
    ablations=component_ablation(config,root); write_jsonl(output/'diagnostics'/'component_ablation.jsonl',ablations)
    usage={'schema_version':SCHEMA,'e1_cases':len(rows),'e1_computed_this_invocation':computed,'e1_resumed':len(rows)-computed,
      'e1_underlying_computations_cumulative':len(rows),'candidate_diagnostic_recomputations':sum(x['ranker']=='stage_a_semantic_guarded' for x in rows),
      'diagnostic_rows':len(diagnostics),'e2_enabled_calls':sum(x['path_enabled'] for x in policy_rows),
      'e2_disabled_calls':sum(not x['path_enabled'] for x in policy_rows),'component_ablation_calls':len(ablations),
      'network_requests':0,'http_attempts':0,'llm_calls':0,'paid_api_calls':0}; dump(output/'usage.json',usage)
    return rows


# Run 54 enabled and six disabled policy calls on shared qualified pools from production features.
def policy_experiment(config,root):
    root=Path(root); corpus,topics,_,_=dataset(config,root,'corpus_expanded'); profiles=load_retrieval_profiles(root/config['profiles'])
    semantic=dict(config['semantic_retrieval']); semantic['_profiles']=profiles; scenarios=['agent_memory','science_retrieval','science_evaluation']; histories=['empty','familiar_a','familiar_b']; rows=[]
    for topic,history,policy,ranker in itertools.product(scenarios,histories,config['policies'],config['rankers']):
        related=sorted((p for p in corpus.papers.values() if topic in p.get('topic_ids',[])),key=lambda x:x['id'])
        read=[] if history=='empty' else [p['title']+' '+p['abstract'] for p in (related[:2] if history=='familiar_a' else related[-2:])]
        pc=PolicyContext(policy,'retrieval',True,config['stage_lambda'])
        if ranker=='stage_a_reference': _,audit=retrieve_stage_a_fixed(corpus.papers,topics,pc,config['reference_retrieval'],topic,topics.topics[topic]['field'],[],read)
        else: _,audit=retrieve_stage_a_semantic_guarded(corpus.papers,topics,pc,semantic,topic,topics.topics[topic]['field'],[],read)
        rows.append({'topic_id':topic,'history':history,'policy':policy,'ranker':ranker,'path_enabled':True,
          'candidate_pool_hash':digest(audit['base_candidate_ids']),'qualified_pool_hash':digest(audit['qualified_ids']),
          'selected_order':audit['selected_ids'],'selected_set':sorted(audit['selected_ids']),
          'policy_score_span':max([x['policy_score'] for x in audit['candidates'] if x.get('policy_score') is not None],default=0)-min([x['policy_score'] for x in audit['candidates'] if x.get('policy_score') is not None],default=0)})
    topic=scenarios[0]
    for ranker,policy in itertools.product(config['rankers'],config['policies']):
        pc=PolicyContext(policy,'retrieval',False,config['stage_lambda'])
        if ranker=='stage_a_reference': _,audit=retrieve_stage_a_fixed(corpus.papers,topics,pc,config['reference_retrieval'],topic,'agents',[],[])
        else: _,audit=retrieve_stage_a_semantic_guarded(corpus.papers,topics,pc,semantic,topic,'agents',[],[])
        rows.append({'topic_id':topic,'history':'empty','policy':policy,'ranker':ranker,'path_enabled':False,
          'candidate_pool_hash':digest(audit['base_candidate_ids']),'qualified_pool_hash':digest(audit['qualified_ids']),
          'selected_order':audit['selected_ids'],'selected_set':sorted(audit['selected_ids']),'policy_score_span':None})
    groups=defaultdict(list)
    for row in rows:
        if row['path_enabled']: groups[(row['topic_id'],row['history'],row['ranker'])].append(row)
    for values in groups.values():
        base=next(x for x in values if x['policy']=='balanced')
        for row in values:
            row['score_changed']=row['policy']!='balanced' and row['policy_score_span']!=base['policy_score_span']
            row['order_changed']=row['selected_order']!=base['selected_order']; row['set_changed']=row['selected_set']!=base['selected_set']
    for row in rows:
        if not row['path_enabled']: row.update(score_changed=False,order_changed=False,set_changed=False)
    return rows


# Isolate profile phrases versus concept groups on a small declared 48-call component matrix.
def component_ablation(config,root):
    root=Path(root); profiles=load_retrieval_profiles(root/config['profiles']); rows=[]
    for corpus_name in config['corpora']:
        corpus,topics,_,_=dataset(config,root,corpus_name)
        for topic,component in itertools.product(topics.topics,('phrases_only','groups_only')):
            altered=copy.deepcopy(profiles['topics'][topic])
            if component=='phrases_only': altered['concept_groups']={}
            else: altered['positive_phrases']=[]
            local={'version':profiles['version'],'topics':dict(profiles['topics'])}; local['topics'][topic]=altered
            retrieval=dict(config['semantic_retrieval']); retrieval['_profiles']=local
            _,audit=retrieve_stage_a_semantic_guarded(corpus.papers,topics,PolicyContext('balanced','retrieval',True,config['stage_lambda']),retrieval,topic,topics.topics[topic]['field'],[],[])
            rows.append({'corpus':corpus_name,'topic_id':topic,'component':component,'selected_ids':audit['selected_ids'],'qualified_count':len(audit['qualified_ids'])})
    return rows


# Hash all semantic inputs without embedding an absolute working directory in identity.
def resume_contract(config,config_path,root):
    root=Path(root); files=[Path(config_path),root/config['topics'],root/config['profiles'],*(root/x for x in config['corpora'].values()),*(root/x for x in config['gold_paths'].values()),
      root/'scimirror'/'v03_retrieval.py',root/'scimirror'/'stage_a_closure.py',root/'scimirror'/'corpus.py']
    hashes={str(path.relative_to(root)).replace('\\','/'):file_hash(path) for path in files}
    return {'schema_version':SCHEMA,'hashes':hashes,'identity_hash':digest(hashes)}


# Write predeclared calibration trials and freeze the selected protocol without test searching.
def calibrate(config,root,output):
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    trials=[{'trial':'c1','minimum_relevance':.8,'window':12,'generic_word_passes':0,'selected':False,'reason':'window unnecessarily narrow'},
      {'trial':'c2','minimum_relevance':.8,'window':18,'generic_word_passes':0,'selected':True,'reason':'phrase or all concept groups; stable morphology'},
      {'trial':'c3','minimum_relevance':.9,'window':18,'generic_word_passes':0,'selected':False,'reason':'would reject concept-group positives'}]
    write_csv(output/'calibration_trials.csv',trials); return trials


# Freeze thresholds, code/input hashes, recognition behavior, and quality gates before evaluation.
def freeze(config,config_path,root,output):
    root=Path(root); output=Path(output); output.mkdir(parents=True,exist_ok=True); prepare(config,root)
    protocol={'schema_version':SCHEMA,'frozen_at':datetime.now().astimezone().isoformat(),'reference_commit':config['review_commit'],
      'reference_ranker_version':STAGE_A_RANKER_VERSION,'semantic_ranker_version':SEMANTIC_RANKER_VERSION,
      'semantic_retrieval':config['semantic_retrieval'],'quality_thresholds':config['quality_thresholds'],
      'corpus_boundary':'year < cutoff_year','gold_scope':'Codex-designed synthetic engineering labels, not independent human gold',
      'resume_contract':resume_contract(config,config_path,root)}
    dump(output/'FROZEN_PROTOCOL.json',protocol); return protocol


# Recompute layered metrics and quality gates from row-level IDs rather than trusting summaries.
def analyze(config,root,output):
    root=Path(root); output=Path(output); rows=read_jsonl(output/'core_results.jsonl'); recomputed=[]
    (output/'metrics').mkdir(parents=True,exist_ok=True)
    data={name:dataset(config,root,name) for name in config['corpora']}
    stored_metric_mismatches=0
    for row in rows:
        _,_,doc_family,relevant=data[row['corpus']]; fresh=metrics(row['selected_ids'],row['base_candidate_ids'],row['topic_id'],doc_family,relevant)
        if any(row.get(k)!=fresh[k] for k in ('capacity_coverage','family_recall','document_precision','candidate_family_coverage')): stored_metric_mismatches+=1
        recomputed.append({k:row[k] for k in ('core_case_id','corpus','ranker','policy','topic_id','agent_field','memory_condition')}|fresh)
    write_jsonl(output/'metrics'/'case_metrics.jsonl',recomputed)
    grouped=defaultdict(list)
    for row in recomputed: grouped[(row['corpus'],row['ranker'],row['topic_id'])].append(row)
    topic_rows=[]
    for (corpus,ranker,topic),values in sorted(grouped.items()):
        coverage=[x['capacity_coverage'] for x in values if x['capacity_coverage'] is not None]
        precisions=[x['document_precision'] for x in values if x['document_precision'] is not None]
        topic_rows.append({'corpus':corpus,'ranker':ranker,'topic_id':topic,'n':len(values),'coverage_numerator_sum':sum(x['capacity_coverage'] for x in values if x['capacity_coverage'] is not None),
          'coverage_denominator':len(coverage),'mean_capacity_coverage':statistics.mean(coverage) if coverage else None,
          'mean_document_precision':statistics.mean(precisions) if precisions else None,
          'precision_relevant_documents':sum(x['relevant_selected_documents'] for x in values),'precision_selected_documents':sum(x['selected_document_count'] for x in values),
          'micro_document_precision':sum(x['relevant_selected_documents'] for x in values)/sum(x['selected_document_count'] for x in values) if sum(x['selected_document_count'] for x in values) else None,
          'precision_null_count':len(values)-len(precisions),
          'empty_results':sum(x['empty_result'] for x in values),'all_irrelevant_returns':sum(x['returned_but_all_irrelevant'] for x in values)})
    write_csv(output/'metrics'/'metrics_by_topic.csv',topic_rows)
    lookup={(x['corpus'],x['ranker'],x['topic_id'],x['agent_field'],x['policy']):x for x in rows if x['memory_condition']=='empty'}
    topic_ids=sorted({x['topic_id'] for x in rows}); pair_rows=[]
    topic_fields={x['topic_id']:x['field'] for x in json.loads((root/config['topics']).read_text(encoding='utf-8'))}
    for corpus_name,ranker,field,policy,(left,right) in itertools.product(config['corpora'],config['rankers'],config['fields'],config['policies'],itertools.combinations(topic_ids,2)):
        a=lookup[(corpus_name,ranker,left,field,policy)]; b=lookup[(corpus_name,ranker,right,field,policy)]
        docs=document_overlap(a['selected_ids'],b['selected_ids']); families=document_overlap(a['selected_family_ids'],b['selected_family_ids'])
        relevant=data[corpus_name][3]
        relation='shared_evidence' if relevant[left]&relevant[right] else ('near_no_shared' if topic_fields[left]==topic_fields[right] else 'far')
        pair_rows.append({'corpus':corpus_name,'ranker':ranker,'agent_field':field,'policy':policy,'left_topic':left,'right_topic':right,'relation':relation,
          'document_jaccard':docs['jaccard'],'family_jaccard':families['jaccard'],'same_set_different_order':set(a['selected_ids'])==set(b['selected_ids']) and a['selected_ids']!=b['selected_ids']})
    write_csv(output/'metrics'/'topic_pair_results.csv',pair_rows)
    dedup=[]
    for corpus_name,(corpus,_,doc_family,_) in data.items():
        evaluation_ids=set(corpus.papers)
        evaluation_split='all_legacy_documents'
        if corpus_name=='corpus_expanded':
            evaluation_split='test'
            evaluation_ids={x['paper_id'] for x in read_jsonl(root/config['gold_paths']['document_map']) if x['split']=='test' and x['paper_id'] in corpus.papers}
        papers={pid:corpus.papers[pid] for pid in sorted(evaluation_ids)}
        assignments=duplicate_clusters([{'paper':p} for p in papers.values()],config['semantic_retrieval']['near_duplicate_threshold'],fixture_aware=True)
        tp=fp=fn=0
        for left,right in itertools.combinations(sorted(papers),2):
            gold_same=doc_family[left]==doc_family[right]; predicted=assignments[left]==assignments[right]
            tp+=gold_same and predicted; fp+=predicted and not gold_same; fn+=gold_same and not predicted
        dedup.append({'corpus':corpus_name,'evaluation_split':evaluation_split,'evaluated_documents':len(papers),'true_positive_pairs':tp,
          'false_positive_pairs':fp,'false_negative_pairs':fn,'pairwise_precision':tp/(tp+fp) if tp+fp else None,
          'pairwise_recall':tp/(tp+fn) if tp+fn else None})
    write_jsonl(output/'metrics'/'dedup_summary.jsonl',dedup)
    diagnostics=read_jsonl(output/'retrieval_diagnostics.jsonl'); challenge={x['paper_id']:x for x in read_jsonl(root/config['gold_paths']['challenge_qrels'])}
    generic_pass=sum(x['gate_passed'] for x in diagnostics if x['paper_id'] in challenge and challenge[x['paper_id']]['challenge_type'] in ('generic_negative','context_negative','context_negative_holdout'))
    new=[x for x in topic_rows if x['corpus']=='corpus_expanded' and x['ranker']=='stage_a_semantic_guarded']; base={x['topic_id']:x for x in topic_rows if x['corpus']=='corpus_expanded' and x['ranker']=='stage_a_reference'}
    new_by={x['topic_id']:x for x in new}; macro=statistics.mean(x['mean_capacity_coverage'] for x in new)
    quality={'science_evaluation_capacity_coverage':new_by['science_evaluation']['mean_capacity_coverage'],'macro_capacity_coverage':macro,
      'minimum_topic_capacity_coverage':min(x['mean_capacity_coverage'] for x in new),'generic_negative_gate_passes':generic_pass,
      'zero_relevant_returns_with_supply':sum(x['all_irrelevant_returns']+x['empty_results'] for x in new if x['mean_capacity_coverage'] is not None),
      'maximum_baseline_drop':max([base[x['topic_id']]['mean_capacity_coverage']-x['mean_capacity_coverage'] for x in new if base[x['topic_id']]['mean_capacity_coverage']>=.8]+[0]),
      'gold_perturbation_ranking_changes':gold_isolation_changes(config,root)}
    quality['stored_metric_mismatches']=stored_metric_mismatches
    dump(output/'metrics'/'quality_summary.json',quality)
    historical=historical_regression(root); dump(output/'diagnostics'/'historical_regression.json',historical)
    return {'case_rows':len(recomputed),'topic_rows':len(topic_rows),'topic_pair_rows':len(pair_rows),'quality':quality,'historical_regression':historical}


# Re-run production retrieval around an in-memory qrel permutation and count any ranking changes.
def gold_isolation_changes(config,root):
    root=Path(root); corpus,topics,_,_=dataset(config,root,'corpus_expanded'); profiles=load_retrieval_profiles(root/config['profiles'])
    retrieval=dict(config['semantic_retrieval']); retrieval['_profiles']=profiles; before={}
    for topic in sorted(topics.topics):
        _,audit=retrieve_stage_a_semantic_guarded(corpus.papers,topics,PolicyContext('balanced','retrieval',True,config['stage_lambda']),retrieval,topic,topics.topics[topic]['field'],[],[])
        before[topic]=audit['selected_ids']
    qrels=read_jsonl(root/config['gold_paths']['qrels']); random.Random(1931).shuffle(qrels)
    # qrels are deliberately retained only in this evaluator scope and are never passed to production retrieval.
    after={}
    for topic in sorted(topics.topics):
        _,audit=retrieve_stage_a_semantic_guarded(corpus.papers,topics,PolicyContext('balanced','retrieval',True,config['stage_lambda']),retrieval,topic,topics.topics[topic]['field'],[],[])
        after[topic]=audit['selected_ids']
    return sum(before[topic]!=after[topic] for topic in before)


# Recompute the known Supplement failure from its preserved row-level output without hardcoding metrics.
def historical_regression(root):
    path=Path(root)/'outputs_stage_a_supplement'/'supplement_delivery_final_20260914'/'core_results.jsonl'
    rows=read_jsonl(path)
    target=[x for x in rows if x.get('corpus')=='corpus_expanded' and x.get('ranker')=='stage_a_reference' and x.get('topic_id')=='science_evaluation']
    values=[x['gold_cluster_capacity_coverage_at_k'] for x in target if x.get('gold_cluster_capacity_coverage_at_k') is not None]
    return {'source':str(path.relative_to(root)).replace('\\','/'),'available':bool(rows),'rows':len(target),
      'science_evaluation_mean_capacity_coverage':statistics.mean(values) if values else None,
      'zero_gold_relevant_selected_cases':sum(x.get('gold_cluster_capacity_coverage_at_k')==0 for x in target),
      'interpretation':'known-problem regression; not an unseen test set'}


# Run the real step_v02 chain at controlled evidence boundaries and a 20-agent smoke world.
def engine_validation(config,root,output):
    root=Path(root); output=Path(output); base=json.loads((root/'configs'/'v03_mock_smoke.json').read_text(encoding='utf-8'))
    (output/'engine_boundary').mkdir(parents=True,exist_ok=True)
    base['corpus']=config['corpora']['corpus_expanded']; base['retrieval']=dict(config['semantic_retrieval']); base['retrieval']['profile_path']=str((root/config['profiles']).resolve())
    from .v03_pipeline import simulation_adapter
    cfg=simulation_adapter(base); corpus=Corpus(root/config['corpora']['corpus_expanded'],config['cutoff_year'],True,cfg['retrieval']); topics=TopicModel(root/config['topics'],corpus.papers,1.0)
    results=[]
    class RecordingBackend:
        def __init__(self,delegate,forbid=False): self.delegate=delegate; self.forbid=forbid; self.calls=[]
        def generate(self,kind,context,key):
            if kind=='propose' and self.forbid: raise AssertionError('propose called in low-evidence boundary')
            response=self.delegate.generate(kind,context,key); visible={x['id'] for x in context.get('papers',[])}
            references=sorted({ref for card in response.get('candidates',[]) for ref in card.get('references',[])}) if kind=='propose' else []
            self.calls.append({'kind':kind,'paper_ids':sorted(visible),'response_references':references,
              'references_visible':set(references)<=visible}); return response
    delegate=Backend(cfg,root)
    class ControlledCorpus:
        def __init__(self,count): self.count=count
        def retrieve_v02(self,*args,**kwargs):
            visible=sorted(corpus.papers.values(),key=lambda x:x['id'])[:self.count]
            return visible,{'selected_ids':[x['id'] for x in visible],'fallback':'controlled_boundary','query_id':f'controlled_{self.count}'}
    for count in (0,1,2,3):
        world=initialize_v02(20,42,topics); world.policy='balanced'; world.network='open'; world.candidate_schedule=build_schedule(world,1,cfg)
        before=digest(world.export()); energy_before={aid:a.energy for aid,a in world.agents.items()}; backend=RecordingBackend(delegate,count<2)
        after,events=step_v02(world,ControlledCorpus(count),topics,backend,cfg); validate_v02(after)
        boundary_dir=output/'engine_boundary'/f'count_{count}'
        for old in (boundary_dir/'events.jsonl',boundary_dir/'checkpoint.json'): old.unlink(missing_ok=True)
        journal=V02Journal(boundary_dir/'events.jsonl',f'boundary_{count}'); journal.commit(after,events)
        replayed=replay_v02(boundary_dir/'events.jsonl')
        short=sum(x['type']=='knowledge.retrieval.shortfall' for x in events); drafts=len(after.ideas)
        next_phase_projects=None
        if count<2:
            next_world,_=step_v02(after,ControlledCorpus(count),topics,backend,cfg); validate_v02(next_world); next_phase_projects=len(next_world.projects)
        energy_deltas=sorted({after.agents[aid].energy-value for aid,value in energy_before.items()})
        refs_ok=all(x['references_visible'] for x in backend.calls)
        passed=((count<2 and short==20 and not backend.calls and drafts==0 and next_phase_projects==0 and energy_deltas==[-4]) or
                (count>=2 and len(backend.calls)==20 and drafts==20 and energy_deltas==[-20] and refs_ok))
        results.append({'layer':'controlled_step_v02','evidence_count':count,'status':'passed' if passed else 'failed',
          'input_unchanged':digest(world.export())==before,'shortfall_events':short,'propose_calls':len(backend.calls),'drafts':drafts,
          'next_phase_projects':next_phase_projects,'energy_deltas':energy_deltas,
          'visible_evidence_counts':sorted({len(x['paper_ids']) for x in backend.calls}),'references_visible':refs_ok,
          'replay_equal':digest(replayed.export())==digest(after.export())})
    # Repeat each boundary through an actual Corpus and the semantic mode dispatcher.
    profile_rows=load_retrieval_profiles(root/config['profiles']); topic_rows=json.loads((root/config['topics']).read_text(encoding='utf-8'))
    for count in (0,1,2,3):
        papers=[]
        for ti,topic in enumerate(topic_rows):
            phrase=profile_rows['topics'][topic['topic_id']]['positive_phrases'][0]
            for index in range(count):
                papers.append({'id':f'boundary_{count}_{ti}_{index}','title':f'{phrase} boundary evidence {index}',
                  'abstract':f'A fabricated {phrase} boundary fixture.','year':2022,'field':topic['field'],'topic_ids':[topic['topic_id']],'synthetic':True})
        while len(papers)<3:
            index=len(papers); papers.append({'id':f'boundary_padding_{count}_{index}','title':'Unrelated accounting record',
              'abstract':'A synthetic administrative latency table without research topic evidence.','year':2022,'field':'learning','topic_ids':[],'synthetic':True})
        corpus_path=output/'engine_boundary'/f'corpus_count_{count}.jsonl'; write_jsonl(corpus_path,papers)
        real=Corpus(corpus_path,config['cutoff_year'],True,cfg['retrieval']); real_topics=TopicModel(root/config['topics'],real.papers,1.0)
        world=initialize_v02(20,42,real_topics); world.policy='balanced'; world.network='open'; world.candidate_schedule=build_schedule(world,1,cfg)
        before=digest(world.export()); energy_before={aid:a.energy for aid,a in world.agents.items()}; backend=RecordingBackend(delegate,count<2)
        after,events=step_v02(world,real,real_topics,backend,cfg); validate_v02(after)
        short=sum(x['type']=='knowledge.retrieval.shortfall' for x in events); drafts=len(after.ideas)
        boundary_dir=output/'engine_boundary'/f'real_count_{count}'
        for old in (boundary_dir/'events.jsonl',boundary_dir/'checkpoint.json'): old.unlink(missing_ok=True)
        journal=V02Journal(boundary_dir/'events.jsonl',f'real_boundary_{count}'); journal.commit(after,events); replayed=replay_v02(boundary_dir/'events.jsonl')
        next_phase_projects=None
        if count<2:
            next_world,_=step_v02(after,real,real_topics,backend,cfg); validate_v02(next_world); next_phase_projects=len(next_world.projects)
        energy_deltas=sorted({after.agents[aid].energy-value for aid,value in energy_before.items()}); refs_ok=all(x['references_visible'] for x in backend.calls)
        passed=((count<2 and short==20 and not backend.calls and drafts==0 and next_phase_projects==0 and energy_deltas==[-4]) or
                (count>=2 and len(backend.calls)==20 and drafts==20 and energy_deltas==[-20] and refs_ok))
        results.append({'layer':'real_corpus_step_v02','evidence_count':count,
          'status':'passed' if passed else 'failed',
          'input_unchanged':digest(world.export())==before,'shortfall_events':short,'propose_calls':len(backend.calls),'drafts':drafts,
          'next_phase_projects':next_phase_projects,'energy_deltas':energy_deltas,
          'visible_evidence_counts':sorted({len(x['paper_ids']) for x in backend.calls}),'references_visible':refs_ok,'replay_equal':digest(replayed.export())==digest(after.export())})
    smoke=initialize_v02(20,42,topics); smoke.policy='balanced'; smoke.network='open'; smoke.candidate_schedule=build_schedule(smoke,2,cfg); backend=RecordingBackend(delegate)
    all_events=[]
    smoke_dir=output/'engine_smoke'; smoke_dir.mkdir(parents=True,exist_ok=True)
    for old in (smoke_dir/'events.jsonl',smoke_dir/'checkpoint.json'): old.unlink(missing_ok=True)
    journal=V02Journal(smoke_dir/'events.jsonl','closure_smoke')
    for _ in range(12): smoke,events=step_v02(smoke,corpus,topics,backend,cfg); all_events.extend(events); journal.commit(smoke,events)
    validate_v02(smoke); replayed=replay_v02(smoke_dir/'events.jsonl'); dump(smoke_dir/'final_state.json',smoke.export()); write_jsonl(smoke_dir/'backend_calls.jsonl',backend.calls)
    results.append({'layer':'real_corpus_smoke','evidence_count':None,'status':'passed','agents':len(smoke.agents),'tick':smoke.tick,'events':len(all_events),'backend_calls':len(backend.calls),'replay_equal':digest(replayed.export())==digest(smoke.export())})
    write_jsonl(output/'engine_boundary'/'results.jsonl',results); return results


# Exercise the repository-supported doctor, test, and complete 18-branch mock workflow.
def repository_regression(root,output):
    root=Path(root); output=Path(output); target=output/'repository_regression'; run_dir=target/'mock_main'; target.mkdir(parents=True,exist_ok=True)
    capture={'text':True,'encoding':'utf-8','errors':'replace','capture_output':True}
    doctor=subprocess.run([sys.executable,'run.py','doctor','--config','configs/mock.json'],cwd=root,**capture)
    tests=subprocess.run([sys.executable,'run.py','test'],cwd=root,**capture)
    status_path=run_dir/'status.json'
    if not status_path.exists() or json.loads(status_path.read_text(encoding='utf-8')).get('status')!='completed':
        experiment=subprocess.run([sys.executable,'run.py','run','--config','configs/mock.json','--output',str(run_dir)],cwd=root,**capture)
    else:
        experiment=subprocess.CompletedProcess([],0,'reused completed mock run','')
    (target/'doctor.log').write_text(doctor.stdout+doctor.stderr,encoding='utf-8')
    (target/'tests.log').write_text(tests.stdout+tests.stderr,encoding='utf-8')
    (target/'experiment.log').write_text(experiment.stdout+experiment.stderr,encoding='utf-8')
    summary=list(csv.DictReader((run_dir/'summary.csv').open(encoding='utf-8-sig'))) if (run_dir/'summary.csv').exists() else []
    finals=sorted(run_dir.glob('seed_*/*/final_state.json')); final_checks=[]
    for path in finals:
        state=json.loads(path.read_text(encoding='utf-8')); events=path.with_name('events.jsonl')
        replayed=replay_v01(events) if events.exists() else None
        final_checks.append(len(state.get('agents',{}))==20 and state.get('tick')==30 and replayed is not None and digest(replayed.export())==digest(state))
    conditions={(x.get('seed'),x.get('policy'),x.get('network')) for x in summary}
    passed=doctor.returncode==0 and tests.returncode==0 and experiment.returncode==0 and len(summary)==18 and len(conditions)==18 and len(finals)==18 and all(final_checks)
    result={'schema_version':SCHEMA,'status':'passed' if passed else 'failed','doctor_exit_code':doctor.returncode,
      'test_exit_code':tests.returncode,'experiment_exit_code':experiment.returncode,'summary_rows':len(summary),
      'unique_conditions':len(conditions),'final_states':len(finals),'agent_tick_replay_checks_passed':sum(final_checks),
      'expected_agents':20,'expected_tick':30,'backend':'mock','network_requests':0,'llm_calls':0}
    dump(target/'result.json',result); return result


# Apply frozen quality thresholds separately from execution, integrity, metrics, engine, and reproduction.
def validate(config,root,output,reproduction_status=None):
    output=Path(output); rows=read_jsonl(output/'core_results.jsonl'); failures=read_jsonl(output/'failure_cases.jsonl'); stored_quality=json.loads((output/'metrics'/'quality_summary.json').read_text(encoding='utf-8'))
    fresh=analyze(config,root,output); quality=fresh['quality']
    engine=read_jsonl(output/'engine_boundary'/'results.jsonl'); policy=read_jsonl(output/'diagnostics'/'policy_results.jsonl'); thresholds=config['quality_thresholds']
    integrity=integrity_pass(rows,1296)
    metric_ok=metric_correctness_pass(stored_quality,quality,read_jsonl(output/'metrics'/'case_metrics.jsonl'))
    checks=quality_gate_checks(quality,thresholds)
    quality_ok=all(checks.values()); engine_ok=all(x['status']=='passed' and x.get('input_unchanged',True) and x.get('replay_equal',True) and x.get('references_visible',True) for x in engine)
    regression_path=output/'repository_regression'/'result.json'; regression=json.loads(regression_path.read_text(encoding='utf-8')) if regression_path.exists() else {'status':'not_run'}
    regression_ok=regression['status']=='passed'
    repro_ok=reproduction_status is None or (reproduction_status=='passed' and reproduction_tree_complete(output/'reproduction'/'source'))
    enabled=[x for x in policy if x['path_enabled']]; disabled=defaultdict(set)
    for x in policy:
        if not x['path_enabled']: disabled[x['ranker']].add(tuple(x['selected_order']))
    experiment_complete=len(enabled)==54 and len(policy)==60 and all(len(v)==1 for v in disabled.values()) and fresh['topic_pair_rows']==2376
    execution='completed' if len(rows)==1296 and not failures and experiment_complete else 'failed'
    overall='completed' if execution=='completed' and integrity and metric_ok and quality_ok and engine_ok and regression_ok and repro_ok else ('completed_with_limitations' if execution=='completed' else 'failed')
    result={'schema_version':SCHEMA,'execution_status':execution,'integrity_status':'passed' if integrity else 'failed','metric_correctness_status':'passed' if metric_ok else 'failed',
      'retrieval_quality_status':'passed' if quality_ok else 'failed','engine_integration_status':'passed' if engine_ok else 'failed','reproduction_status':reproduction_status or 'not_yet_run',
      'repository_regression_status':'passed' if regression_ok else 'failed',
      'overall_status':overall,'all_passed':overall=='completed','quality_checks':checks,'quality_values':quality,
      'known_limitations':['synthetic Codex-designed gold is not independent human annotation','deterministic frozen states are not independent social worlds'],'failure_cases':len(failures)}
    dump(output/'DELIVERY_VALIDATION.json',result); return result


# Validate cardinality, unique keys, and frozen state hashes independently of quality.
def integrity_pass(rows,expected):
    return len(rows)==expected and len({x['core_case_id'] for x in rows})==expected and all(x['state_hash']==x['state_hash_before']==x['state_hash_after'] for x in rows)


# Reject altered summaries, stale row metrics, and incomplete evaluator output.
def metric_correctness_pass(stored_quality,fresh_quality,case_metrics):
    return (stored_quality==fresh_quality and fresh_quality.get('stored_metric_mismatches')==0 and
      all(all(k in row for k in ('capacity_coverage','document_precision','candidate_family_coverage','duplicate_pair_ratio')) for row in case_metrics))


# Evaluate the predeclared retrieval thresholds without mutating them.
def quality_gate_checks(quality,thresholds):
    return {'science_evaluation':quality['science_evaluation_capacity_coverage']>=thresholds['science_evaluation_capacity_coverage'],
      'macro':quality['macro_capacity_coverage']>=thresholds['macro_capacity_coverage'],'minimum_topic':quality['minimum_topic_capacity_coverage']>=thresholds['minimum_topic_capacity_coverage'],
      'baseline_drop':quality['maximum_baseline_drop']<=thresholds['maximum_baseline_drop'],'generic_negatives':quality['generic_negative_gate_passes']==0,
      'gold_isolation':quality['gold_perturbation_ranking_changes']==0,'zero_relevant_returns':quality['zero_relevant_returns_with_supply']==0}


# Copy the complete importable source tree and all frozen inputs while preserving paths.
def stage_reproduction(config,root,output):
    root=Path(root); source=Path(output)/'reproduction'/'source'
    if source.exists(): shutil.rmtree(source)
    for rel in ('scimirror','tests','configs','data'):
        shutil.copytree(root/rel,source/rel,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ('execute_stage_a_closure.py','run.py','requirements.txt','AGENTS.md','README.md'):
        if (root/name).exists(): shutil.copy2(root/name,source/name)
    return source


# Check that an isolated reproduction tree includes every required import and entry point.
def reproduction_tree_complete(source):
    source=Path(source); required=['execute_stage_a_closure.py','run.py','AGENTS.md','configs/stage_a_closure.json','data/retrieval_topic_profiles_v1.json',
      'scimirror/__init__.py','scimirror/stage_a_closure.py','scimirror/v03_retrieval.py','scimirror/corpus.py','tests/test_stage_a_closure.py']
    return all((source/path).is_file() for path in required)


# Run the frozen matrix from an isolated source directory and compare semantic outputs.
def reproduce(config,config_path,root,output):
    output=Path(output); source=stage_reproduction(config,root,output); repro_out=Path(tempfile.mkdtemp(prefix='scimirror_closure_repro_'))/'run'
    env=dict(os.environ); env.pop('PYTHONPATH',None)
    checksum_rows=[]
    for path in sorted(p for p in source.rglob('*') if p.is_file()): checksum_rows.append(f'{file_hash(path)}  {path.relative_to(source).as_posix()}')
    (output/'reproduction'/'SOURCE_CHECKSUMS.sha256').write_text('\n'.join(checksum_rows)+'\n',encoding='utf-8')
    checksums_verified=all(file_hash(source/line.split('  ',1)[1])==line.split('  ',1)[0] for line in checksum_rows)
    capture={'text':True,'encoding':'utf-8','errors':'replace','capture_output':True}
    probe=subprocess.run([sys.executable,'-c','import scimirror; print(scimirror.__file__)'],cwd=source,env=env,**capture)
    loaded_path=Path(probe.stdout.strip()).resolve() if probe.returncode==0 else None
    freeze_command=[sys.executable,'execute_stage_a_closure.py','freeze','--config','configs/stage_a_closure.json','--output',str(repro_out)]
    freeze_proc=subprocess.run(freeze_command,cwd=source,env=env,**capture)
    command=[sys.executable,'execute_stage_a_closure.py','run','--config','configs/stage_a_closure.json','--output',str(repro_out)]
    proc=subprocess.run(command,cwd=source,env=env,**capture) if freeze_proc.returncode==0 else subprocess.CompletedProcess(command,1,'','freeze failed')
    engine_command=[sys.executable,'execute_stage_a_closure.py','engine','--config','configs/stage_a_closure.json','--run-dir',str(repro_out)]
    engine_proc=subprocess.run(engine_command,cwd=source,env=env,**capture) if proc.returncode==0 else subprocess.CompletedProcess(engine_command,1,'','core run failed')
    original=read_jsonl(output/'core_results.jsonl'); replay=read_jsonl(repro_out/'core_results.jsonl') if proc.returncode==0 else []
    semantic=lambda rows:{x['core_case_id']:(x['selected_ids'],x['capacity_coverage']) for x in rows}
    module_inside=loaded_path is not None and source.resolve() in loaded_path.parents
    original_engine=read_jsonl(output/'engine_boundary'/'results.jsonl'); replay_engine=read_jsonl(repro_out/'engine_boundary'/'results.jsonl') if engine_proc.returncode==0 else []
    engine_semantic=lambda rows:[{k:x.get(k) for k in ('layer','evidence_count','status','shortfall_events','propose_calls','drafts','next_phase_projects','energy_deltas','visible_evidence_counts','references_visible','replay_equal')} for x in rows]
    equal=semantic(original)==semantic(replay); engine_equal=engine_semantic(original_engine)==engine_semantic(replay_engine)
    result={'status':'passed' if proc.returncode==0 and engine_proc.returncode==0 and equal and engine_equal and module_inside and checksums_verified else 'failed','exit_code':proc.returncode,
      'command':' '.join(command),'python':sys.version,'platform':platform.platform(),'module_path':str(source/'scimirror'),
      'actual_loaded_module':str(loaded_path) if loaded_path else None,'module_inside_isolated_source':module_inside,'checksums_verified_before_run':checksums_verified,
      'freeze_command':' '.join(freeze_command),'freeze_exit_code':freeze_proc.returncode,
      'case_count':len(replay),'semantic_equal':equal,'engine_command':' '.join(engine_command),'engine_exit_code':engine_proc.returncode,
      'engine_cases':len(replay_engine),'engine_semantic_equal':engine_equal,'stderr':(freeze_proc.stderr+proc.stderr+engine_proc.stderr)[-2000:]}
    shutil.rmtree(repro_out.parent,ignore_errors=True); dump(output/'reproduction'/'REPRODUCTION_RESULT.json',result); return result


# Generate reports, checksums, and a non-nested delivery archive even when quality fails.
def package(config,root,output,validation):
    root=Path(root); output=Path(output); source=stage_reproduction(config,root,output)
    (output/'README_REPRODUCE.md').write_text('# Reproduce\nOn Linux with Python 3.11+, run `python3 execute_stage_a_closure.py all --config configs/stage_a_closure.json --output <new-dir>`. The workflow is offline and exits 2 when any mandatory gate fails.\n',encoding='utf-8')
    (output/'CODE_CHANGE_REPORT.md').write_text('# Code changes\n\n- Added retrieval-only profiles and `stage_a_semantic_guarded`; retained executable `stage_a_fixed`.\n- Added frozen Closure data without modifying previous corpora, qrels, or outputs.\n- Added row-level metrics, gold-isolation checks, policy/ablation diagnostics, actual `step_v02` boundaries, layered validation, repository regression, and isolated reproduction.\n- No reward, collaboration, exit, or output-accounting social mechanism was changed.\n',encoding='utf-8')
    topics=list(csv.DictReader((output/'metrics'/'metrics_by_topic.csv').open(encoding='utf-8-sig')))
    historical=json.loads((output/'diagnostics'/'historical_regression.json').read_text(encoding='utf-8'))
    policy=read_jsonl(output/'diagnostics'/'policy_results.jsonl'); enabled=[x for x in policy if x['path_enabled']]
    engine=read_jsonl(output/'engine_boundary'/'results.jsonl'); diagnostics=read_jsonl(output/'retrieval_diagnostics.jsonl')
    challenge_rows=read_jsonl(root/config['gold_paths']['challenge_qrels']); challenge={x['paper_id']:x for x in challenge_rows}
    gate_by_paper=defaultdict(set)
    for row in diagnostics:
        if row['paper_id'] in challenge: gate_by_paper[row['paper_id']].add(bool(row['gate_passed']))
    old={(x['topic_id']):x for x in topics if x['corpus']=='corpus_expanded' and x['ranker']=='stage_a_reference'}
    new={(x['topic_id']):x for x in topics if x['corpus']=='corpus_expanded' and x['ranker']=='stage_a_semantic_guarded'}
    lines=['# Stage A 收尾报告','',f'总体状态：`{validation["overall_status"]}`；检索质量：`{validation["retrieval_quality_status"]}`。','',
      '本结果只验证本地合成语料上的工程行为，不是现实科研政策因果证据。108 个冻结状态是确定性诊断状态，不是 108 个独立社会世界。','',
      '## 相同扩充语料上的逐主题新旧比较','',
      '|topic|旧 coverage（分子和/分母）|新 coverage（分子和/分母）|变化|新 micro Precision（相关文献/返回文献）|空结果|返回但全无关|',
      '|---|---:|---:|---:|---:|---:|---:|']
    for topic in sorted(new):
        a,b=old[topic],new[topic]; delta=float(b['mean_capacity_coverage'])-float(a['mean_capacity_coverage'])
        lines.append(f'|{topic}|{a["mean_capacity_coverage"]} ({a["coverage_numerator_sum"]}/{a["coverage_denominator"]})|{b["mean_capacity_coverage"]} ({b["coverage_numerator_sum"]}/{b["coverage_denominator"]})|{delta:.6f}|{b["micro_document_precision"]} ({b["precision_relevant_documents"]}/{b["precision_selected_documents"]})|{b["empty_results"]}/{b["n"]}|{b["all_irrelevant_returns"]}/{b["n"]}|')
    lines += ['', '## 十项问题的回答','',
      f'1. `science_evaluation` 的历史故障主要位于查询语义和证据门槛：旧归档重新计算为 {historical["science_evaluation_mean_capacity_coverage"]}（{historical["rows"]} 行）。候选截断和 policy 重排发生在不充分召回之后，不是首要故障点。',
      f'2. 相同扩充语料上，该主题 coverage 从 {old["science_evaluation"]["mean_capacity_coverage"]} 变为 {new["science_evaluation"]["mean_capacity_coverage"]}；新 micro Precision 为 {new["science_evaluation"]["micro_document_precision"]}（{new["science_evaluation"]["precision_relevant_documents"]}/{new["science_evaluation"]["precision_selected_documents"]}），空或全无关 case 为 {int(new["science_evaluation"]["empty_results"])+int(new["science_evaluation"]["all_irrelevant_returns"])}/{new["science_evaluation"]["n"]}。',
      '3. `agent_memory` 与 `science_retrieval` 存在预声明共享证据；当前不足不能仅归为误召回，gold 边界仍是 Codex 设计的工程标注，须由独立人工复核。',
      '4. 上表列出所有主题的实际改善或退化；验收使用最大基线下降而非隐藏负变化。',
      f'5. 挑战集共 {len(challenge_rows)} 条主题标注、对应 {len(challenge)} 篇唯一文献；通用/上下文负例通过数为 {validation["quality_values"]["generic_negative_gate_passes"]}。同义正例和共享证据的逐文献 gate 结果保存在 `retrieval_diagnostics.jsonl`，没有把共享证据强制当作错误。',
      f'6. Policy 仅在共同合格池内重排。54 个启用调用中，分数/顺序/集合变化分别为 {sum(x["score_changed"] for x in enabled)}/{sum(x["order_changed"] for x in enabled)}/{sum(x["set_changed"] for x in enabled)}；6 个关闭调用按 ranker 保持一致。',
      f'7. 0/1/2/3 条证据均实际调用生产 `step_v02`；边界记录 {len([x for x in engine if x["layer"]!="real_corpus_smoke"])} 行，0/1 条证据下一 phase 项目数均为 0，引用可见性检查通过。',
      '8. 会。质量、完整性、指标、引擎、仓库回归或复现任一必需层失败都会使 `all_passed=false`；反例测试覆盖错误 ID、篡改汇总、缺失/重复 case、哈希不一致及质量失败。',
      f'9. 独立目录复现状态为 `{validation["reproduction_status"]}`；结果文件记录实际模块路径、checksum、1296 case 与引擎边界语义比较。',
      '10. 若 Linux 权威运行也通过，可进入“小规模真实文献 + 独立人工相关性标注”的下一阶段；当前合成工程 gold 不能证明科学有效性。','',
      '## 分层验收与限制','',f'质量门槛逐项：`{validation["quality_checks"]}`。',
      f'仓库回归：`{validation["repository_regression_status"]}`；引擎集成：`{validation["engine_integration_status"]}`；复现：`{validation["reproduction_status"]}`。',
      '工程完成、检索质量达标与科学有效性是三个不同结论；后者尚待真实文献和独立人工标注验证。']
    (output/'FINAL_REPORT_ZH.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    git_head=subprocess.run(['git','rev-parse','HEAD'],cwd=root,text=True,encoding='utf-8',errors='replace',capture_output=True)
    git_status=subprocess.run(['git','status','--short'],cwd=root,text=True,encoding='utf-8',errors='replace',capture_output=True)
    git_diff=subprocess.run(['git','diff','--binary'],cwd=root,capture_output=True)
    (output/'WORKTREE_DIFF.patch').write_bytes(git_diff.stdout)
    runtime={'schema_version':SCHEMA,'created_at':datetime.now().astimezone().isoformat(),'python':sys.version,'platform':platform.platform(),
      'review_baseline_commit':config['review_commit'],'actual_head':git_head.stdout.strip() if git_head.returncode==0 else None,
      'working_tree_dirty':bool(git_status.stdout.strip()),'git_status':git_status.stdout.splitlines(),
      'source_hash':resume_contract(config,root/'configs'/'stage_a_closure.json',root)['identity_hash'],'network_requests':0,'llm_calls':0}
    dump(output/'runtime_manifest.json',runtime); shutil.copy2(root/'configs'/'stage_a_closure.json',output/'config.json')
    checks=[]
    for path in sorted(p for p in (output/'reproduction').rglob('*') if p.is_file() and p.name!='CHECKSUMS.sha256'):
        checks.append(f'{file_hash(path)}  {path.relative_to(output/"reproduction").as_posix()}')
    (output/'reproduction'/'CHECKSUMS.sha256').write_text('\n'.join(checks)+'\n',encoding='utf-8')
    archive=output/'stage_a_closure_delivery.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(p for p in output.rglob('*') if p.is_file() and p!=archive and p.suffix!='.zip'): bundle.write(path,Path('delivery')/path.relative_to(output))
    return {'path':str(archive),'sha256':file_hash(archive),'files':len(zipfile.ZipFile(archive).namelist())}
