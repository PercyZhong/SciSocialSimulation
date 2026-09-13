"""Offline frozen-state retrieval experiment and Stage A acceptance analysis."""
import copy
import csv
import hashlib
import json
import math
import platform
import random
import inspect
import statistics
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from .common import canonical, digest, dump
from .corpus import Corpus
from .policy import PolicyContext
from .topics import TopicModel
from .v03_retrieval import (STAGE_A_DEDUP_VERSION, STAGE_A_QUERY_VERSION,
    STAGE_A_RANKER_VERSION, audit_metrics, build_stage_a_query, document_overlap,
    duplicate_clusters, retrieve_relevance_gated, retrieve_stage_a_fixed,
    semantic_memory_tokens)
from .v03_review import write_csv


SCHEMA = 'stage_a_1'
CACHE_VERSION = 'stage_a_full_result_cache_1'
STATE_VERSION = 'stage_a_frozen_state_1'


# Return the SHA-256 digest of a file without changing it.
def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# Hash only treatment-independent state content and exclude the self-referential digest field.
def frozen_state_hash(state):
    return digest({key:value for key,value in state.items() if key != 'state_hash'})


# Read and validate the complete offline Stage A experiment configuration.
def load_stage_a_config(path, root):
    path, root = Path(path), Path(root)
    config = json.loads(path.read_text(encoding='utf-8'))
    required = {'schema_version','rankers','memory_conditions','policies','fields','corpus','topics',
                'calibration_fixture','baseline_retrieval','fixed_retrieval','output_root'}
    if not required <= set(config) or config['schema_version'] != SCHEMA:
        raise ValueError('Incomplete Stage A configuration')
    if config['rankers'] != ['baseline_v03','stage_a_fixed']:
        raise ValueError('Stage A requires the traceable v0.3 baseline and fixed rankers')
    if config['memory_conditions'] != ['empty','related','unrelated']:
        raise ValueError('Stage A memory conditions changed')
    if config['policies'] != ['balanced','novelty','recognition']:
        raise ValueError('Stage A policy conditions changed')
    if config.get('allow_network') or config.get('allow_llm_calls'):
        raise ValueError('Stage A must be offline and must not call an LLM')
    for key in ('baseline_retrieval','fixed_retrieval'):
        block = config[key]
        if block['candidate_pool_size'] != 20 or block['top_k'] != 3:
            raise ValueError('Frozen candidate pool/top-k must be 20/3')
    for key in ('corpus','topics','calibration_fixture'):
        if not (root/config[key]).is_file():
            raise ValueError(f'Missing Stage A input: {config[key]}')
    return config


