"""Versioned query construction, relevance gating and retrieval diagnostics."""
import math
import re
from collections import Counter

from .common import digest
from .corpus import similarity, tokens


QUERY_BUILDER_VERSION = 'v03_weighted_topic_field_memory_1'
DEDUP_VERSION = 'v03_exact_id_doi_text_plus_greedy_1'
STAGE_A_QUERY_VERSION = 'stage_a_normalized_topic_memory_1'
STAGE_A_RANKER_VERSION = 'stage_a_evidence_gate_context_rerank_1'
STAGE_A_DEDUP_VERSION = 'stage_a_exact_fixture_family_plus_greedy_1'
SEMANTIC_QUERY_VERSION = 'retrieval_topic_profiles_v1'
SEMANTIC_RANKER_VERSION = 'stage_a_closure_1'
REPAIRED_QUERY_VERSION = 'retrieval_topic_profiles_repair_v1'
REPAIRED_RANKER_VERSION = 'stage_a_repaired_v1'


# 将文本规范化为用于完全重复检测的稳定形式。
def normalize_text(text):
    return ' '.join(re.findall(r'[a-z0-9]+', text.lower()))


# Normalize identifiers and prose to the same lexical representation without changing legacy corpus metrics.
def comparable_tokens(text):
    text = re.sub(r'[_\-]+', ' ', str(text).lower())
    return set(re.findall(r'[a-z0-9]+', text))


# Expand semantic-memory topic identifiers through the frozen topic dictionary before lexical comparison.
def semantic_memory_tokens(topic_model, memory_terms):
    expanded = []
    for value in memory_terms:
        value = value.get('text', value.get('topic_id', '')) if isinstance(value, dict) else str(value)
        expanded.append(value)
        topic_key = re.sub(r'[_\-\s]+', '_', value.strip().lower())
        if topic_key in topic_model.topics:
            topic = topic_model.topics[topic_key]
            expanded.extend([topic['description'], *topic['keywords']])
    return comparable_tokens(' '.join(expanded))


# 构建保留topic_id并展开描述、关键词、领域和记忆的查询对象。
def build_query(topic_model, topic_id, field_name, memory_terms, weights):
    if topic_id not in topic_model.topics:
        topic_words = set(tokens(topic_id.replace('_', ' ')))
        unknown = True
    else:
        topic = topic_model.topics[topic_id]
        topic_words = tokens(topic_id.replace('_', ' ')+' '+topic['description']+' '+' '.join(topic['keywords']))
        unknown = False
    components = {'topic': sorted(topic_words), 'field': sorted(tokens(field_name)),
                  'memory': sorted(tokens(' '.join(memory_terms)))}
    normalized = ' '.join(word for key in ('topic', 'field', 'memory') for word in components[key])
    return {'topic_id': topic_id, 'unknown_topic': unknown, 'components': components,
            'weights': {key: float(weights[key]) for key in ('topic', 'field', 'memory')},
            'normalized_query': normalized, 'query_builder_version': QUERY_BUILDER_VERSION}


# 计算论文文本对查询各组成部分的召回式词法相关性。
def component_relevance(query, paper):
    paper_words = tokens(paper['field']+' '+paper['title']+' '+paper['abstract'])
    scores = {}
    for name, words in query['components'].items():
        word_set = set(words)
        scores[name] = len(word_set & paper_words)/len(word_set) if word_set else 0.0
    raw = sum(query['weights'][name]*scores[name] for name in scores)
    return raw, scores


# 依据DOI、规范化全文和冻结词法阈值给论文分配近重复簇。
def duplicate_clusters(items, threshold, fixture_aware=False):
    clusters = []
    assignments = {}
    exact = {}
    for item in sorted(items, key=lambda value: value['paper']['id']):
        paper = item['paper']
        normalized = normalize_text(paper['title']+' '+paper['abstract'])
        if fixture_aware and paper.get('synthetic'):
            normalized = re.sub(r'\bscenario\s+\d+\b|\bvariant\s+\d+\b', '', normalized)
            normalized = ' '.join(normalized.split())
        exact_key = ('doi', paper['doi'].lower()) if paper.get('doi') else ('text', normalized)
        if exact_key in exact:
            assignments[paper['id']] = exact[exact_key]
            continue
        text = paper['title']+' '+paper['abstract']
        cluster_id = None
        for index, representative in enumerate(clusters):
            if similarity(text, representative['title']+' '+representative['abstract']) >= threshold:
                cluster_id = f'cluster_{index:04d}'
                break
        if cluster_id is None:
            cluster_id = f'cluster_{len(clusters):04d}'
            clusters.append(paper)
        assignments[paper['id']] = cluster_id
        exact[exact_key] = cluster_id
    return assignments


