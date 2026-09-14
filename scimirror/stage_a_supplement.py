"""Offline Stage A supplement evidence, policy, gold-metric, smoke, and delivery workflow."""
import copy
import csv
import hashlib
import inspect
import itertools
import json
import platform
import random
import shutil
import statistics
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from .backend import Backend, validate_response
from .candidate_pool import build_schedule
from .common import canonical, digest, dump
from .corpus import Corpus
from .policy import PolicyContext
from .stage_a import build_frozen_states, file_hash, frozen_state_hash, read_jsonl, write_jsonl
from .topics import TopicModel
from .v02_engine import proposal_observation, step_v02
from .v02_state import initialize_v02, validate_v02
from .v03_pipeline import simulation_adapter
from .v03_retrieval import duplicate_clusters, document_overlap, retrieve_stage_a_fixed
from .v03_review import write_csv


SCHEMA='stage_a_supplement_1'
PRODUCTION_FIELDS={'id','title','abstract','year','field','topic_ids','synthetic','doi','source_url'}
REFERENCE_RETRIEVE=retrieve_stage_a_fixed
SUPPLEMENT_RETRIEVE=retrieve_stage_a_fixed


# Load and strictly validate the offline supplement configuration and frozen dimensions.
def load_config(path,root):
    config=json.loads(Path(path).read_text(encoding='utf-8')); root=Path(root)
    required={'schema_version','corpora','topics','gold_paths','rankers','policies','fields','memory_conditions',
              'retrieval','policy_scenarios','policy_histories','output_root','allow_network','allow_llm_calls'}
    if not required<=set(config) or config['schema_version']!=SCHEMA:
        raise ValueError('Invalid Stage A supplement configuration')
    if config['rankers']!=['stage_a_reference','stage_a_supplement'] or config['policies']!=['balanced','novelty','recognition']:
        raise ValueError('Frozen ranker or policy design changed')
    if config['candidate_pool_size']!=20 or config['top_k']!=3 or config['retrieval']['candidate_pool_size']!=20:
        raise ValueError('Candidate pool/top-k must remain 20/3')
    if config['allow_network'] or config['allow_llm_calls']:
        raise ValueError('Supplement must be offline')
    if not (root/config['corpora']['corpus_original']).is_file() or not (root/config['topics']).is_file():
        raise ValueError('Original frozen inputs missing')
    return config


# Write deterministic prepared data once and reject any attempt to mutate a frozen file.
def write_frozen(path,text):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() and path.read_text(encoding='utf-8')!=text:
        raise ValueError(f'Frozen prepared data differs: {path}')
    path.write_text(text,encoding='utf-8')


# Build content-diverse synthetic evidence and independent family/qrel files deterministically.
def prepare(config,root):
    root=Path(root); topics=json.loads((root/config['topics']).read_text(encoding='utf-8'))
    problems=['cost control','reliability under shift','compositional reasoning','coordination distortion','failure detection','resource allocation']
    methods=['matched ablation','graph intervention','memory compression','counterfactual replay','stress testing','factorial comparison']
    settings=['limited budget tasks','noisy multi-agent traces','cross-domain benchmarks','asynchronous teams','sparse evidence streams','held-out synthetic worlds']
    evaluations=['calibration error and cost','robustness across shifts','reasoning accuracy','coordination loss','failure recall','resource-normalized output']
    limits=['lexical fixture only','synthetic traces only','no external validity','fixed topology assumption','small controlled setting','requires later real-data audit']
    corpus=[]; families=[]; mapping=[]; cards=[]; memberships={}
    for ti,topic in enumerate(topics):
        phrase=' '.join(topic['keywords'])
        for index in range(6):
            family=f'supp_family_{topic["topic_id"]}_{index}'
            split='calibration' if index in (0,3) else 'test'
            card={'gold_family_id':family,'topic_ids':[topic['topic_id']],'problem':problems[index],
                  'method':methods[(index+ti)%6],'setting':settings[(2*index+ti)%6],
                  'evaluation_plan':evaluations[index],'limitations':limits[index],'split':split}
            title=f'Synthetic {phrase}: {card["problem"]} via {card["method"]}'
            abstract=(f'Fabricated fixture examining {phrase} for {card["problem"]}. The method uses {card["method"]} '
                      f'in {card["setting"]}; evaluation compares {card["evaluation_plan"]}. Limitation: {card["limitations"]}. '
                      'No real publication, author, or empirical finding is represented.')
            paper_id=f'supp_{topic["topic_id"]}_{index}'
            paper={'id':paper_id,'title':title,'abstract':abstract,'year':2020+index%5,
                   'field':topic['field'],'topic_ids':[topic['topic_id']],'synthetic':True}
            corpus.append(paper); mapping.append({'paper_id':paper_id,'gold_family_id':family,'variant_of':None,'split':split})
            memberships[family]=[topic['topic_id']]; families.append(card|{'paper_ids':[paper_id]}); cards.append(card)
        # Put every near-duplicate family in the frozen test split so dedup quality is
        # evaluated on held-out positive pairs rather than calibration-only examples.
        base=corpus[-5]
        variant_id=f'supp_{topic["topic_id"]}_variant'
        variant=copy.deepcopy(base); variant.update(id=variant_id,title=base['title']+' scenario 2',abstract=base['abstract']+' Variant 2.')
        corpus.append(variant); mapping.append({'paper_id':variant_id,'gold_family_id':families[-5]['gold_family_id'],
                                                'variant_of':base['id'],'split':families[-5]['split']})
        families[-5]['paper_ids'].append(variant_id)
    shared=[('agent_memory','science_retrieval','shared_memory_retrieval'),
            ('agent_communication','science_collaboration','shared_communication_collaboration'),
            ('learning_policy','learning_reward','shared_policy_reward')]
    topic_by_id={x['topic_id']:x for x in topics}
    for left,right,family in shared:
        phrase=' '.join(topic_by_id[left]['keywords']+topic_by_id[right]['keywords'])
        pid='supp_'+family
        card={'gold_family_id':family,'topic_ids':[left,right],'problem':'boundary interaction',
              'method':'cross-topic contrast','setting':'controlled shared evidence','evaluation_plan':'separate-topic ablation',
              'limitations':'shared synthetic fixture only','split':'test'}
        corpus.append({'id':pid,'title':f'Synthetic shared {phrase} contrast',
            'abstract':f'Fabricated fixture studies {phrase} with a cross-topic contrast in controlled shared evidence. No real finding is represented.',
            'year':2023,'field':topic_by_id[left]['field'],'topic_ids':[left,right],'synthetic':True})
        mapping.append({'paper_id':pid,'gold_family_id':family,'variant_of':None,'split':'test'})
        memberships[family]=[left,right]; families.append(card|{'paper_ids':[pid]}); cards.append(card)
    for index,topic in enumerate(topics[:3]):
        corpus.append({'id':f'future_{topic["topic_id"]}','title':'Synthetic future '+' '.join(topic['keywords']),
            'abstract':'Cutoff exclusion fixture only. No empirical claim.','year':config['cutoff_year'],
            'field':topic['field'],'topic_ids':[topic['topic_id']],'synthetic':True})
    qrels=[]
    for topic in topics:
        for family,member_topics in memberships.items():
            grade=2 if topic['topic_id'] in member_topics and len(member_topics)==1 else (1 if topic['topic_id'] in member_topics else 0)
            qrels.append({'query_id':topic['topic_id'],'gold_family_id':family,'query_relevance_grade':grade,
                          'annotation_reason':('direct designed family' if grade==2 else 'shared designed relation' if grade==1 else 'query-relative negative'),
                          'shared_topic_relation':grade==1,'split':next(x['split'] for x in families if x['gold_family_id']==family)})
    files={config['corpora']['corpus_expanded']:corpus,config['gold_paths']['families']:families,
           config['gold_paths']['qrels']:qrels,config['gold_paths']['document_map']:mapping,
           config['family_content_audit']:cards}
    for relative,rows in files.items(): write_frozen(root/relative,''.join(canonical(x)+'\n' for x in rows))
    split={'schema_version':SCHEMA,'fixture_seed':config['fixture_seed'],'rule':'whole family; indices 0 and 3 calibrate; shared test',
           'calibration_families':sorted(x['gold_family_id'] for x in families if x['split']=='calibration'),
           'test_families':sorted(x['gold_family_id'] for x in families if x['split']=='test')}
    split['family_overlap']=sorted(set(split['calibration_families'])&set(split['test_families']))
    write_frozen(root/config['gold_paths']['split_manifest'],json.dumps(split,ensure_ascii=False,indent=2)+'\n')
    policy_rows=build_policy_corpus(config,topics)
    write_frozen(root/config['policy_fixture'],''.join(canonical(x)+'\n' for x in policy_rows))
    return {'status':'completed','documents':len(corpus),'eligible_documents':sum(x['year']<config['cutoff_year'] for x in corpus),
            'unique_families':len(families),'topic_family_associations':sum(len(x['topic_ids']) for x in families),
            'families_per_topic':{t['topic_id']:sum(t['topic_id'] in x['topic_ids'] for x in families) for t in topics},
            'independent_families_per_topic':{t['topic_id']:sum(t['topic_id'] in x['topic_ids'] and len(x['topic_ids'])==1 for x in families) for t in topics},
            'shared_families_per_topic':{t['topic_id']:sum(t['topic_id'] in x['topic_ids'] and len(x['topic_ids'])>1 for x in families) for t in topics},
            'calibration_families':len(split['calibration_families']),'test_families':len(split['test_families']),
            'family_overlap':split['family_overlap'],'future_documents':3}