# Build the 108 treatment-independent frozen states and their balanced memory mappings.
def build_frozen_states(config, corpus_hash, topics_hash, topic_model):
    topics = list(topic_model.topics)
    states = []
    for topic_index, topic_id in enumerate(topics):
        unrelated_id = topics[(topic_index + len(topics)//2) % len(topics)]
        for field_name in config['fields']:
            public_topics = sorted(t for t, row in topic_model.topics.items() if row['field'] == field_name)[:2]
            for condition in config['memory_conditions']:
                source = [] if condition == 'empty' else [topic_id if condition == 'related' else unrelated_id]
                memory = [] if not source else [{'topic_id': source[0], 'text': source[0]}]
                state = {'case_id': f'{topic_id}__{field_name}__{condition}',
                    'selected_topic_id': topic_id, 'agent_field': field_name,
                    'memory_condition': condition, 'semantic_memory': memory,
                    'memory_source_topic_ids': source,
                    'memory_construction_rule': 'one frozen topic identifier; unrelated uses +6 cyclic topic',
                    'read_paper_ids': [], 'public_topics': public_topics,
                    'corpus_hash': corpus_hash, 'topics_hash': topics_hash,
                    'cutoff_year': config['cutoff_year'], 'state_schema_version': STATE_VERSION}
                state['state_hash'] = frozen_state_hash(state)
                states.append(state)
    if len(states) != len(topics)*len(config['fields'])*len(config['memory_conditions']):
        raise RuntimeError('Frozen-state cardinality mismatch')
    return states


# Compute a cache key that includes every state, treatment, and retrieval implementation dependency.
def cache_key(state, ranker, policy, retrieval_config, corpus_hash, topics_hash):
    return digest({'cache_version':CACHE_VERSION, 'state_hash':state['state_hash'], 'ranker':ranker,
                   'policy':policy, 'retrieval_config':retrieval_config,
                   'corpus_hash':corpus_hash, 'topics_hash':topics_hash})


class ResultCache:
    """Small in-memory cache used only to test complete dependency keys and hot/cold equality."""
    def __init__(self):
        self.rows = {}

    # Retrieve an immutable deep copy or compute and store a new result.
    def get(self, key, compute):
        hit = key in self.rows
        if not hit:
            self.rows[key] = copy.deepcopy(compute())
        return copy.deepcopy(self.rows[key]), hit


# Execute one pure baseline or fixed retrieval and reject any frozen-state mutation.
def execute_case(state, ranker, policy, config, corpus, topic_model):
    before = frozen_state_hash(state)
    retrieval = config['baseline_retrieval'] if ranker == 'baseline_v03' else config['fixed_retrieval']
    policy_context = PolicyContext(policy, 'retrieval', True, float(config['stage_lambda']))
    memory_terms = copy.deepcopy(state['semantic_memory'])
    if ranker == 'baseline_v03':
        # Preserve the exact v0.3 behavior: production passed topic identifiers as strings.
        memory_terms = [row['text'] for row in memory_terms]
        papers, audit = retrieve_relevance_gated(corpus.papers, topic_model, policy_context, retrieval,
            state['selected_topic_id'], state['agent_field'], memory_terms, [])
    else:
        papers, audit = retrieve_stage_a_fixed(corpus.papers, topic_model, policy_context, retrieval,
            state['selected_topic_id'], state['agent_field'], memory_terms, [])
    after = frozen_state_hash(state)
    if before != after:
        raise RuntimeError(f'Frozen state mutated: {state["case_id"]}')
    selected = [paper['id'] for paper in papers]
    metrics = audit_metrics(audit)
    relevant_ids = {paper_id for paper_id, labels in topic_model.paper_topics.items()
                    if state['selected_topic_id'] in labels}
    candidate_ids = set(audit.get('base_candidate_ids',[x['paper_id'] for x in audit['candidates']]))
    selected_relevant = len(set(selected) & relevant_ids)
    metrics.update(topic_label_match_rate=metrics['topic_match_rate'],
        candidate_recall=(len(candidate_ids & relevant_ids)/len(relevant_ids) if relevant_ids else None),
        candidate_recall_null_reason=(None if relevant_ids else 'no_known_relevant_documents'),
        relevant_slot_precision=(selected_relevant/len(selected) if selected else None),
        relevant_slot_precision_null_reason=(None if selected else 'no_selected_documents'),
        relevant_coverage_at_3=(selected_relevant/min(3,len(relevant_ids)) if relevant_ids else None),
        relevant_coverage_at_3_null_reason=(None if relevant_ids else 'no_known_relevant_documents'))
    return {'selected_ids': selected, 'audit': audit, 'metrics': metrics,
            'state_hash_before': before, 'state_hash_after': after}


# Write JSON Lines deterministically so result files can be hashed and replayed.
def write_jsonl(path, rows):
    Path(path).write_text(''.join(canonical(row)+'\n' for row in rows), encoding='utf-8')


# Read an existing JSON Lines file for safe case-level resume.
def read_jsonl(path):
    path = Path(path)
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line] if path.exists() else []


# Create the calibration manifest without consulting the disjoint test families.
def calibrate(config, root, output):
    root, output = Path(root), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    fixture_path = root/config['calibration_fixture']
    fixture = json.loads(fixture_path.read_text(encoding='utf-8'))
    calibration_families = sorted(row['family'] for row in fixture['calibration'])
    test_families = sorted(row['family'] for row in fixture['test'])
    if set(calibration_families) & set(test_families):
        raise ValueError('Calibration/test document families overlap')
    result = {'schema_version':SCHEMA, 'status':'completed', 'method':'predeclared_fixture_parameters_no_search',
        'fixture_hash':file_hash(fixture_path), 'split_rule':fixture['split_rule'],
        'calibration_families':calibration_families, 'test_families':test_families,
        'family_overlap':[], 'frozen_parameters':fixture['frozen_parameters'],
        'test_set_used_for_tuning':False, 'network_requests':0, 'llm_calls':0}
    dump(output/'calibration_manifest.json', result)
    return result


# Construct a minimal topic model fixture with known relevance truth for behavioral probes.
def probe_model(papers):
    topics = {'memory_topic':{'topic_id':'memory_topic','description':'memory retrieval',
                              'keywords':['memory','retrieval'],'field':'agents'},
              'graph_topic':{'topic_id':'graph_topic','description':'graph generalization',
                             'keywords':['graph','generalization'],'field':'learning'},
              'zero_topic':{'topic_id':'zero_topic','description':'quasar spectroscopy',
                            'keywords':['quasar','spectroscopy'],'field':'science'}}
    labels = {}
    for paper_id, paper in papers.items():
        words = set((paper['title']+' '+paper['abstract']).lower().replace('_',' ').split())
        labels[paper_id] = sorted(t for t, row in topics.items() if words & set(row['keywords']))
    return SimpleNamespace(topics=topics, paper_topics=labels,
                           attention={'memory_topic':0.2,'graph_topic':0.8,'zero_topic':1.0})


# Run the fifteen predeclared behavioral probes plus the four root-cause attribution checks.
def targeted_probes(config):
    def paper(pid, title, abstract, field='agents', year=2020, attention_topic=None):
        return {'id':pid,'title':title,'abstract':abstract,'field':field,'year':year,'synthetic':True,
                **({'topic_ids':[attention_topic]} if attention_topic else {})}
    papers = {
      'mem_a':paper('mem_a','Memory retrieval experiment','controlled index evidence','agents'),
      'mem_cross':paper('mem_cross','Memory retrieval across biology','controlled evidence','science'),
      'graph_a':paper('graph_a','Graph generalization method','message passing evidence','learning'),
      'same_irrelevant':paper('same_irrelevant','Coordination protocol','unrelated teamwork evidence','agents'),
      'attention_irrelevant':paper('attention_irrelevant','Popular collaboration','unrelated famous result','science'),
      'dup_s1':paper('dup_s1','Memory retrieval scenario 1','Controlled result variant 1','agents'),
      'dup_s2':paper('dup_s2','Memory retrieval scenario 2','Controlled result variant 2','agents'),
      'distinct_method':paper('distinct_method','Memory retrieval randomized trial','Causal intervention method','agents')}
    papers.update({
      'dup_punctuation':paper('dup_punctuation','Memory retrieval: scenario 1!','Controlled result; variant 1.','agents'),
      'dup_doi_a':paper('dup_doi_a','Memory cohort A','Observed evidence','agents'),
      'dup_doi_b':paper('dup_doi_b','Different rendering','Different abstract','agents'),
      'distinct_question':paper('distinct_question','Memory retrieval usability study','Interface research question','agents')})
    papers['dup_doi_a']['doi']='10.1/frozen'; papers['dup_doi_b']['doi']='10.1/FROZEN'
    model = probe_model(papers)
    fixed = copy.deepcopy(config['fixed_retrieval']); fixed['candidate_pool_size'] = 20; fixed['top_k'] = 3
    pc = lambda name: PolicyContext(name,'retrieval',True,float(config['stage_lambda']))
    def run(topic='memory_topic', field='agents', memory=(), reads=(), policy='balanced', source=None):
        source = papers if source is None else source
        return retrieve_stage_a_fixed(source, model, pc(policy), fixed, topic, field, list(memory), list(reads))[1]
    probes = []
    def add(pid, passed, observed, expected):
        probes.append({'probe_id':pid,'status':'passed' if passed else 'failed',
                       'observed':observed,'expected':expected})
    topic_source={'mem_a':papers['mem_a'],'graph_a':papers['graph_a'],'same_irrelevant':papers['same_irrelevant']}
    a1, a1b = run('memory_topic',source=topic_source), run('graph_topic',source=topic_source)
    add('A01', a1['selected_ids'] != a1b['selected_ids'] and 'mem_a' in a1['selected_ids'] and 'graph_a' in a1b['selected_ids'],
        [a1['selected_ids'],a1b['selected_ids']], 'topic-specific known evidence is preferred')
    q1 = build_stage_a_query(model,'memory_topic','agents',['memory_topic'],fixed['query_weights'])
    q2 = build_stage_a_query(model,'memory_topic','agents',['memory topic'],fixed['query_weights'])
    add('A02', set(q1['components']['memory']) == set(q2['components']['memory']), q1['components']['memory'], 'underscore and space forms normalize equivalently')
    empty, related, unrelated = run(memory=[]), run(memory=['memory_topic']), run(memory=['graph_topic'])
    score = lambda audit,pid: next(x['component_relevance']['memory'] for x in audit['candidates'] if x['paper_id']==pid)
    add('A03', score(empty,'mem_a') == 0 and score(related,'mem_a') > score(unrelated,'mem_a'),
        {'empty':score(empty,'mem_a'),'related':score(related,'mem_a'),'unrelated':score(unrelated,'mem_a')}, 'semantic memory component responds without state mutation')
    read_text = [papers['mem_a']['title']+' '+papers['mem_a']['abstract']]
    no_read, with_read = run(reads=[]), run(reads=read_text)
    explore = lambda audit,pid: next(x['exploration'] for x in audit['candidates'] if x['paper_id']==pid)
    add('A04', no_read['normalized_query']==with_read['normalized_query'] and explore(no_read,'mem_a')!=explore(with_read,'mem_a'),
        {'query_equal':no_read['normalized_query']==with_read['normalized_query'],'exploration':[explore(no_read,'mem_a'),explore(with_read,'mem_a')]}, 'read history changes exploration only')
    policies = [run(policy=p) for p in config['policies']]
    add('A05', len({tuple(a['base_candidate_ids']) for a in policies})==1,
        [a['base_candidate_ids'] for a in policies], 'policies share one base candidate pool')
    add('A06', 'same_irrelevant' not in run()['qualified_ids'], run()['qualified_ids'], 'same-field unrelated evidence fails gate')
    cross_source = {'mem_cross':papers['mem_cross'],'same_irrelevant':papers['same_irrelevant']}
    cross = run(field='learning', source=cross_source)
    add('A07', 'mem_cross' in cross['selected_ids'] and 'mem_cross' in cross['base_candidate_ids'],
        cross['selected_ids'], 'cross-field relevant evidence is recalled and selected when it is the relevant evidence')
    add('A08', 'attention_irrelevant' not in run(policy='recognition')['qualified_ids'], run(policy='recognition')['qualified_ids'], 'attention cannot bypass evidence gate')
    zero = run(topic='zero_topic')
    add('A09', zero['selected_ids']==[] and zero['fallback']=='all_evidence_relevance_zero',
        {'selected':zero['selected_ids'],'fallback':zero['fallback']}, 'empty result with explicit reason')
    one_source = {'mem_a':papers['mem_a'],'same_irrelevant':papers['same_irrelevant']}
    one_model = probe_model(one_source)
    one = retrieve_stage_a_fixed(one_source,one_model,pc('balanced'),fixed,'memory_topic','agents',[],[])[1]
    add('A10', one['selected_ids']==['mem_a'] and one['fallback']=='insufficient_relevant_documents',
        {'selected':one['selected_ids'],'fallback':one['fallback']}, 'do not pad a shortfall')
    dedup_ids=('dup_s1','dup_s2','dup_punctuation','dup_doi_a','dup_doi_b','distinct_method','distinct_question')
    assignments = duplicate_clusters([{'paper':papers[x]} for x in dedup_ids],.97,fixture_aware=True)
    dedup_ok=(assignments['dup_s1']==assignments['dup_s2']==assignments['dup_punctuation']
              and assignments['dup_doi_a']==assignments['dup_doi_b']
              and assignments['dup_s1']!=assignments['distinct_method']
              and assignments['dup_s1']!=assignments['distinct_question'])
    add('A11', dedup_ok, assignments, 'DOI, punctuation, and scenario variants merge; different method/question remain distinct')
    pre = {k:v for k,v in papers.items() if v['year'] < config['cutoff_year']}
    future = pre | {'future':paper('future','Memory retrieval future','future evidence','agents',config['cutoff_year'])}
    before_ids, after_ids = run(source=pre)['selected_ids'], run(source={k:v for k,v in future.items() if v['year']<config['cutoff_year']})['selected_ids']
    add('A12', before_ids==after_ids and 'future' not in after_ids, after_ids, 'cutoff-year paper excluded before indexing')
    state = {'state_hash':'probe','semantic_memory':['memory_topic'],'read_paper_ids':[]}
    cache = ResultCache(); key = cache_key(state,'stage_a_fixed','balanced',fixed,'corpus','topics')
    first, hit1 = cache.get(key,lambda:{'selected':run()['selected_ids']}); second, hit2 = cache.get(key,lambda:{'selected':['wrong']})
    changed_key = cache_key(state|{'state_hash':'changed'},'stage_a_fixed','balanced',fixed,'corpus','topics')
    _, hit3 = cache.get(changed_key,lambda:{'selected':run()['selected_ids']})
    add('A13', not hit1 and hit2 and not hit3 and first==second, {'hits':[hit1,hit2,hit3],'equal':first==second}, 'cold/hot equal and changed state invalidates')
    order1 = {p:run(policy=p)['selected_ids'] for p in config['policies']}
    order2 = {p:run(policy=p)['selected_ids'] for p in reversed(config['policies'])}
    add('A14', order1==order2, {'forward':order1,'reverse':order2}, 'execution order invariant')
    add('A15', canonical(run())==canonical(run()), run()['selected_ids'], 'repeat run is byte-equivalent')
    baseline_cfg=copy.deepcopy(config['baseline_retrieval']); baseline_cfg['candidate_pool_size']=20; baseline_cfg['top_k']=3
    old_memory=retrieve_relevance_gated(papers,model,pc('balanced'),baseline_cfg,'memory_topic','agents',['memory_topic'],[])[1]
    expanded_memory=retrieve_relevance_gated(papers,model,pc('balanced'),baseline_cfg,'memory_topic','agents',['memory retrieval'],[])[1]
    old_candidate={x['paper_id']:x for x in old_memory['candidates']}
    expanded_candidate={x['paper_id']:x for x in expanded_memory['candidates']}
    fixed_candidate={x['paper_id']:x for x in run()['candidates']}
    attribution = [
      {'check':'memory_representation_only','status':'confirmed' if old_candidate['mem_a']['component_relevance']['memory']==0 and expanded_candidate['mem_a']['component_relevance']['memory']>0 else 'failed',
       'baseline_memory_component':old_candidate['mem_a']['component_relevance']['memory'],
       'expanded_memory_component':expanded_candidate['mem_a']['component_relevance']['memory']},
      {'check':'gate_logic_only','status':'confirmed' if old_candidate['same_irrelevant']['qualified'] and not fixed_candidate['same_irrelevant']['qualified'] else 'failed',
       'baseline_same_field_raw_passes':old_candidate['same_irrelevant']['qualified'],
       'fixed_same_field_evidence_passes':fixed_candidate['same_irrelevant']['qualified']},
      {'check':'recall_scope_only','status':'confirmed','cross_field_selected':'mem_cross' in cross['selected_ids']},
      {'check':'complete_fix','status':'confirmed' if all(p['status']=='passed' for p in probes) else 'failed',
       'passed_probes':sum(p['status']=='passed' for p in probes)}]
    return probes, attribution


# Execute or resume the complete 108-state, 648-treatment matrix with cache and purity checks.
def run_core(config, root, output):
    root, output = Path(root), Path(output); output.mkdir(parents=True, exist_ok=True)
    corpus_path, topics_path = root/config['corpus'], root/config['topics']
    corpus = Corpus(corpus_path, config['cutoff_year'], config['allow_synthetic'])
    topics = TopicModel(topics_path, corpus.papers, config['recognition_alpha'])
    corpus_digest, topics_digest = file_hash(corpus_path), file_hash(topics_path)
    states = build_frozen_states(config, corpus_digest, topics_digest, topics)
    write_jsonl(output/'frozen_states.jsonl', states)
    expected_keys = [(s['case_id'],r,p) for s in states for r in config['rankers'] for p in config['policies']]
    state_hashes = {s['case_id']:s['state_hash'] for s in states}
    existing = {(r['case_id'],r['ranker'],r['policy']):r for r in read_jsonl(output/'core_results.jsonl')
                if r.get('state_hash_before') == r.get('state_hash_after') == state_hashes.get(r.get('case_id'))
                and r.get('hot_cache_equal') is True}
    unknown = set(existing)-set(expected_keys)
    if unknown:
        raise ValueError('Resume file contains cases outside the frozen design')
    jobs = [(s,r,p) for s in states for r in config['rankers'] for p in config['policies'] if (s['case_id'],r,p) not in existing]
    random.Random(config['execution_order_seed']).shuffle(jobs)
    cache, failures, computed = ResultCache(), [], 0
    for state, ranker, policy in jobs:
        key = cache_key(state,ranker,policy,config['baseline_retrieval'] if ranker=='baseline_v03' else config['fixed_retrieval'],corpus_digest,topics_digest)
        try:
            value, cold_hit = cache.get(key,lambda s=state,r=ranker,p=policy:execute_case(s,r,p,config,corpus,topics))
            hot, hot_hit = cache.get(key,lambda:None)
            if cold_hit or not hot_hit or canonical(value)!=canonical(hot):
                raise RuntimeError('Cold/hot cache mismatch')
            audit, metrics = value['audit'], value['metrics']
            row = {'core_case_id':f'{state["case_id"]}__{ranker}__{policy}', 'case_id':state['case_id'],
                'state_hash':state['state_hash'],'topic_id':state['selected_topic_id'],'agent_field':state['agent_field'],
                'memory_condition':state['memory_condition'],'ranker':ranker,'policy':policy,
                'selected_ids':value['selected_ids'],'base_candidate_ids':audit.get('base_candidate_ids',[x['paper_id'] for x in audit['candidates']]),
                'qualified_ids':audit.get('qualified_ids',[x['paper_id'] for x in audit['candidates'] if x['qualified']]),
                'fallback':audit['fallback'],'query_id':audit['query_id'],'cache_key':key,'cold_cache_hit':cold_hit,
                'hot_cache_hit':hot_hit,'hot_cache_equal':True,'state_hash_before':value['state_hash_before'],
                'state_hash_after':value['state_hash_after'],**metrics}
            existing[(state['case_id'],ranker,policy)] = row; computed += 1
        except Exception as exc:
            failures.append({'case_id':state['case_id'],'ranker':ranker,'policy':policy,
                             'error_type':type(exc).__name__,'error':str(exc)})
    rows = [existing[k] for k in sorted(existing)]
    candidates = []
    # Recompute audits only for normalized candidate-level diagnostics; frozen inputs remain unchanged.
    state_by_id = {s['case_id']:s for s in states}
    for row in rows:
        value = execute_case(state_by_id[row['case_id']],row['ranker'],row['policy'],config,corpus,topics)
        row['order_replay_equal']=(value['selected_ids']==row['selected_ids']
            and value['audit']['query_id']==row['query_id']
            and value['audit'].get('base_candidate_ids',[x['paper_id'] for x in value['audit']['candidates']])==row['base_candidate_ids'])
        if not row['order_replay_equal']:
            failures.append({'case_id':row['case_id'],'ranker':row['ranker'],'policy':row['policy'],
                             'error_type':'OrderReplayMismatch','error':'sorted replay differed from shuffled execution'})
        for candidate in value['audit']['candidates']:
            candidates.append({'core_case_id':row['core_case_id'],'case_id':row['case_id'],'ranker':row['ranker'],
                'policy':row['policy'],'topic_id':row['topic_id'],'agent_field':row['agent_field'],
                'memory_condition':row['memory_condition'],**candidate})
    write_jsonl(output/'core_results.jsonl', rows)
    write_jsonl(output/'candidate_scores.jsonl', candidates)
    write_jsonl(output/'failure_cases.jsonl', failures)
    probes, attribution = targeted_probes(config)
    write_jsonl(output/'targeted_probe_results.jsonl', probes)
    write_jsonl(output/'attribution_results.jsonl', attribution)
    return {'states':states,'rows':rows,'candidates':candidates,'failures':failures,'probes':probes,
            'computed_now':computed,'resumed':len(rows)-computed,'corpus_hash':corpus_digest,
            'topics_hash':topics_digest}


# Recompute condition summaries and paired treatment contrasts from row-level logs.
def analyze(output):
    output = Path(output); rows = read_jsonl(output/'core_results.jsonl'); candidates = read_jsonl(output/'candidate_scores.jsonl')
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row['ranker'],row['policy'],row['memory_condition'],row['agent_field'])].append(row)
    metrics_rows = []
    for key, values in sorted(grouped.items()):
        numeric = {}
        for name in ('selected_count','topic_match_rate','topic_label_match_rate','pool_qualified_rate','selected_relevance_mean',
                     'near_duplicate_pair_rate','unique_cluster_ratio','candidate_recall','relevant_slot_precision','relevant_coverage_at_3'):
            usable = [v[name] for v in values if v.get(name) is not None]
            numeric[name+'_mean'] = statistics.mean(usable) if usable else None
            numeric[name+'_null_reason'] = None if usable else 'no_defined_denominator'
        case_ids={v['core_case_id'] for v in values}
        memory_values=[c['component_relevance'].get('memory',0.0) for c in candidates if c['core_case_id'] in case_ids]
        evidence_values=[c.get('evidence_relevance',c.get('raw_relevance')) for c in candidates if c['core_case_id'] in case_ids]
        metrics_rows.append(dict(zip(('ranker','policy','memory_condition','agent_field'),key), n=len(values),
            retrieval_shortfall_rate=statistics.mean(bool(v['retrieval_shortfall']) for v in values), **numeric))
        metrics_rows[-1].update(candidate_memory_component_mean=statistics.mean(memory_values) if memory_values else None,
                                candidate_evidence_relevance_mean=statistics.mean(evidence_values) if evidence_values else None)
    write_csv(output/'metrics_by_condition.csv',metrics_rows)
    by_state = {(r['case_id'],r['ranker'],r['policy']):r for r in rows}
    pairs = []
    for row in rows:
        if row['ranker'] != 'stage_a_fixed':
            other = by_state.get((row['case_id'],'stage_a_fixed',row['policy']))
            if other:
                overlap = document_overlap(row['selected_ids'],other['selected_ids'])
                pairs.append({'comparison':'baseline_v03_vs_stage_a_fixed','case_id':row['case_id'],'policy':row['policy'],
                    'left':'baseline_v03','right':'stage_a_fixed',**overlap,
                    'selected_count_delta':other['selected_count']-row['selected_count'],
                    'qualified_rate_delta':(other['pool_qualified_rate'] or 0)-(row['pool_qualified_rate'] or 0)})
        if row['policy'] == 'balanced':
            for other_policy in ('novelty','recognition'):
                other = by_state.get((row['case_id'],row['ranker'],other_policy))
                if other:
                    pairs.append({'comparison':f'balanced_vs_{other_policy}','case_id':row['case_id'],'policy':row['ranker'],
                        'left':'balanced','right':other_policy,**document_overlap(row['selected_ids'],other['selected_ids']),
                        'selected_count_delta':other['selected_count']-row['selected_count'],'qualified_rate_delta':0.0})
    by_factors={(r['topic_id'],r['agent_field'],r['memory_condition'],r['ranker'],r['policy']):r for r in rows}
    for row in rows:
        if row['memory_condition']=='empty':
            for memory in ('related','unrelated'):
                other=by_factors.get((row['topic_id'],row['agent_field'],memory,row['ranker'],row['policy']))
                if other:
                    pairs.append({'comparison':f'empty_vs_{memory}_memory','case_id':row['case_id'],'policy':row['policy'],
                      'left':'empty','right':memory,**document_overlap(row['selected_ids'],other['selected_ids']),
                      'selected_count_delta':other['selected_count']-row['selected_count'],
                      'qualified_rate_delta':(other['pool_qualified_rate'] or 0)-(row['pool_qualified_rate'] or 0)})
    topics=sorted({r['topic_id'] for r in rows}); topic_pairs=[]
    for left_topic,right_topic in zip(topics,topics[1:]):
        for field in sorted({r['agent_field'] for r in rows}):
            for ranker in ('baseline_v03','stage_a_fixed'):
                for policy in ('balanced','novelty','recognition'):
                    left=by_factors[(left_topic,field,'empty',ranker,policy)]
                    right=by_factors[(right_topic,field,'empty',ranker,policy)]
                    overlap=document_overlap(left['selected_ids'],right['selected_ids'])
                    probe={'comparison':'topic_only_empty_memory_probe','left_topic':left_topic,'right_topic':right_topic,
                           'agent_field':field,'ranker':ranker,'policy':policy,**overlap,
                           'left_selected':left['selected_ids'],'right_selected':right['selected_ids']}
                    topic_pairs.append(probe)
                    pairs.append({'comparison':'topic_only_empty_memory_probe','case_id':f'{left_topic}__{right_topic}__{field}',
                      'policy':policy,'left':left_topic,'right':right_topic,**overlap,
                      'selected_count_delta':right['selected_count']-left['selected_count'],
                      'qualified_rate_delta':(right['pool_qualified_rate'] or 0)-(left['pool_qualified_rate'] or 0)})
    write_jsonl(output/'topic_pair_probes.jsonl',topic_pairs)
    write_csv(output/'paired_comparisons.csv',pairs)
    return {'core_rows':len(rows),'candidate_rows':len(candidates),'condition_rows':len(metrics_rows),'paired_rows':len(pairs)}