# Build the Stage A query with separate evidence, field, and semantic-memory representations.
def build_stage_a_query(topic_model, topic_id, field_name, memory_terms, weights):
    raw_memory = [value.get('text', value.get('topic_id', '')) if isinstance(value, dict) else str(value)
                  for value in memory_terms]
    if topic_id in topic_model.topics:
        topic = topic_model.topics[topic_id]
        evidence_words = comparable_tokens(' '.join(topic['keywords']))
        display_words = comparable_tokens(topic_id+' '+topic['description']+' '+' '.join(topic['keywords']))
        unknown = False
    else:
        evidence_words = comparable_tokens(topic_id)
        display_words = set(evidence_words)
        unknown = True
    components = {'topic': sorted(evidence_words), 'topic_display': sorted(display_words),
                  'field': sorted(comparable_tokens(field_name)),
                  'memory': sorted(semantic_memory_tokens(topic_model, memory_terms))}
    normalized = ' '.join(word for key in ('topic_display', 'field', 'memory') for word in components[key])
    return {'topic_id': topic_id, 'unknown_topic': unknown, 'components': components,
            'raw_components': {'topic': topic_id, 'field': field_name, 'memory': raw_memory},
            'weights': {key: float(weights[key]) for key in ('topic', 'field', 'memory')},
            'normalized_query': normalized, 'query_builder_version': STAGE_A_QUERY_VERSION}


# Score evidence relevance independently from field and memory context preferences.
def stage_a_component_relevance(query, paper):
    paper_words = comparable_tokens(paper['field']+' '+paper['title']+' '+paper['abstract'])
    scores = {}
    for name in ('topic', 'field', 'memory'):
        words = set(query['components'][name])
        scores[name] = len(words & paper_words)/len(words) if words else 0.0
    ranking_relevance = sum(query['weights'][name]*scores[name] for name in scores)
    return scores['topic'], ranking_relevance, scores


# Normalize punctuation, hyphens, case, and simple English plurals for guarded matching.
def semantic_words(text):
    words=re.findall(r'[a-z0-9]+',str(text).lower().replace('-',' '))
    return [word[:-1] if len(word)>3 and word.endswith('s') and not word.endswith('ss') else word for word in words]


# Load and validate retrieval-only profiles without consulting corpus labels or qrels.
def load_retrieval_profiles(path):
    import json
    from pathlib import Path
    raw=json.loads(Path(path).read_text(encoding='utf-8'))
    if raw.get('version') not in (SEMANTIC_QUERY_VERSION,REPAIRED_QUERY_VERSION) or len(raw.get('topics',{}))!=12:
        raise ValueError('Invalid retrieval topic profiles')
    required={'definition','positive_phrases','concept_groups','generic_terms','scope_note'}
    if any(not required<=set(profile) for profile in raw['topics'].values()):
        raise ValueError('Incomplete retrieval topic profile')
    forbidden=('supp_','family_','gold_','variant_','challenge_')
    if any(any(token in str(profile).lower() for token in forbidden) for profile in raw['topics'].values()):
        raise ValueError('Evaluation identifiers leaked into retrieval profiles')
    return raw


# Normalize only explicitly frozen English inflections instead of truncating every trailing s.
def repaired_words(text):
    mapping={'agents':'agent','ideas':'idea','proposals':'proposal','papers':'paper','tools':'tool',
      'strategies':'strategy','policies':'policy','hypotheses':'hypothesis','researchers':'researcher',
      'teams':'team','graphs':'graph','signals':'signal','systems':'system','actions':'action'}
    return [mapping.get(word,word) for word in re.findall(r'[a-z0-9]+',str(text).lower().replace('-',' '))]


# Test a normalized phrase as a contiguous token sequence with explicit token boundaries.
def token_phrase_present(words,phrase):
    target=repaired_words(phrase)
    return bool(target) and any(words[index:index+len(target)]==target for index in range(len(words)-len(target)+1))