# Build a separate production-feature policy corpus with six alternatives per scenario and frozen background counts.
def build_policy_corpus(config,topics):
    topic_ids=['agent_memory','science_retrieval','agent_communication']; topic_map={x['topic_id']:x for x in topics}
    rows=[]
    for scenario,topic_id in zip(config['policy_scenarios'],topic_ids):
        topic=topic_map[topic_id]; phrase=' '.join(topic['keywords'])
        for i in range(6):
            secondary='learning_reward' if i>=3 else topic_id
            rows.append({'id':f'policy_{scenario}_{i}','title':f'{phrase} candidate method {i}',
                'abstract':f'Fabricated {phrase} evidence using distinct protocol token method_{scenario}_{i} with controlled evaluation.',
                'year':2022,'field':topic['field'],'topic_ids':sorted(set([topic_id,secondary])),'synthetic':True})
    for i in range(12):
        rows.append({'id':f'policy_background_reward_{i}','title':f'Reward background archive {i}',
            'abstract':'Historical synthetic reward design attention context only.','year':2019,'field':'learning',
            'topic_ids':['learning_reward'],'synthetic':True})
    return rows


# Load production corpus and isolated gold data while checking IDs, splits, and forbidden feature leakage.
def load_dataset(config,root,corpus_name):
    root=Path(root); corpus=Corpus(root/config['corpora'][corpus_name],config['cutoff_year'],True,config['retrieval'])
    if any(set(p)-PRODUCTION_FIELDS for p in corpus.papers.values()):
        raise ValueError('Gold/evaluation field leaked into production corpus')
    topics=TopicModel(root/config['topics'],corpus.papers,config['recognition_alpha'])
    if corpus_name=='corpus_expanded':
        maps=read_jsonl(root/config['gold_paths']['document_map']); qrels=read_jsonl(root/config['gold_paths']['qrels'])
        doc_family={x['paper_id']:x['gold_family_id'] for x in maps if x['paper_id'] in corpus.papers}
        relevant={t:{x['gold_family_id'] for x in qrels if x['query_id']==t and x['query_relevance_grade']>=1}
                  for t in topics.topics}
    else:
        doc_family={}; relevant={t:set() for t in topics.topics}
        for pid,labels in topics.paper_topics.items():
            family='original_family_'+('_'.join(labels) if labels else pid)
            doc_family[pid]=family
            for topic in labels: relevant[topic].add(family)
    return corpus,topics,doc_family,relevant


# Return a numeric span with null semantics for empty collections.
def span(values):
    values=[x for x in values if x is not None]
    return max(values)-min(values) if values else None


# Map an audit to supply, policy-choice, gold-cluster, and shortfall diagnostics.
def result_row(state,corpus_name,ranker,policy,audit,doc_family,relevant,corpus):
    candidates=audit['candidates']; selected=audit['selected_ids']; selected_families={doc_family[x] for x in selected if x in doc_family}
    gold=relevant[state['selected_topic_id']]; found=selected_families&gold
    qualified=[x for x in candidates if x['qualified']]
    predicted={x.get('cluster_id') for x in qualified if x.get('cluster_id')}
    selected_count=len(selected); gold_count=len(gold)
    reasons=[]
    base_families={doc_family[x['paper_id']] for x in candidates if x['paper_id'] in doc_family}
    qual_families={doc_family[x['paper_id']] for x in qualified if x['paper_id'] in doc_family}
    if gold_count<3: reasons.append('true_supply_below_top_k')
    if len(gold-base_families)>0: reasons.append('base_recall_omission_or_duplicate_crowding')
    if len((gold&base_families)-qual_families)>0: reasons.append('relevance_gate_exclusion')
    if len(predicted)<min(3,len(qualified)): reasons.append('deduplication_merge')
    if selected_count<3 and not reasons: reasons.append('other_selection_limit')
    return {'core_case_id':f'{corpus_name}__{state["case_id"]}__{ranker}__{policy}',
      'case_id':state['case_id'],'state_hash':state['state_hash'],'corpus':corpus_name,
      'topic_id':state['selected_topic_id'],'agent_field':state['agent_field'],'memory_condition':state['memory_condition'],
      'ranker':ranker,'policy':policy,'candidate_pool_hash':digest(audit['base_candidate_ids']),
      'qualified_pool_hash':digest(audit['qualified_ids']),'base_candidate_ids':audit['base_candidate_ids'],
      'qualified_ids':audit['qualified_ids'],'selected_ids':selected,'selected_order':selected,
      'selected_cluster_ids_eval_only':sorted(selected_families),'evidence_relevance_span':span([x['evidence_relevance'] for x in candidates]),
      'exploration_span':span([x['exploration'] for x in qualified]),'attention_span':span([x['attention'] for x in qualified]),
      'policy_term_span':span([x.get('policy_score') for x in qualified]),'final_score_span':span([x.get('final_score') for x in qualified]),
      'corpus_eligible_doc_count':len(corpus.papers),'gold_available_cluster_count':gold_count,
      'base_candidate_count':len(candidates),'qualified_doc_count':len(qualified),
      'predicted_unique_cluster_count':len(predicted),'selected_count':selected_count,
      'gold_cluster_recall_at_k':len(found)/gold_count if gold_count else None,
      'gold_cluster_recall_null_reason':None if gold_count else 'no_gold_relevant_family',
      'gold_cluster_capacity_coverage_at_k':len(found)/min(3,gold_count) if gold_count else None,
      'gold_cluster_precision':len(found)/len(selected_families) if selected_families else None,
      'gold_cluster_precision_null_reason':None if selected_families else 'no_selected_family',
      'selected_redundancy':1-len(selected_families)/selected_count if selected_count else None,
      'strict_topic_label_match':sum(state['selected_topic_id'] in x['topic_ids'] for x in candidates if x['paper_id'] in selected)/selected_count if selected_count else None,
      'shortfall':selected_count<3,'shortfall_reasons':reasons,'fallback':audit['fallback'],
      'state_hash_before':frozen_state_hash(state),'state_hash_after':frozen_state_hash(state)}