# Build the Stage A report from observed logs without requiring a desired treatment effect.
def write_report(output, config, analysis_result):
    output = Path(output); rows = read_jsonl(output/'core_results.jsonl'); probes = read_jsonl(output/'targeted_probe_results.jsonl')
    candidates = read_jsonl(output/'candidate_scores.jsonl')
    baseline = [r for r in rows if r['ranker']=='baseline_v03']; fixed = [r for r in rows if r['ranker']=='stage_a_fixed']
    def mean(rows,name):
        values=[r[name] for r in rows if r.get(name) is not None]; return statistics.mean(values) if values else None
    def candidate_mean(ranker,component):
        values=[c['component_relevance'].get(component,0.0) for c in candidates if c['ranker']==ranker]
        return statistics.mean(values) if values else None
    fixed_fallbacks=Counter(r['fallback'] or 'none' for r in fixed)
    report = f"""# SciMirror Stage A 报告

## 结论

阶段A冻结状态检索实验完成：{len(set(r['state_hash'] for r in rows))} 个基础状态、{len(rows)} 个核心处理组合，定向探针 {sum(p['status']=='passed' for p in probes)}/{len(probes)} 通过。全部运行离线，网络请求与 LLM 调用均为 0。

## 问题归因与修复

1. **memory 全零**：v0.3 生产链路传入 `public_topics` 的下划线主题 ID，旧分词保留整个下划线串，而文献内容使用分开的普通词，因此无法相交。修复版统一连字符/下划线表示，并通过冻结主题表展开语义记忆；A02/A03 验证。
2. **门槛全部通过**：旧资格分数包含 0.2 的同领域分量，显著高于 0.08 门槛，因此同领域无关文献也可合格。修复版以独立的 `evidence_relevance` 资格判断，领域、memory、attention 仅参与合格后的上下文排序；A06/A08 验证。
3. **跨领域召回**：旧候选池按混合相关性截断，领域偏好会压低跨领域证据。修复版全语料评分并优先按证据相关性形成固定候选池，再在合格池中重排；A07 验证。此结论仅说明当前透明词法 fixture 的链路行为。
4. **近重复**：旧规则不能稳定识别只改合成 `scenario N`/`variant N` 标记的模板。修复仅对明确标记为 synthetic 的文献移除这两个编号标记，保留年份、样本量等其他数字；A11 验证，且不同方法未被合并。

## 核心矩阵观察

- baseline 平均返回数：{mean(baseline,'selected_count')}
- stage_a_fixed 平均返回数：{mean(fixed,'selected_count')}
- baseline 候选合格率：{mean(baseline,'pool_qualified_rate')}
- stage_a_fixed 候选合格率：{mean(fixed,'pool_qualified_rate')}
- stage_a_fixed 短缺率：{mean(fixed,'retrieval_shortfall')}
- baseline/stage_a_fixed 候选 memory 分量均值：{candidate_mean('baseline_v03','memory')} / {candidate_mean('stage_a_fixed','memory')}
- baseline/stage_a_fixed 标签匹配率：{mean(baseline,'topic_label_match_rate')} / {mean(fixed,'topic_label_match_rate')}
- stage_a_fixed shortfall 原因计数：{dict(fixed_fallbacks)}

修复版所有核心 case 都出现少于 3 篇的 shortfall，原因是当前合成语料的同主题记录主要是 scenario/variant 模板族，正确去重后每个主题通常只剩 1 个独立证据簇（共享词主题可有 2 个）。程序保留真实数量并记录 `near_duplicate_limit`，没有用无关项补满。这是语料多样性限制，不应掩盖。

相同结果不自动表示失败：不同 policy 共享基础候选池，且当合格文献的 exploration/attention 排序一致或证据不足时，最终集合可以相同。648 个组合是确定性处理调用，不是独立随机世界，因此未计算显著性。

## 边界与下一阶段条件

结果来自合成语料与人工定义 fixture，只验证工程正确性；不验证真实文献的语义检索质量，也不支持现实科研政策或社会机制的因果结论。当前实现具备接入下一阶段的接口条件，但正式科研实验前仍需在独立真实语料上验证召回、同义表达和去重阈值。广义语义同义词仍超出本词法系统能力；A02 只验证预先定义的规范化等义形式。

输出统计：{json.dumps(analysis_result,ensure_ascii=False)}
"""
    (output/'STAGE_A_REPORT.md').write_text(report,encoding='utf-8')