# Score the repaired profile using bounded phrase and concept co-occurrence in title/abstract only.
def repaired_evidence_score(profile,paper,window_size):
    title=repaired_words(paper['title']); abstract=repaired_words(paper['abstract'])
    title_hits=[phrase for phrase in profile['positive_phrases'] if token_phrase_present(title,phrase)]
    abstract_hits=[phrase for phrase in profile['positive_phrases'] if token_phrase_present(abstract,phrase)]
    sentences=[repaired_words(value) for value in re.split(r'[.!?;]+',paper['title']+'. '+paper['abstract']) if value.strip()]
    groups={name:list(values) for name,values in profile['concept_groups'].items()}
    group_in=lambda words,values:any(token_phrase_present(words,value) for value in values)
    matched=[name for name,values in groups.items() if any(group_in(sentence,values) for sentence in sentences)]
    group_pass=bool(groups) and any(all(group_in(sentence,values) for values in groups.values()) for sentence in sentences)
    if not group_pass and profile.get('cooccurrence_scope')!='sentence_only':
        stream=repaired_words(paper['title']+' '+paper['abstract'])
        group_pass=any(all(group_in(stream[index:index+window_size],values) for values in groups.values()) for index in range(len(stream)))
        if group_pass: matched=list(groups)
    generic_hit=any(token_phrase_present(title+abstract,value) for value in profile['generic_terms'])
    score=max(1.0 if title_hits else 0.0,.9 if abstract_hits else 0.0,.8 if group_pass else 0.0)
    return score,{'title_match_score':1.0 if title_hits else 0.0,'abstract_match_score':.9 if abstract_hits else 0.0,
      'matched_phrases':title_hits+abstract_hits,'matched_concept_groups':matched,'generic_only_match':generic_hit and score==0}


# Resolve semantic-memory terms through frozen retrieval profiles and report unknown literals.
def repaired_memory_tokens(profiles,memory_terms):
    words=set(); statuses=[]
    for raw in memory_terms:
        value=raw.get('topic_id',raw.get('text','')) if isinstance(raw,dict) else str(raw)
        key=re.sub(r'[_\-\s]+','_',value.strip().lower())
        if key in profiles['topics']:
            profile=profiles['topics'][key]
            material=' '.join([profile['definition'],*profile['positive_phrases'],*(term for values in profile['concept_groups'].values() for term in values)])
            words.update(repaired_words(material)); statuses.append({'input':value,'status':'resolved_topic','topic_id':key})
        else:
            words.update(repaired_words(value)); statuses.append({'input':value,'status':'unknown_literal','topic_id':None})
    return words,statuses