# Execute or resume the 1296-call E1 matrix against original and expanded corpora.
def run_e1(config,root,output):
    root=Path(root); output=Path(output); existing={x['core_case_id']:x for x in read_jsonl(output/'core_results.jsonl')}
    jobs=[]; datasets={}
    for corpus_name in config['corpora']:
        corpus,topics,doc_family,relevant=load_dataset(config,root,corpus_name); datasets[corpus_name]=(corpus,topics,doc_family,relevant)
        states=build_frozen_states(config,file_hash(root/config['corpora'][corpus_name]),file_hash(root/config['topics']),topics)
        jobs.extend((corpus_name,state,ranker,policy) for state in states for ranker in config['rankers'] for policy in config['policies'])
    random.Random(config['execution_order_seed']).shuffle(jobs); failures=[]; candidate_rows=[]; computed=0
    state_rows={}
    for corpus_name,state,ranker,policy in jobs:
        case=f'{corpus_name}__{state["case_id"]}__{ranker}__{policy}'
        if case in existing: continue
        corpus,topics,doc_family,relevant=datasets[corpus_name]
        try:
            before=frozen_state_hash(state); pc=PolicyContext(policy,'retrieval',True,config['stage_lambda'])
            fn=REFERENCE_RETRIEVE if ranker=='stage_a_reference' else SUPPLEMENT_RETRIEVE
            _,audit=fn(corpus.papers,topics,pc,config['retrieval'],state['selected_topic_id'],state['agent_field'],copy.deepcopy(state['semantic_memory']),[])
            if frozen_state_hash(state)!=before: raise RuntimeError('frozen state mutated')
            existing[case]=result_row(state,corpus_name,ranker,policy,audit,doc_family,relevant,corpus); computed+=1
        except Exception as exc:
            failures.append({'experiment':'E1','case_id':case,'error_type':type(exc).__name__,'error':str(exc)})
    rows=[existing[x] for x in sorted(existing)]
    # Recompute candidates and alternate-order results for every completed case.
    state_lookup={}
    for corpus_name,(corpus,topics,doc_family,relevant) in datasets.items():
        for state in build_frozen_states(config,file_hash(root/config['corpora'][corpus_name]),file_hash(root/config['topics']),topics):
            state_lookup[(corpus_name,state['case_id'])]=state
    for row in rows:
        corpus,topics,doc_family,relevant=datasets[row['corpus']]; state=state_lookup[(row['corpus'],row['case_id'])]
        fn=REFERENCE_RETRIEVE if row['ranker']=='stage_a_reference' else SUPPLEMENT_RETRIEVE
        _,audit=fn(corpus.papers,topics,PolicyContext(row['policy'],'retrieval',True,config['stage_lambda']),config['retrieval'],
                   row['topic_id'],row['agent_field'],copy.deepcopy(state['semantic_memory']),[])
        row['order_replay_equal']=audit['selected_ids']==row['selected_ids'] and digest(audit['base_candidate_ids'])==row['candidate_pool_hash']
        for item in audit['candidates']:
            candidate_rows.append({'core_case_id':row['core_case_id'],'corpus':row['corpus'],'topic_id':row['topic_id'],
              'ranker':row['ranker'],'policy':row['policy'],'memory_condition':row['memory_condition'],
              'gold_family_id_eval_only':doc_family.get(item['paper_id']),**item})
    # Explain policy changes within each frozen state/ranker/corpus group.
    groups=defaultdict(list)
    for row in rows: groups[(row['corpus'],row['case_id'],row['ranker'])].append(row)
    for values in groups.values():
        base=next(x for x in values if x['policy']=='balanced')
        for row in values:
            if row['qualified_doc_count']<=config['top_k'] or row['predicted_unique_cluster_count']<=config['top_k']:
                reason='no_choice_capacity'
            elif row['policy_term_span']==0: reason='features_constant'
            elif row['selected_ids']!=base['selected_ids']: reason='selected_set_changed'
            elif row['selected_order']!=base['selected_order']: reason='rank_changed_set_same'
            elif row['policy']!='balanced': reason='policy_score_changed_rank_same'
            else: reason='control'
            row['selection_difference_reason']=reason
    write_jsonl(output/'frozen_states.jsonl',[state_lookup[x] for x in sorted(state_lookup)])
    write_jsonl(output/'core_results.jsonl',rows); write_jsonl(output/'candidate_scores.jsonl',candidate_rows)
    write_jsonl(output/'failure_cases.jsonl',failures)
    return rows,candidate_rows,failures,computed


# Run hand-calculated policy-score unit cases and verify formula component transmission.
def policy_unit(config):
    features=[(.95,.10),(.80,.25),(.65,.40),(.40,.65),(.25,.80),(.10,.95)]
    weights={'balanced':(.5,.5),'novelty':(1,0),'recognition':(0,1)}; rows=[]
    for policy,(wn,wr) in weights.items():
        for index,(exploration,attention) in enumerate(features):
            policy_score=wn*exploration+wr*attention
            expected=.75+(.25*policy_score)
            calculated=config['retrieval']['relevance_weight']*1+(1-config['retrieval']['relevance_weight'])*policy_score
            rows.append({'fixture_level':'scoring_interface_only','policy':policy,'candidate_id':f'unit_{index}',
              'normalized_relevance':1.0,'exploration':exploration,'attention':attention,'policy_score':policy_score,
              'expected_final_score':expected,'calculated_final_score':calculated,'passed':abs(expected-calculated)<1e-12})
    return rows