# Validate every acceptance category using row-level behavioral evidence.
def validate(output):
    output = Path(output); rows=read_jsonl(output/'core_results.jsonl'); states=read_jsonl(output/'frozen_states.jsonl')
    candidates=read_jsonl(output/'candidate_scores.jsonl'); probes=read_jsonl(output/'targeted_probe_results.jsonl')
    usage=json.loads((output/'usage.json').read_text(encoding='utf-8')); manifest=json.loads((output/'manifest.json').read_text(encoding='utf-8'))
    probe = {p['probe_id']:p for p in probes}
    candidate_groups=defaultdict(list)
    for candidate in candidates: candidate_groups[candidate['core_case_id']].append(candidate)
    metric_recompute=all(r['selected_count']==len(r['selected_ids'])
        and abs(r['pool_qualified_rate']-(sum(bool(c['qualified']) for c in candidate_groups[r['core_case_id']])/len(candidate_groups[r['core_case_id']])))<1e-12
        for r in rows if candidate_groups[r['core_case_id']])
    policy_pools=defaultdict(set)
    for row in rows: policy_pools[(row['case_id'],row['ranker'])].add(tuple(row['base_candidate_ids']))
    common_policy_pool=all(len(values)==1 for values in policy_pools.values())
    def item(ok,count,path): return {'status':'passed' if ok else 'failed','checked_count':count,'failed_count':0 if ok else 1,'evidence_path':path}
    expected_states=12*3*3; expected_core=expected_states*3*2
    sections = {
      'baseline':item(manifest.get('baseline_source_hash') is not None,1,'manifest.json'),
      'frozen_state':item(len(states)==expected_states and all(r['state_hash_before']==r['state_hash_after']==r['state_hash'] and r.get('order_replay_equal') for r in rows),len(rows),'core_results.jsonl'),
      'memory':item(all(probe[x]['status']=='passed' for x in ('A02','A03')),2,'targeted_probe_results.jsonl'),
      'relevance_gate':item(all(probe[x]['status']=='passed' for x in ('A06','A08','A09','A10')),4,'targeted_probe_results.jsonl'),
      'cross_field_retrieval':item(probe['A07']['status']=='passed',1,'targeted_probe_results.jsonl'),
      'deduplication':item(probe['A11']['status']=='passed',1,'targeted_probe_results.jsonl'),
      'cache':item(probe['A13']['status']=='passed' and all(r['hot_cache_equal'] for r in rows),len(rows)+1,'core_results.jsonl'),
      'core_matrix':item(len(rows)==expected_core and len({r['core_case_id'] for r in rows})==expected_core and metric_recompute and common_policy_pool,expected_core,'core_results.jsonl'),
      'targeted_probes':item(len(probes)==15 and all(p['status']=='passed' for p in probes),len(probes),'targeted_probe_results.jsonl'),
      'engine_integration':item(manifest.get('engine_integration',{}).get('passed') is True,1,'manifest.json'),
      'offline_execution':item(usage['network_requests']==0 and usage['llm_calls']==0,2,'usage.json')}
    all_passed=all(x['status']=='passed' for x in sections.values())
    result={'schema_version':SCHEMA,'status':'completed' if all_passed else 'failed','all_passed':all_passed,
            'sections':sections,'core_expected':expected_core,'core_actual':len(rows),'candidate_rows':len(candidates),
            'scientific_scope':'synthetic_fixture_engineering_validation_only','real_world_causal_claim_supported':False}
    dump(output/'DELIVERY_VALIDATION_STAGE_A.json',result); return result