# Retrieve with a memory-independent evidence pool and repaired field/memory/policy reranking.
def retrieve_stage_a_repaired(papers,topic_model,policy_context,config,topic_id,field_name,memory_terms,read_texts):
    profiles=config.get('_profiles')
    if not profiles or profiles.get('version')!=REPAIRED_QUERY_VERSION or topic_id not in profiles['topics']:
        raise ValueError('Repaired retrieval profiles were not loaded')
    beta_field=float(config['beta_field']); requested_beta_memory=float(config['beta_memory'])
    if beta_field<0 or requested_beta_memory<0 or beta_field+requested_beta_memory>=1: raise ValueError('Invalid repaired weights')
    memory_enabled=bool(config.get('memory_enabled',True)); memory_words,memory_status=repaired_memory_tokens(profiles,memory_terms)
    beta_memory=requested_beta_memory if memory_enabled and memory_words else 0.0
    evidence_weight=1-beta_field-beta_memory; profile=profiles['topics'][topic_id]; scored=[]
    for paper in papers.values():
        evidence,detail=repaired_evidence_score(profile,paper,int(config['match_window_tokens']))
        text_words=set(repaired_words(paper['title']+' '+paper['abstract']))
        memory_score=len(memory_words&text_words)/len(memory_words) if memory_words and memory_enabled else 0.0
        field_score=1.0 if field_name and field_name!='unknown' and paper['field']==field_name else 0.0
        exploration=(sum(1-similarity(paper['title']+' '+paper['abstract'],old) for old in read_texts)/len(read_texts) if read_texts else .5)
        labels=topic_model.paper_topics[paper['id']]; attention=sum(topic_model.attention[t] for t in labels)/len(labels) if labels else 0.0
        scored.append({'paper':paper,'evidence_score':evidence,'field_score':field_score,'memory_score':memory_score,
          'exploration_score':exploration,'attention_score':attention,'topic_ids':labels,**detail})
    qualified=[row for row in scored if row['evidence_score']>=float(config['minimum_relevance'])]
    base=sorted(qualified,key=lambda row:(-row['evidence_score'],row['paper']['id']))[:int(config['candidate_pool_size'])]
    clusters=duplicate_clusters(base,float(config['near_duplicate_threshold']),fixture_aware=True); wn,wr=policy_context.weights
    alpha=float(config['alpha'])
    for row in base:
        row['cluster_id']=clusters[row['paper']['id']]
        row['retrieval_score']=evidence_weight*row['evidence_score']+beta_field*row['field_score']+beta_memory*row['memory_score']
        row['policy_score']=wn*row['exploration_score']+wr*row['attention_score']
        row['final_score']=alpha*row['retrieval_score']+(1-alpha)*row['policy_score']
    ranked=sorted(base,key=lambda row:(-row['final_score'],-row['evidence_score'],row['paper']['id']))
    selected=[]; counts=Counter()
    for row in ranked:
        if counts[row['cluster_id']]>=int(config['max_per_near_duplicate_cluster']): continue
        selected.append(row); counts[row['cluster_id']]+=1
        if len(selected)==int(config['top_k']): break
    candidate_rank={row['paper']['id']:index+1 for index,row in enumerate(base)}; selected_rank={row['paper']['id']:index+1 for index,row in enumerate(selected)}
    diagnostics=[]
    for row in sorted(scored,key=lambda value:value['paper']['id']):
        pid=row['paper']['id']; passed=row in qualified
        diagnostics.append({'paper_id':pid,**{key:row[key] for key in ('title_match_score','abstract_match_score','matched_phrases','matched_concept_groups','generic_only_match','evidence_score','field_score','memory_score','exploration_score','attention_score')},
          'gate_passed':passed,'gate_reason':'passed_semantic_evidence' if passed else ('generic_only' if row['generic_only_match'] else 'no_semantic_evidence'),
          'retrieval_score':row.get('retrieval_score'),'policy_score':row.get('policy_score'),'final_score':row.get('final_score'),
          'candidate_rank':candidate_rank.get(pid),'selected_rank':selected_rank.get(pid),'pool_member':pid in candidate_rank,
          'effective_evidence_weight':evidence_weight,'effective_beta_field':beta_field,'effective_beta_memory':beta_memory})
    config_identity={key:value for key,value in config.items() if key!='_profiles'}
    query_id=digest({'corpus':digest(papers),'topic':topic_id,'field':field_name,'memory':memory_terms,'read_texts':read_texts,
      'policy':policy_context.audit(),'config':config_identity,'profile':digest(profile)})[:20]
    audit={'schema_version':'0.3','stage_a_schema_version':'stage_a_repair_1','mode':'stage_a_repaired_v1','ranker_version':REPAIRED_RANKER_VERSION,
      'query_id':query_id,'decision_id':query_id,'topic_id':topic_id,'query_profile_hash':digest(profile),'corpus_hash':digest(papers),
      'recognition_snapshot_hash':topic_model.audit['snapshot_hash'],'memory_enabled':memory_enabled,'memory_resolution':memory_status,
      'scored_ids':sorted(papers),'evidence_qualified_ids':[row['paper']['id'] for row in qualified],
      'qualified_ids':[row['paper']['id'] for row in qualified],'base_candidate_ids':[row['paper']['id'] for row in base],
      'ranked_ids':[row['paper']['id'] for row in ranked],'selected_ids':[row['paper']['id'] for row in selected],
      'selected_count':len(selected),'fallback':None if len(selected)==int(config['top_k']) else ('no_qualified_evidence' if not qualified else 'insufficient_distinct_evidence'),
      'diagnostics':diagnostics,'candidates':[row for row in diagnostics if row['pool_member']],'policy_context':policy_context.audit()}
    return [row['paper'] for row in selected],audit