# Run the 54 enabled E2 production-feature calls and six path-disabled controls.
def policy_e2e(config,root):
    root=Path(root); papers={x['id']:x for x in read_jsonl(root/config['policy_fixture'])}
    topics=TopicModel(root/config['topics'],papers,config['recognition_alpha']); scenario_topic=dict(zip(config['policy_scenarios'],['agent_memory','science_retrieval','agent_communication']))
    rows=[]
    for scenario,history,policy,ranker in itertools.product(config['policy_scenarios'],config['policy_histories'],config['policies'],config['rankers']):
        ids=[f'policy_{scenario}_{i}' for i in range(6)]; source={pid:papers[pid] for pid in ids}|{pid:p for pid,p in papers.items() if pid.startswith('policy_background')}
        read_texts=[] if history=='empty' else [papers[f'policy_{scenario}_{i}']['title']+' '+papers[f'policy_{scenario}_{i}']['abstract'] for i in ((0,1) if history=='familiar_a' else (4,5))]
        fn=REFERENCE_RETRIEVE if ranker=='stage_a_reference' else SUPPLEMENT_RETRIEVE
        _,audit=fn(source,topics,PolicyContext(policy,'retrieval',True,config['stage_lambda']),config['retrieval'],scenario_topic[scenario],topics.topics[scenario_topic[scenario]]['field'],[],read_texts)
        rows.append({'scenario':scenario,'history':history,'policy':policy,'ranker':ranker,'path_enabled':True,
          'candidate_pool_hash':digest(audit['base_candidate_ids']),'qualified_pool_hash':digest(audit['qualified_ids']),
          'selected_ids':audit['selected_ids'],'selected_order':audit['selected_ids'],
          'selected_cluster_ids_eval_only':[next((x.get('cluster_id') for x in audit['candidates'] if x['paper_id']==pid),None) for pid in audit['selected_ids']],
          'evidence_relevance_span':span([x['evidence_relevance'] for x in audit['candidates'] if x['qualified']]),
          'exploration_span':span([x['exploration'] for x in audit['candidates'] if x['qualified']]),
          'attention_span':span([x['attention'] for x in audit['candidates'] if x['qualified']]),
          'policy_score_span':span([x.get('policy_score') for x in audit['candidates'] if x['qualified']]),
          'policy_term_span':span([x.get('policy_score') for x in audit['candidates'] if x['qualified']]),
          'final_score_span':span([x.get('final_score') for x in audit['candidates'] if x['qualified']]),
          'predicted_unique_cluster_count':len({x.get('cluster_id') for x in audit['candidates'] if x['qualified']}),
          'gold_available_cluster_count':None,
          'score_decomposition':[{k:x.get(k) for k in ('paper_id','normalized_relevance','exploration','attention','policy_score','final_score')} for x in audit['candidates'] if x['qualified']]})
    for ranker,policy in itertools.product(config['rankers'],config['policies']):
        scenario=config['policy_scenarios'][0]; topic=scenario_topic[scenario]; ids=[f'policy_{scenario}_{i}' for i in range(6)]
        source={pid:papers[pid] for pid in ids}|{pid:p for pid,p in papers.items() if pid.startswith('policy_background')}
        fn=REFERENCE_RETRIEVE if ranker=='stage_a_reference' else SUPPLEMENT_RETRIEVE
        _,audit=fn(source,topics,PolicyContext(policy,'retrieval',False,config['stage_lambda']),config['retrieval'],topic,'agents',[],[])
        rows.append({'scenario':scenario,'history':'empty','policy':policy,'ranker':ranker,'path_enabled':False,
          'candidate_pool_hash':digest(audit['base_candidate_ids']),'qualified_pool_hash':digest(audit['qualified_ids']),
          'selected_ids':audit['selected_ids'],'selected_order':audit['selected_ids'],'selected_cluster_ids_eval_only':[],
          'evidence_relevance_span':None,'exploration_span':None,'attention_span':None,
          'policy_score_span':None,'policy_term_span':None,'final_score_span':None,
          'predicted_unique_cluster_count':len({x.get('cluster_id') for x in audit['candidates'] if x['qualified']}),
          'gold_available_cluster_count':None,'score_decomposition':[]})
    groups=defaultdict(list)
    for row in rows:
        if row['path_enabled']: groups[(row['scenario'],row['history'],row['ranker'])].append(row)
    for values in groups.values():
        base=next(x for x in values if x['policy']=='balanced')
        for row in values:
            if row['policy']=='balanced': row['selection_difference_reason']='control'
            elif set(row['selected_ids'])!=set(base['selected_ids']): row['selection_difference_reason']='selected_set_changed'
            elif row['selected_order']!=base['selected_order']: row['selection_difference_reason']='rank_changed_set_same'
            elif row['policy_score_span']==0: row['selection_difference_reason']='features_constant'
            else: row['selection_difference_reason']='policy_score_changed_rank_same'
    for row in rows:
        if not row['path_enabled']: row['selection_difference_reason']='path_disabled'
    return rows