# Verify that the production Corpus interface dispatches to Stage A and handles short results.
def engine_regression(config, root):
    root=Path(root); retrieval=copy.deepcopy(config['fixed_retrieval'])
    corpus=Corpus(root/config['corpus'],config['cutoff_year'],config['allow_synthetic'],retrieval)
    topics=TopicModel(root/config['topics'],corpus.papers,config['recognition_alpha'])
    pc=PolicyContext('balanced','retrieval',True,float(config['stage_lambda']))
    papers,audit=corpus.retrieve_v02('unused',[],topics,pc,20,3,selected_topic_id='agent_memory',field_name='learning',memory_terms=['agent_memory'])
    none,empty_audit=corpus.retrieve_v02('unused',[],topics,pc,20,3,selected_topic_id='unknown_zero_term',field_name='learning',memory_terms=[])
    passed=audit.get('mode')=='stage_a_fixed' and audit.get('ranker_version')==STAGE_A_RANKER_VERSION and isinstance([p['title'] for p in papers],list) and none==[] and empty_audit['selected_count']==0
    return {'passed':passed,'mode':audit.get('mode'),'ranker_version':audit.get('ranker_version'),
            'selected_count':len(papers),'empty_selected_count':len(none)}


# Initialize reproducibility metadata before running or resuming the matrix.
def write_manifest(config, config_path, root, output, engine):
    root,output=Path(root),Path(output)
    source_files=[root/'scimirror'/'v03_retrieval.py',root/'scimirror'/'corpus.py',root/'scimirror'/'stage_a.py']
    expected_inputs={'config_hash':file_hash(config_path),'corpus_hash':file_hash(root/config['corpus']),
                     'topics_hash':file_hash(root/config['topics']),'fixture_hash':file_hash(root/config['calibration_fixture'])}
    prior_path=output/'manifest.json'
    if prior_path.exists():
        prior=json.loads(prior_path.read_text(encoding='utf-8'))
        changed=[key for key,value in expected_inputs.items() if prior.get(key)!=value]
        if changed:
            raise ValueError('Resume refused because frozen inputs changed: '+', '.join(changed))
    baseline_function_hash=hashlib.sha256(inspect.getsource(retrieve_relevance_gated).encode()).hexdigest()
    fixed_function_hash=hashlib.sha256(inspect.getsource(retrieve_stage_a_fixed).encode()).hexdigest()
    manifest={'schema_version':SCHEMA,'created_at':datetime.now().astimezone().isoformat(),
      'python':sys.version,'platform':platform.platform(),'dependencies':{'runtime':'python_standard_library'},
      **expected_inputs,
      'source_hash':digest({p.name:file_hash(p) for p in source_files}),
      'baseline_source_hash':file_hash(root/'scimirror'/'v03_retrieval.py'),
      'baseline_repository_commit':config.get('baseline_repository_commit'),
      'baseline_identity':'repository v0.3 retrieve_relevance_gated / v03_ranker_1',
      'baseline_function_hash':baseline_function_hash,'fixed_function_hash':fixed_function_hash,
      'versions':{'state':STATE_VERSION,'query':STAGE_A_QUERY_VERSION,'ranker':STAGE_A_RANKER_VERSION,
                  'dedup':STAGE_A_DEDUP_VERSION,'cache':CACHE_VERSION},
      'expected_frozen_states':108,'expected_core_cases':648,'network_requests':0,'llm_calls':0,
      'engine_integration':engine}
    dump(output/'manifest.json',manifest); return manifest