# Score phrases and concept-group co-occurrence using only title and abstract text.
def semantic_evidence_score(profile,paper,window_size):
    title=' '.join(semantic_words(paper['title'])); abstract=' '.join(semantic_words(paper['abstract']))
    phrases=[' '.join(semantic_words(x)) for x in profile['positive_phrases']]
    title_hits=[p for p in phrases if p and p in title]; abstract_hits=[p for p in phrases if p and p in abstract]
    sentences=[semantic_words(x) for x in re.split(r'[.!?;]+',paper['title']+'. '+paper['abstract']) if x.strip()]
    normalized_groups={group:[semantic_words(x) for x in alternatives] for group,alternatives in profile['concept_groups'].items()}
    has_alt=lambda segment,alternatives:any(all(word in segment for word in alt) for alt in alternatives)
    matched_groups=[group for group,alternatives in normalized_groups.items() if any(has_alt(segment,alternatives) for segment in sentences)]
    group_pass=bool(normalized_groups) and any(all(has_alt(segment,alternatives) for alternatives in normalized_groups.values()) for segment in sentences)
    if not group_pass and len(profile['concept_groups'])>1 and profile.get('cooccurrence_scope')!='sentence_only':
        stream=semantic_words(paper['title']+' '+paper['abstract'])
        for start in range(len(stream)):
            segment=stream[start:start+window_size]
            window_present=[]
            for alternatives in normalized_groups.values():
                window_present.append(any(all(word in segment for word in alt) for alt in alternatives))
            if all(window_present): group_pass=True; matched_groups=list(normalized_groups); break
    generic={' '.join(semantic_words(x)) for x in profile['generic_terms']}
    generic_hit=any(term and term in title+' '+abstract for term in generic)
    score=max(1.0 if title_hits else 0.0,.9 if abstract_hits else 0.0,.8 if group_pass else 0.0)
    generic_only=generic_hit and score==0
    return score,{'title_match_score':1.0 if title_hits else 0.0,'abstract_match_score':.9 if abstract_hits else 0.0,
                  'matched_phrases':title_hits+abstract_hits,'matched_concept_groups':matched_groups,
                  'generic_only_match':generic_only}


# Retrieve with a corpus-wide content gate followed by a common pool and policy reranking.
def retrieve_stage_a_semantic_guarded(papers,topic_model,policy_context,config,topic_id,field_name,
                                      memory_terms,read_texts):
    profiles=config.get('_profiles')
    if not profiles or topic_id not in profiles['topics']:
        raise ValueError('Semantic retrieval profiles were not loaded')
    profile=profiles['topics'][topic_id]; scored=[]
    for paper in papers.values():
        evidence,detail=semantic_evidence_score(profile,paper,int(config['match_window_tokens']))
        exploration=(sum(1-similarity(paper['title']+' '+paper['abstract'],old) for old in read_texts)/len(read_texts) if read_texts else .5)
        topic_ids=topic_model.paper_topics[paper['id']]
        attention=sum(topic_model.attention[t] for t in topic_ids)/len(topic_ids) if topic_ids else 0.0
        context=(1.0 if paper['field']==field_name else 0.0)*config['field_context_weight']
        scored.append({'paper':paper,'evidence_relevance':evidence,'raw_relevance':evidence+context,
                       'context_score':context,'exploration':exploration,'attention':attention,
                       'topic_ids':topic_ids,**detail})
    qualified=[x for x in scored if x['evidence_relevance']>=config['minimum_relevance']]
    base=sorted(qualified,key=lambda x:(-x['evidence_relevance'],-x['context_score'],x['paper']['id']))[:config['candidate_pool_size']]
    clusters=duplicate_clusters(base,config['near_duplicate_threshold'],fixture_aware=True)
    wn,wr=policy_context.weights
    for item in base:
        item['normalized_relevance']=item['evidence_relevance']
        item['cluster_id']=clusters[item['paper']['id']]
        item['policy_score']=wn*item['exploration']+wr*item['attention']
        item['final_score']=config['relevance_weight']*item['normalized_relevance']+(1-config['relevance_weight'])*item['policy_score']
    ranked=sorted(base,key=lambda x:(-x['final_score'],-x['evidence_relevance'],x['paper']['id']))
    selected=[]; counts=Counter()
    for item in ranked:
        if counts[item['cluster_id']]>=config['max_per_near_duplicate_cluster']: continue
        selected.append(item); counts[item['cluster_id']]+=1
        if len(selected)==config['top_k']: break
    selected_rank={x['paper']['id']:i+1 for i,x in enumerate(selected)}
    candidate_rank={x['paper']['id']:i+1 for i,x in enumerate(base)}
    diagnostics=[]
    for item in sorted(scored,key=lambda x:x['paper']['id']):
        pid=item['paper']['id']; passed=item in qualified
        diagnostics.append({'paper_id':pid,'title_match_score':item['title_match_score'],'abstract_match_score':item['abstract_match_score'],
          'matched_phrases':item['matched_phrases'],'matched_concept_groups':item['matched_concept_groups'],
          'generic_only_match':item['generic_only_match'],'evidence_score':item['evidence_relevance'],'gate_passed':passed,
          'gate_reason':'passed_semantic_evidence' if passed else ('generic_only' if item['generic_only_match'] else 'no_semantic_evidence'),
          'context_score':item['context_score'],'exploration_score':item['exploration'],'attention_score':item['attention'],
          'policy_score':item.get('policy_score'),'final_score':item.get('final_score'),'candidate_rank':candidate_rank.get(pid),
          'selected_rank':selected_rank.get(pid),'topic_ids':item['topic_ids'],'cluster_id':item.get('cluster_id')})
    reason=None if len(selected)==config['top_k'] else ('no_qualified_evidence' if not qualified else 'insufficient_distinct_evidence')
    profile_hash=digest(profile); query_id=digest({'profile':profile_hash,'topic':topic_id,'corpus':digest(papers),'config':{k:v for k,v in config.items() if k!='_profiles'}})[:20]
    audit={'schema_version':'0.3','stage_a_schema_version':'stage_a_closure_1','mode':'stage_a_semantic_guarded',
      'ranker_version':SEMANTIC_RANKER_VERSION,'query_id':query_id,'topic_id':topic_id,'query_profile_hash':profile_hash,
      'corpus_hash':digest(papers),'dedup_version':STAGE_A_DEDUP_VERSION,'scored_ids':sorted(papers),
      'recalled_ids':sorted(papers),'evidence_qualified_ids':[x['paper']['id'] for x in qualified],
      'qualified_ids':[x['paper']['id'] for x in qualified],'base_candidate_ids':[x['paper']['id'] for x in base],
      'ranked_ids':[x['paper']['id'] for x in ranked],'ranked_qualified_ids':[x['paper']['id'] for x in ranked],
      'selected_ids':[x['paper']['id'] for x in selected],'selected_count':len(selected),'candidate_pool_size':len(base),
      'fallback':reason,'minimum_relevance':config['minimum_relevance'],'diagnostics':diagnostics,
      'candidates':[x for x in diagnostics if x['candidate_rank'] is not None],'policy_context':policy_context.audit()}
    return [x['paper'] for x in selected],audit