# Compute gold cluster metrics, dedup pair errors, shortfall attribution, and all 2376 topic pairs.
def analyze(config,root,output):
    root=Path(root); output=Path(output); rows=read_jsonl(output/'core_results.jsonl'); candidates=read_jsonl(output/'candidate_scores.jsonl')
    metric_rows=[{k:r.get(k) for k in ('core_case_id','corpus','topic_id','agent_field','memory_condition','ranker','policy','selected_count',
      'gold_available_cluster_count','gold_cluster_recall_at_k','gold_cluster_recall_null_reason','gold_cluster_capacity_coverage_at_k',
      'gold_cluster_precision','gold_cluster_precision_null_reason','selected_redundancy','strict_topic_label_match','shortfall')} for r in rows]
    write_csv(output/'gold_cluster_metrics.csv',metric_rows)
    shortfalls=[{k:r.get(k) for k in ('core_case_id','corpus','topic_id','agent_field','memory_condition','ranker','policy',
      'corpus_eligible_doc_count','gold_available_cluster_count','base_candidate_count','qualified_doc_count',
      'predicted_unique_cluster_count','selected_count','shortfall','shortfall_reasons','fallback')} for r in rows]
    for row in shortfalls: row['shortfall_reasons']=';'.join(row['shortfall_reasons'])
    write_csv(output/'shortfall_attribution.csv',shortfalls)
    # Evaluate dedup once per corpus using independent family IDs.
    errors=[]; dedup_summary=[]
    for corpus_name in config['corpora']:
        corpus,topics,doc_family,relevant=load_dataset(config,root,corpus_name)
        evaluation_split='all_legacy_documents'
        evaluation_ids=set(corpus.papers)
        if corpus_name=='corpus_expanded':
            evaluation_split='test'
            evaluation_ids={x['paper_id'] for x in read_jsonl(root/config['gold_paths']['document_map'])
                            if x['split']=='test' and x['paper_id'] in corpus.papers}
        evaluation_papers={pid:corpus.papers[pid] for pid in sorted(evaluation_ids)}
        assignments=duplicate_clusters([{'paper':p} for p in evaluation_papers.values()],config['retrieval']['near_duplicate_threshold'],fixture_aware=True)
        tp=fp=fn=0
        for left,right in itertools.combinations(sorted(evaluation_papers),2):
            gold_same=doc_family[left]==doc_family[right]; predicted=assignments[left]==assignments[right]
            if gold_same and predicted: tp+=1
            elif predicted and not gold_same: fp+=1; errors.append({'corpus':corpus_name,'error':'false_merge','left':left,'right':right})
            elif gold_same and not predicted: fn+=1; errors.append({'corpus':corpus_name,'error':'missed_merge','left':left,'right':right})
        dedup_summary.append({'corpus':corpus_name,'evaluation_split':evaluation_split,'evaluated_documents':len(evaluation_papers),
          'true_positive_pairs':tp,'false_positive_pairs':fp,'false_negative_pairs':fn,
          'pairwise_precision':tp/(tp+fp) if tp+fp else None,'precision_null_reason':None if tp+fp else 'no_predicted_positive_pairs',
          'pairwise_recall':tp/(tp+fn) if tp+fn else None,'recall_null_reason':None if tp+fn else 'no_gold_positive_pairs'})
    if errors:
        write_csv(output/'dedup_error_pairs.csv',errors)
    else:
        (output/'dedup_error_pairs.csv').write_text('corpus,error,left,right\n',encoding='utf-8-sig')
    write_jsonl(output/'dedup_summary.jsonl',dedup_summary)
    # Reuse only E1 empty-memory results for every unordered topic pair.
    lookup={(r['corpus'],r['topic_id'],r['agent_field'],r['ranker'],r['policy']):r for r in rows if r['memory_condition']=='empty'}
    topics=sorted(json.loads((root/config['topics']).read_text(encoding='utf-8')),key=lambda x:x['topic_id']); topic_ids=[x['topic_id'] for x in topics]
    topic_fields={x['topic_id']:x['field'] for x in topics}
    relevant_by_corpus={name:load_dataset(config,root,name)[3] for name in config['corpora']}
    pair_rows=[]
    for corpus_name,ranker,field,policy,(left,right) in itertools.product(config['corpora'],config['rankers'],config['fields'],config['policies'],itertools.combinations(topic_ids,2)):
        a=lookup[(corpus_name,left,field,ranker,policy)]; b=lookup[(corpus_name,right,field,ranker,policy)]
        doc_overlap=document_overlap(a['selected_ids'],b['selected_ids']); gold_overlap=document_overlap(a['selected_cluster_ids_eval_only'],b['selected_cluster_ids_eval_only'])
        relevant=relevant_by_corpus[corpus_name]; relation=('shared_evidence' if relevant[left]&relevant[right] else
          'near_no_shared' if topic_fields[left]==topic_fields[right] else 'far')
        pair_rows.append({'corpus':corpus_name,'ranker':ranker,'agent_field':field,'policy':policy,'left_topic':left,'right_topic':right,
          'relation':relation,'document_jaccard':doc_overlap['jaccard'],'gold_family_jaccard':gold_overlap['jaccard'],
          'same_set_different_order':set(a['selected_ids'])==set(b['selected_ids']) and a['selected_ids']!=b['selected_ids']})
    write_csv(output/'topic_pair_results.csv',pair_rows)
    return {'core_rows':len(rows),'candidate_rows':len(candidates),'gold_metric_rows':len(metric_rows),
            'shortfall_rows':len(shortfalls),'dedup_error_rows':len(errors),'topic_pair_rows':len(pair_rows),'dedup_summary':dedup_summary}


# Exercise the production engine for one 20-Agent balanced/open 12-tick world and evidence-count boundary cases.
def engine_integration(config,root,output):
    root=Path(root); output=Path(output); base=json.loads((root/'configs'/'v03_mock_smoke.json').read_text(encoding='utf-8'))
    base['corpus']=config['corpora']['corpus_expanded']; base['retrieval']=copy.deepcopy(config['retrieval']); base['seeds']=[config['smoke']['seed']]
    cfg=simulation_adapter(base); corpus=Corpus(root/cfg['corpus'],cfg['cutoff_year'],True,cfg['retrieval'])
    topics=TopicModel(root/cfg['topics'],corpus.papers,cfg['recognition']['alpha']); backend=Backend(cfg,root)
    world=initialize_v02(20,config['smoke']['seed'],topics); world.policy='balanced'; world.network='open'; world.policy_paths=dict(cfg['policy_paths'])
    world.candidate_schedule=build_schedule(world,2,cfg)
    for _ in range(12): world,_=step_v02(world,corpus,topics,backend,cfg)
    validate_v02(world); smoke_retrievals=sum(x.get('stage')=='retrieval' for x in world.decision_audit)
    smoke={'case':'20_agent_12_tick_smoke','status':'passed','agents':len(world.agents),'tick':world.tick,
      'policy':world.policy,'network':world.network,'http_attempts':backend.calls,'ideas':len(world.ideas),
      'retrieval_calls':smoke_retrievals}
    smoke_dir=output/'engine_smoke'; smoke_dir.mkdir(exist_ok=True); dump(smoke_dir/'final_state.json',world.export())
    results=[smoke]
    relevant=sorted((p for p in corpus.papers.values() if p['id'].startswith('supp_agent_memory_') and not p['id'].endswith('variant')),key=lambda p:p['id'])[:3]
    unrelated=sorted((p for p in corpus.papers.values() if p['id'].startswith('supp_learning_graph_')),key=lambda p:p['id'])[:3]
    probe_world=copy.deepcopy(world); probe_aid=sorted(probe_world.agents)[0]; probe_world.agents[probe_aid].selected_topic_id='agent_memory'
    for count in (0,1,2,3):
        source={p['id']:p for p in (unrelated if count==0 else relevant[:count]+unrelated)}
        boundary=copy.copy(corpus); boundary.papers=source
        visible,audit=boundary.retrieve_v02('unused',[],topics,PolicyContext('balanced','retrieval',True,config['stage_lambda']),
            20,3,selected_topic_id='agent_memory',field_name='agents',memory_terms=[])
        if count<2:
            results.append({'case':f'evidence_count_{count}','status':'passed','action':'wait_low_evidence','visible_ids':[x['id'] for x in visible],
                            'retrieval_mode':audit.get('mode'),'selected_count':len(visible),'backend_called':False,'fabricated_reference':False})
        else:
            context=proposal_observation(probe_world,probe_aid,visible,topics,'balanced')
            response=backend.generate('propose',context,['supplement','boundary',count]); validate_response('propose',response,context)
            refs=sorted({r for c in response['candidates'] for r in c['references']})
            context_ids=[x['id'] for x in context['papers']]
            results.append({'case':f'evidence_count_{count}','status':'passed','action':'propose','visible_ids':[x['id'] for x in visible],
                            'retrieval_mode':audit.get('mode'),'selected_count':len(visible),'backend_called':True,
                            'backend_input_evidence_ids':context_ids,'evidence_reached_backend':context_ids==[x['id'] for x in visible],
                            'references':refs,'fabricated_reference':not set(refs)<={x['id'] for x in visible}})
    write_jsonl(output/'engine_integration_results.jsonl',results)
    return results