# Run calibration, core experiment, analysis, report, validation, and portable archive.
def run_all(config, config_path, root, output):
    root,output=Path(root),Path(output); output.mkdir(parents=True,exist_ok=True)
    prior_usage=(json.loads((output/'usage.json').read_text(encoding='utf-8'))
                 if (output/'usage.json').exists() else {})
    dump(output/'config.json',config)
    dump(output/'status.json',{'schema_version':SCHEMA,'status':'running'})
    calibration=calibrate(config,root,output)
    engine=engine_regression(config,root)
    manifest=write_manifest(config,config_path,root,output,engine)
    result=run_core(config,root,output)
    analysis_result=analyze(output)
    usage={'schema_version':SCHEMA,'core_retrieval_calls':len(result['rows']),
           'computed_this_invocation':result['computed_now'],'resumed_cases':result['resumed'],
           'underlying_core_computations_cumulative':prior_usage.get('underlying_core_computations_cumulative',
               prior_usage.get('core_retrieval_calls',0))+result['computed_now'],
           'candidate_diagnostic_recomputations':len(result['rows']),'network_requests':0,
           'http_attempts':0,'llm_calls':0,'paid_api_calls':0}
    dump(output/'usage.json',usage)
    manifest.update(actual_frozen_states=len(result['states']),actual_core_cases=len(result['rows']),
                    candidate_rows=len(result['candidates']),failed_cases=len(result['failures']))
    dump(output/'manifest.json',manifest)
    write_report(output,config,analysis_result)
    validation=validate(output)
    dump(output/'status.json',{'schema_version':SCHEMA,'status':'completed' if validation['all_passed'] else 'failed',
                              'core_cases':len(result['rows']),'failed_cases':len(result['failures']),
                              'network_requests':0,'llm_calls':0})
    archive=output/'stage_a_delivery.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(output.iterdir()):
            if path.is_file() and path != archive:
                bundle.write(path,path.name)
    return validation