# Retrieve from a common corpus-wide pool, gate on evidence relevance, then apply context and policy reranking.
def retrieve_stage_a_fixed(papers, topic_model, policy_context, config, topic_id, field_name,
                           memory_terms, read_texts):
    query = build_stage_a_query(topic_model, topic_id, field_name, memory_terms, config['query_weights'])
    scored = []
    for paper in papers.values():
        evidence_relevance, ranking_relevance, components = stage_a_component_relevance(query, paper)
        exploration = (sum(1-similarity(paper['title']+' '+paper['abstract'], old) for old in read_texts)/len(read_texts)
                       if read_texts else .5)
        topic_ids = topic_model.paper_topics[paper['id']]
        attention = sum(topic_model.attention[t] for t in topic_ids)/len(topic_ids) if topic_ids else 0.0
        scored.append({'paper': paper, 'evidence_relevance': evidence_relevance,
                       'raw_relevance': ranking_relevance, 'component_relevance': components,
                       'exploration': exploration, 'attention': attention, 'topic_ids': topic_ids})
    recalled = sorted(scored, key=lambda item: item['paper']['id'])
    base = sorted(scored, key=lambda item: (-item['evidence_relevance'], -item['raw_relevance'],
                                            item['paper']['id']))[:config['candidate_pool_size']]
    qualified = [item for item in base if item['evidence_relevance'] >= config['minimum_relevance']]
    cluster_ids = duplicate_clusters(qualified, config['near_duplicate_threshold'], fixture_aware=True)
    peak = max((item['raw_relevance'] for item in qualified), default=0.0)
    wn, wr = policy_context.weights
    for item in qualified:
        item['normalized_relevance'] = item['raw_relevance']/peak if peak > 0 else 0.0
        item['cluster_id'] = cluster_ids[item['paper']['id']]
        item['policy_score'] = wn*item['exploration'] + wr*item['attention']
        item['final_score'] = (config['relevance_weight']*item['normalized_relevance'] +
                               (1-config['relevance_weight'])*item['policy_score'])
    ranked = sorted(qualified, key=lambda item: (-item['final_score'], -item['evidence_relevance'],
                                                  -item['raw_relevance'], item['paper']['id']))
    selected, cluster_counts = [], Counter()
    for item in ranked:
        if cluster_counts[item['cluster_id']] >= config['max_per_near_duplicate_cluster']:
            continue
        selected.append(item)
        cluster_counts[item['cluster_id']] += 1
        if len(selected) == config['top_k']:
            break
    reason = None
    if len(selected) < config['top_k']:
        if not qualified:
            reason = 'all_evidence_relevance_zero' if not any(x['evidence_relevance'] for x in base) else 'no_qualified_evidence'
        elif len(qualified) < config['top_k']:
            reason = 'insufficient_relevant_documents'
        else:
            reason = 'near_duplicate_limit'
    query_id = digest({'query': query, 'corpus': digest(sorted(papers)), 'config': config})[:20]
    audit_items = []
    for item in base:
        audit_items.append({'paper_id': item['paper']['id'], 'evidence_relevance': item['evidence_relevance'],
            'raw_relevance': item['raw_relevance'], 'normalized_relevance': item.get('normalized_relevance', 0.0),
            'component_relevance': item['component_relevance'], 'exploration': item['exploration'],
            'attention': item['attention'], 'topic_ids': item['topic_ids'], 'qualified': item in qualified,
            'cluster_id': item.get('cluster_id'), 'policy_score': item.get('policy_score'),
            'final_score': item.get('final_score')})
    audit = {'schema_version': '0.3', 'stage_a_schema_version': 'stage_a_1',
             'mode': 'stage_a_fixed', 'ranker_version': STAGE_A_RANKER_VERSION,
             'query_id': query_id, **query, 'corpus_hash': digest(papers), 'dedup_version': STAGE_A_DEDUP_VERSION,
             'recalled_ids': [item['paper']['id'] for item in recalled],
             'base_candidate_ids': [item['paper']['id'] for item in base],
             'qualified_ids': [item['paper']['id'] for item in qualified],
             'ranked_qualified_ids': [item['paper']['id'] for item in ranked],
             'candidate_pool_size': len(base), 'qualified_count': len(qualified),
             'selected_count': len(selected), 'selected_ids': [item['paper']['id'] for item in selected],
             'selected_topic_ids': [item['topic_ids'] for item in selected], 'fallback': reason,
             'minimum_relevance': config['minimum_relevance'], 'candidates': audit_items,
             'policy_context': policy_context.audit()}
    return [item['paper'] for item in selected], audit