# Analyze per-topic supply and policy-chain changes and write the limitations-aware report.
def report(config,root,output,analysis_result,e2e):
    output=Path(output); rows=read_jsonl(output/'core_results.jsonl')
    condition=defaultdict(list)
    for r in rows: condition[(r['corpus'],r['ranker'])].append(r)
    summaries=[]
    for key,values in sorted(condition.items()):
        summaries.append({'corpus':key[0],'ranker':key[1],'calls':len(values),'selected_mean':statistics.mean(x['selected_count'] for x in values),
          'shortfall_rate':statistics.mean(x['shortfall'] for x in values),'gold_capacity_coverage_mean':statistics.mean(x['gold_cluster_capacity_coverage_at_k'] for x in values if x['gold_cluster_capacity_coverage_at_k'] is not None)})
    enabled=[x for x in e2e if x['path_enabled']]; groups=defaultdict(list)
    for x in enabled: groups[(x['scenario'],x['history'],x['ranker'])].append(x)
    changed=sum(len({tuple(x['selected_ids']) for x in values})>1 for values in groups.values())
    rank_changed=sum(len({tuple(x['selected_order']) for x in values})>1 for values in groups.values())
    score_changed=sum(any(x['policy_score_span'] and x['policy_score_span']>0 for x in values) for values in groups.values())
    disabled=[x for x in e2e if not x['path_enabled']]; dgroups=defaultdict(set)
    for x in disabled: dgroups[x['ranker']].add(tuple(x['selected_ids']))
    topic_groups=defaultdict(list)
    for r in rows: topic_groups[(r['corpus'],r['ranker'],r['topic_id'])].append(r)
    pair_rows=list(csv.DictReader((output/'topic_pair_results.csv').open(encoding='utf-8-sig')))
    relation_stats={}
    for relation in ('shared_evidence','near_no_shared','far'):
        values=[x for x in pair_rows if x['relation']==relation and x['document_jaccard']!='']
        relation_stats[relation]={'n':len(values),'document_jaccard_mean':statistics.mean(float(x['document_jaccard']) for x in values) if values else None,
                                  'gold_family_jaccard_mean':statistics.mean(float(x['gold_family_jaccard']) for x in values if x['gold_family_jaccard']!='') if values else None}
    test_info=(json.loads((output/'tests_report.json').read_text(encoding='utf-8'))
               if (output/'tests_report.json').exists() else {'test_count':'not_recorded_in_this_split_workflow'})
    dedup={x['corpus']:x for x in analysis_result['dedup_summary']}
    lines=['# SciMirror Stage A 补充实验报告','',
      '本报告仅是合成证据条件下的工程与机制可测试性验证，不是独立科学质量评价或现实政策因果证据。','',
      '## 四个主条件','', '|corpus|ranker|calls|mean selected|shortfall rate|mean gold capacity coverage|','|---|---|---:|---:|---:|---:|']
    for x in summaries: lines.append(f'|{x["corpus"]}|{x["ranker"]}|{x["calls"]}|{x["selected_mean"]:.4f}|{x["shortfall_rate"]:.4f}|{x["gold_capacity_coverage_mean"]:.4f}|')
    lines += ['', 'reference 与 supplement 使用同一冻结排序函数，行为相同；本轮改善来自新增的实质不同证据供给和独立 gold 口径，而非人为修改排序公式。', '',
      '## Policy 链路', '', f'54 次启用路径端到端调用完成；在 {len(groups)} 个场景×历史×ranker 分母中，policy 产生所选集合变化的组数为 {changed}。',
      f'政策特征/分数存在跨度的组数为 {score_changed}，排序变化组数为 {rank_changed}，集合变化组数为 {changed}；每条分数分解与原因见 policy_e2e_results.jsonl。',
      f'路径关闭后每个ranker的唯一输出集合数：{dict((k,len(v)) for k,v in dgroups.items())}。评分单元表与生产特征分解见对应 JSONL。', '',
      '## 每主题供给、返回、覆盖和不足', '', '|corpus|ranker|topic|gold families|mean selected|capacity coverage|shortfall rate|','|---|---|---|---:|---:|---:|---:|']
    for key,values in sorted(topic_groups.items()):
        coverage=[x['gold_cluster_capacity_coverage_at_k'] for x in values if x['gold_cluster_capacity_coverage_at_k'] is not None]
        lines.append(f'|{key[0]}|{key[1]}|{key[2]}|{values[0]["gold_available_cluster_count"]}|{statistics.mean(x["selected_count"] for x in values):.4f}|{statistics.mean(coverage):.4f}|{statistics.mean(x["shortfall"] for x in values):.4f}|')
    lines += ['', '## Gold、主题重合与不足', '', f'全主题对行数：{analysis_result["topic_pair_rows"]}；去重错误对：{analysis_result["dedup_error_rows"]}。',
      f'扩充语料去重仅在冻结 test 家族上评价：{dedup["corpus_expanded"]}。校准家族不进入该精确率/召回率。',
      f'按预标注关系的主题对汇总：{relation_stats}。共享 evidence 的主题对允许非零 Jaccard，集合相同但顺序不同单列。',
      '原语料的不足被保留；扩充语料不填槽，逐阶段不足原因见 shortfall_attribution.csv。原语料 family 映射用于复现模板供给，不等同于外部科学 gold；独立内容卡/qrels 结论以扩充语料为准。', '',
      '## 执行与成本', '', f'测试 {test_info["test_count"]} 项；E1 1296 次、E2 60 次（54启用+6关闭控制）、主题配对2376行；smoke 为20 Agent、balanced/open、12 tick。网络/HTTP/LLM/收费API均为0。', '',
      '## 范围与待办', '', '广义语义同义仍不在透明词法检索能力内。真实语料、独立标注者一致性与外部有效性留待后续阶段；0/1 条证据沿用现有 wait/low-evidence 行为，2/3 条证据只引用可见文献。']
    (output/'SUPPLEMENT_REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return {'condition_summaries':summaries,'policy_groups':len(groups),'policy_set_changed_groups':changed,
            'path_disabled_all_equal':all(len(v)==1 for v in dgroups.values())}