# 在共同相关性合格池内按政策特征重排并允许证据不足短缺。
def retrieve_relevance_gated(papers, topic_model, policy_context, config, topic_id, field_name,
                             memory_terms, read_texts):
    query = build_query(topic_model, topic_id, field_name, memory_terms, config['query_weights'])
    scored = []
    for paper in papers.values():
        raw, components = component_relevance(query, paper)
        exploration = (sum(1-similarity(paper['title']+' '+paper['abstract'], old) for old in read_texts)/len(read_texts)
                       if read_texts else .5)
        topic_ids = topic_model.paper_topics[paper['id']]
        attention = sum(topic_model.attention[t] for t in topic_ids)/len(topic_ids) if topic_ids else 0.0
        scored.append({'paper': paper, 'raw_relevance': raw, 'component_relevance': components,
                       'exploration': exploration, 'attention': attention, 'topic_ids': topic_ids})
    peak = max((item['raw_relevance'] for item in scored), default=0.0)
    for item in scored:
        item['normalized_relevance'] = item['raw_relevance']/peak if peak > 0 else 0.0
    base = sorted(scored, key=lambda item: (-item['raw_relevance'], item['paper']['id']))[:config['candidate_pool_size']]
    qualified = [item for item in base if item['raw_relevance'] >= config['minimum_relevance']]
    cluster_ids = duplicate_clusters(qualified, config['near_duplicate_threshold'])
    wn, wr = policy_context.weights
    for item in qualified:
        item['cluster_id'] = cluster_ids[item['paper']['id']]
        item['policy_score'] = wn*item['exploration'] + wr*item['attention']
        item['final_score'] = (config['relevance_weight']*item['normalized_relevance'] +
                               (1-config['relevance_weight'])*item['policy_score'])
    ranked = sorted(qualified, key=lambda item: (-item['final_score'], -item['raw_relevance'], item['paper']['id']))
    selected, cluster_counts = [], Counter()
    for item in ranked:
        if cluster_counts[item['cluster_id']] >= config['max_per_near_duplicate_cluster']:
            continue
        selected.append(item)
        cluster_counts[item['cluster_id']] += 1
        if len(selected) == config['top_k']:
            break
    reason = None
    if len(selected) < config['top_k']:
        reason = 'all_relevance_zero' if peak == 0 else ('insufficient_relevant_documents' if len(qualified) < config['top_k']
                  else 'near_duplicate_limit')
    query_id = digest({'query': query, 'corpus': digest(sorted(papers)), 'config': config})[:20]
    audit_items = []
    for item in base:
        audit_items.append({'paper_id': item['paper']['id'],
            'evidence_relevance': item['component_relevance']['topic'], 'raw_relevance': item['raw_relevance'],
            'normalized_relevance': item['normalized_relevance'], 'component_relevance': item['component_relevance'],
            'exploration': item['exploration'], 'attention': item['attention'], 'topic_ids': item['topic_ids'],
            'qualified': item in qualified, 'cluster_id': item.get('cluster_id'),
            'policy_score': item.get('policy_score'), 'final_score': item.get('final_score')})
    audit = {'schema_version': '0.3', 'mode': 'relevance_gated', 'query_id': query_id, **query,
             'corpus_hash': digest(papers), 'dedup_version': DEDUP_VERSION,
             'recalled_ids': sorted(papers), 'base_candidate_ids': [item['paper']['id'] for item in base],
             'qualified_ids': [item['paper']['id'] for item in qualified],
             'ranked_qualified_ids': [item['paper']['id'] for item in ranked],
             'candidate_pool_size': len(base), 'qualified_count': len(qualified),
             'selected_count': len(selected), 'selected_ids': [item['paper']['id'] for item in selected],
             'selected_topic_ids': [item['topic_ids'] for item in selected], 'fallback': reason,
             'minimum_relevance': config['minimum_relevance'], 'candidates': audit_items,
             'policy_context': policy_context.audit()}
    return [item['paper'] for item in selected], audit