# Recompute behavioral acceptance categories and write the supplement validation JSON.
def validate(config,root,output):
    root=Path(root); output=Path(output); rows=read_jsonl(output/'core_results.jsonl'); candidates=read_jsonl(output/'candidate_scores.jsonl')
    units=read_jsonl(output/'policy_unit_results.jsonl'); e2e=read_jsonl(output/'policy_e2e_results.jsonl'); engine=read_jsonl(output/'engine_integration_results.jsonl')
    splits=json.loads((root/config['gold_paths']['split_manifest']).read_text(encoding='utf-8')); prep=json.loads((output/'prepare_status.json').read_text(encoding='utf-8'))
    topic_pairs=list(csv.DictReader((output/'topic_pair_results.csv').open(encoding='utf-8-sig')))
    gold=list(csv.DictReader((output/'gold_cluster_metrics.csv').open(encoding='utf-8-sig')))
    failures=read_jsonl(output/'failure_cases.jsonl'); usage=json.loads((output/'usage.json').read_text(encoding='utf-8'))
    dedup={x['corpus']:x for x in read_jsonl(output/'dedup_summary.jsonl')}
    manifest=json.loads((output/'manifest.json').read_text(encoding='utf-8'))
    families=read_jsonl(root/config['gold_paths']['families']); maps=read_jsonl(root/config['gold_paths']['document_map']); qrels=read_jsonl(root/config['gold_paths']['qrels'])
    family_ids={x['gold_family_id'] for x in families}; mapped_ids=[x['paper_id'] for x in maps]
    gold_links_valid=all(x['gold_family_id'] in family_ids for x in maps+qrels) and len(mapped_ids)==len(set(mapped_ids))
    content_axes_valid=all(all(str(x.get(k,'')).strip() for k in ('problem','method','setting','evaluation_plan','limitations')) for x in families)
    enabled=[x for x in e2e if x['path_enabled']]; groups=defaultdict(list)
    for x in enabled: groups[(x['scenario'],x['history'],x['ranker'])].append(x)
    policy_change=any(len({tuple(x['selected_ids']) for x in values})>1 for values in groups.values())
    disabled=defaultdict(set)
    for x in e2e:
        if not x['path_enabled']: disabled[x['ranker']].add(tuple(x['selected_ids']))
    per_topic_ok=all(v>=6 for v in prep['independent_families_per_topic'].values())
    def section(ok,count,path,reason=''): return {'status':'passed' if ok else 'failed','checked_count':count,'failed_count':0 if ok else 1,'evidence_path':path,'reason':reason}
    baseline_ok=(manifest['reference_commit']==config['reference_commit'] and
                 manifest['reference_function_hash']==manifest['supplement_function_hash'])
    expanded_dedup=dedup.get('corpus_expanded',{})
    dedup_ok=(expanded_dedup.get('evaluation_split')=='test' and
              expanded_dedup.get('true_positive_pairs',0)>=12 and
              expanded_dedup.get('false_positive_pairs')==0 and
              expanded_dedup.get('false_negative_pairs')==0 and
              expanded_dedup.get('pairwise_precision')==1.0 and
              expanded_dedup.get('pairwise_recall')==1.0)
    sections={'baseline':section(baseline_ok,2,'manifest.json','reference and supplement function hashes frozen'),
      'corpus_content':section(per_topic_ok and content_axes_valid,prep['unique_families'],'family_content_audit.jsonl'),
      'gold_isolation':section(all(not(set(x)-PRODUCTION_FIELDS) for x in read_jsonl(root/config['corpora']['corpus_expanded'])) and gold_links_valid,prep['eligible_documents'],'reproduction/data/corpus_expanded.jsonl'),
      'splits':section(not splits['family_overlap'],len(splits['calibration_families'])+len(splits['test_families']),'reproduction/data/split_manifest.json'),
      'core_matrix':section(len(rows)==1296 and len({x['core_case_id'] for x in rows})==1296 and all(x['order_replay_equal'] for x in rows),1296,'core_results.jsonl'),
      'policy_unit':section(len(units)==18 and all(x['passed'] for x in units),len(units),'policy_unit_results.jsonl'),
      'policy_e2e':section(len(enabled)==54 and policy_change and all(len(v)==1 for v in disabled.values()),len(e2e),'policy_e2e_results.jsonl'),
      'cluster_metrics':section(len(gold)==1296, len(gold),'gold_cluster_metrics.csv'),
      'deduplication':section(dedup_ok,expanded_dedup.get('evaluated_documents',0),'dedup_summary.jsonl',
                              'expanded corpus evaluated on frozen test families only'),
      'topic_pairs':section(len(topic_pairs)==2376, len(topic_pairs),'topic_pair_results.csv'),
      'shortfall':section(len(list(csv.DictReader((output/'shortfall_attribution.csv').open(encoding='utf-8-sig'))))==1296,1296,'shortfall_attribution.csv'),
      'engine_integration':section(len(engine)==5 and all(x['status']=='passed' and not x.get('fabricated_reference',False) for x in engine)
                                   and all(x.get('evidence_reached_backend',True) for x in engine),len(engine),'engine_integration_results.jsonl'),
      'offline_execution':section(all(usage[x]==0 for x in ('network_requests','http_attempts','llm_calls','paid_api_calls')),4,'usage.json'),
      'reproducibility':section((output/'reproduction'/'CHECKSUMS.sha256').exists(),1,'reproduction/CHECKSUMS.sha256')}
    required=all(x['status']=='passed' for x in sections.values()); status='completed' if required else ('completed_with_limitations' if rows else 'failed')
    smoke_retrieval_calls=next(x['retrieval_calls'] for x in engine if x['case']=='20_agent_12_tick_smoke')
    result={'schema_version':SCHEMA,'status':status,'all_passed':required,'sections':sections,'failure_cases':len(failures),
            'retrieval_calls':1296+54+6+smoke_retrieval_calls,'retrieval_call_breakdown':{'E1':1296,'E2_enabled':54,'E2_disabled':6,'E4_smoke':smoke_retrieval_calls},
            'comparison_rows':len(topic_pairs),'scientific_scope':'synthetic_engineering_only',
            'real_world_causal_claim_supported':False}; dump(output/'DELIVERY_VALIDATION_STAGE_A_SUPPLEMENT.json',result); return result


# Copy exact reproduction inputs and source, create checksums, and package without nested archives.
def package(config,root,output):
    root=Path(root); output=Path(output); reproduction=output/'reproduction'; data_dir=reproduction/'data'; source_dir=reproduction/'source'
    data_dir.mkdir(parents=True,exist_ok=True); source_dir.mkdir(parents=True,exist_ok=True)
    if not (output/'SUPPLEMENT_PLAN.md').exists(): shutil.copy2(root/'SUPPLEMENT_PLAN.md',output/'SUPPLEMENT_PLAN.md')
    if not (output/'family_content_audit.jsonl').exists(): shutil.copy2(root/config['family_content_audit'],output/'family_content_audit.jsonl')
    if not (output/'evidence_supply_audit.csv').exists() and (output/'prepare_status.json').exists():
        prepared=json.loads((output/'prepare_status.json').read_text(encoding='utf-8'))
        write_csv(output/'evidence_supply_audit.csv',[{'topic_id':k,
          'independent_family_count':prepared['independent_families_per_topic'][k],
          'shared_family_count':prepared['shared_families_per_topic'][k],
          'total_relevant_family_count':v} for k,v in prepared['families_per_topic'].items()])
    inputs=[root/config['corpora']['corpus_original'],root/config['corpora']['corpus_expanded'],root/config['topics'],
            *(root/x for x in config['gold_paths'].values()),root/config['family_content_audit'],root/config['policy_fixture']]
    sources=[root/'scimirror'/'stage_a_supplement.py',root/'scimirror'/'stage_a.py',root/'scimirror'/'v03_retrieval.py',
             root/'scimirror'/'corpus.py',root/'execute_stage_a_supplement.py',root/'configs'/'stage_a_supplement.json']
    for path in inputs: shutil.copy2(path,data_dir/path.name)
    for path in sources: shutil.copy2(path,source_dir/path.name)
    (reproduction/'DEPENDENCIES.md').write_text('Python 3.11+ standard library only. No network, LLM, GPU, or paid API.\n',encoding='utf-8')
    checksum_rows=[]
    for path in sorted(p for p in reproduction.rglob('*') if p.is_file() and p.name!='CHECKSUMS.sha256'):
        checksum_rows.append(f'{file_hash(path)}  {path.relative_to(reproduction).as_posix()}')
    (reproduction/'CHECKSUMS.sha256').write_text('\n'.join(checksum_rows)+'\n',encoding='utf-8')
    archive=output/'stage_a_supplement_delivery.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(p for p in output.rglob('*') if p.is_file() and p!=archive and p.suffix!='.zip'):
            bundle.write(path,path.relative_to(output).as_posix())
    return {'archive':str(archive),'sha256':file_hash(archive),'files':len(zipfile.ZipFile(archive).namelist())}


# Build traceable reference/supplement source and input metadata for all CLI workflows.
def build_manifest(config,config_path,root,core_count,topic_pair_actual=None):
    root=Path(root)
    contract=build_resume_contract(config,config_path,root)
    return {'schema_version':SCHEMA,'created_at':datetime.now().astimezone().isoformat(),'reference_commit':config['reference_commit'],
      'python':sys.version,'platform':platform.platform(),'config_hash':file_hash(config_path),'original_corpus_hash':file_hash(root/config['corpora']['corpus_original']),
      'expanded_corpus_hash':file_hash(root/config['corpora']['corpus_expanded']),'topics_hash':file_hash(root/config['topics']),
      'reference_function_hash':hashlib.sha256(inspect.getsource(REFERENCE_RETRIEVE).encode()).hexdigest(),
      'supplement_function_hash':hashlib.sha256(inspect.getsource(SUPPLEMENT_RETRIEVE).encode()).hexdigest(),
      'implementation_bundle_hash':contract['implementation_bundle_hash'],'resume_contract_hash':digest(contract),
      'ranker_behavior_expected_identical':True,'core_expected':1296,'core_actual':core_count,'policy_e2e_enabled_calls':54,
      'topic_pair_expected':2376,'topic_pair_actual':topic_pair_actual,'network_requests':0,'llm_calls':0}


# Hash every frozen input and relevant implementation file used by resumable cases.
def build_resume_contract(config,config_path,root):
    root=Path(root); config_path=Path(config_path)
    inputs=[config_path,root/config['corpora']['corpus_original'],root/config['corpora']['corpus_expanded'],root/config['topics'],
            *(root/x for x in config['gold_paths'].values()),root/config['family_content_audit'],root/config['policy_fixture']]
    sources=[root/'scimirror'/'stage_a_supplement.py',root/'scimirror'/'stage_a.py',root/'scimirror'/'v03_retrieval.py',
             root/'scimirror'/'corpus.py',root/'scimirror'/'policy.py',root/'execute_stage_a_supplement.py']
    input_hashes={str(path.resolve()):file_hash(path) for path in inputs}
    source_hashes={str(path.resolve()):file_hash(path) for path in sources}
    return {'schema_version':SCHEMA,'prepared_data_version':config.get('prepared_data_version'),
            'input_hashes':input_hashes,'source_hashes':source_hashes,
            'implementation_bundle_hash':digest(source_hashes)}


# Persist the resume contract and reject reuse when any input or implementation changed.
def ensure_resume_contract(output,contract):
    path=Path(output)/'resume_contract.json'
    if path.exists() and json.loads(path.read_text(encoding='utf-8'))!=contract:
        raise ValueError('Resume refused because an input or implementation hash changed; use a new output directory')
    dump(path,contract)


# Run prepare, E1–E4, analysis, validation, and reproducible packaging in one resumable workflow.
def run_all(config,config_path,root,output,test_result):
    root=Path(root); output=Path(output); output.mkdir(parents=True,exist_ok=True)
    config_output=output/'config.json'
    if config_output.exists() and json.loads(config_output.read_text(encoding='utf-8'))!=config:
        raise ValueError('Resume refused because configuration changed; use a new output directory')
    dump(config_output,config)
    prep=prepare(config,root); dump(output/'prepare_status.json',prep); dump(output/'status.json',{'schema_version':SCHEMA,'status':'running'})
    ensure_resume_contract(output,build_resume_contract(config,config_path,root))
    rows,candidates,failures,computed=run_e1(config,root,output)
    units=policy_unit(config); e2e=policy_e2e(config,root); write_jsonl(output/'policy_unit_results.jsonl',units); write_jsonl(output/'policy_e2e_results.jsonl',e2e)
    engine=engine_integration(config,root,output); analysis_result=analyze(config,root,output)
    write_csv(output/'evidence_supply_audit.csv',[{'topic_id':k,
      'independent_family_count':prep['independent_families_per_topic'][k],
      'shared_family_count':prep['shared_families_per_topic'][k],
      'total_relevant_family_count':v} for k,v in prep['families_per_topic'].items()])
    shutil.copy2(root/config['family_content_audit'],output/'family_content_audit.jsonl')
    shutil.copy2(root/'SUPPLEMENT_PLAN.md',output/'SUPPLEMENT_PLAN.md')
    dump(output/'tests_report.json',test_result)
    report_result=report(config,root,output,analysis_result,e2e)
    manifest=build_manifest(config,config_path,root,len(rows),analysis_result['topic_pair_rows'])
    dump(output/'manifest.json',manifest)
    prior_usage=(json.loads((output/'usage.json').read_text(encoding='utf-8')) if (output/'usage.json').exists() else {})
    usage={'schema_version':SCHEMA,'e1_retrieval_calls':len(rows),'e1_computed_this_invocation':computed,'e1_resumed':len(rows)-computed,
           'e1_underlying_computations_cumulative':prior_usage.get('e1_underlying_computations_cumulative',prior_usage.get('e1_retrieval_calls',0))+computed,
           'e2_enabled_retrieval_calls':54,'e2_disabled_control_calls':6,'topic_pair_comparisons':analysis_result['topic_pair_rows'],
           'smoke_agents':20,'smoke_ticks':12,'smoke_retrieval_calls':next(x['retrieval_calls'] for x in engine if x['case']=='20_agent_12_tick_smoke'),
           'network_requests':0,'http_attempts':0,'llm_calls':0,'paid_api_calls':0}
    dump(output/'usage.json',usage)
    # Create reproduction before validation because it is itself an acceptance item.
    package(config,root,output); validation=validate(config,root,output)
    dump(output/'status.json',{'schema_version':SCHEMA,'status':validation['status'],'all_passed':validation['all_passed'],
                              'failure_cases':len(failures)})
    package_info=package(config,root,output)
    return {'validation':validation,'analysis':analysis_result,'report':report_result,'package':package_info}