# 汇总一条检索审计的主题匹配、覆盖、相关性和近重复指标。
def audit_metrics(audit):
    selected_ids = set(audit['selected_ids'])
    selected = [item for item in audit['candidates'] if item['paper_id'] in selected_ids]
    labeled = [item for item in selected if item['topic_ids']]
    matches = [audit['topic_id'] in item['topic_ids'] for item in labeled]
    clusters = {item['cluster_id'] for item in selected if item.get('cluster_id')}
    pair_count = len(selected)*(len(selected)-1)//2
    duplicate_pairs = sum(a.get('cluster_id') == b.get('cluster_id') for index, a in enumerate(selected)
                          for b in selected[index+1:] if a.get('cluster_id'))
    pool_scores = sorted(item['raw_relevance'] for item in audit['candidates'])
    selected_scores = sorted(item['raw_relevance'] for item in selected)
    return {'query_id': audit['query_id'], 'topic_id': audit['topic_id'], 'selected_count': len(selected),
            'topic_match_rate': sum(matches)/len(matches) if matches else None,
            'unlabeled_selected': len(selected)-len(labeled),
            'topic_coverage': len({topic for item in selected for topic in item['topic_ids']}),
            'pool_relevance_min': pool_scores[0] if pool_scores else None,
            'pool_relevance_median': pool_scores[len(pool_scores)//2] if pool_scores else None,
            'pool_relevance_max': pool_scores[-1] if pool_scores else None,
            'pool_zero_rate': sum(score == 0 for score in pool_scores)/len(pool_scores) if pool_scores else None,
            'pool_qualified_rate': sum(item['qualified'] for item in audit['candidates'])/len(audit['candidates']) if audit['candidates'] else None,
            'selected_relevance_mean': sum(selected_scores)/len(selected_scores) if selected_scores else None,
            'near_duplicate_pair_rate': duplicate_pairs/pair_count if pair_count else None,
            'unique_cluster_ratio': len(clusters)/len(selected) if selected else None,
            'retrieval_shortfall': len(selected) < 3, 'fallback': audit['fallback']}


# 计算两个文献集合的Jaccard与交集对较小集合比例。
def document_overlap(left, right):
    left, right = set(left), set(right)
    if not left and not right:
        return {'jaccard': None, 'intersection_over_min': None, 'reason': 'both_empty'}
    union = left | right
    minimum = min(len(left), len(right))
    return {'jaccard': len(left & right)/len(union),
            'intersection_over_min': len(left & right)/minimum if minimum else None,
            'reason': None if minimum else 'one_empty'}
