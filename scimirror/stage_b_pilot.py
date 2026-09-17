"""Frozen real-document pilot workflow; never creates papers, queries, or ratings."""
import csv
import json
import math
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from .common import canonical, digest, dump
from .corpus import Corpus, similarity
from .policy import PolicyContext
from .retrieval_evaluation import EVALUATOR_VERSION, ranked_metrics
from .stage_a import file_hash, read_jsonl, write_jsonl
from .topics import TopicModel
from .v03_retrieval import load_retrieval_profiles, normalize_text, retrieve_stage_a_repaired, retrieve_stage_a_semantic_guarded
from .v03_review import write_csv

SCHEMA='stage_b_small_1'
VALID_SPLITS={'pilot','calibration','test'}


# Expand the v0.3 pilot config into explicit frozen defaults without changing its requested scale.
def normalize_config(config):
    value=dict(config); value.setdefault('topics_path','data/topics.json'); value.setdefault('annotation_scope','pooled_top10')
    value.setdefault('parameter_origin','stage_b_predeclared_not_stage_a_selected')
    value.setdefault('ranker_config',{'bm25_k1':1.2,'bm25_b':.75,'gate_pool_size':20,'top_k':value.get('top_k',10),'query_weight':.2,'near_duplicate_threshold':.97,'max_per_near_duplicate_cluster':1,'tie_break':'paper_id_ascending'})
    value.setdefault('closure_gate',{'mode':'stage_a_semantic_guarded','version':'stage_a_closure_1','profile_path':'data/retrieval_topic_profiles_v1.json','minimum_relevance':.8,'match_window_tokens':18,'field_context_weight':0,'relevance_weight':1})
    value.setdefault('repaired_gate',{'mode':'stage_a_repaired_v1','version':'stage_a_repaired_v1','profile_path':'data/retrieval_topic_profiles_repair_v1.json','minimum_relevance':.8,'match_window_tokens':18,'alpha':1,'beta_field':0,'beta_memory':0,'memory_enabled':False})
    return value


# Read a CSV/JSONL input while preserving empty values for validation.
def read_records(path):
    path=Path(path)
    if path.suffix.lower()=='.csv':
        with path.open(encoding='utf-8-sig',newline='') as stream: return list(csv.DictReader(stream))
    if path.suffix.lower()=='.jsonl': return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    raise ValueError('Input must be CSV or JSONL')


# Normalize DOI/arXiv identifiers and remove arXiv version suffixes.
def normalize_identifier(value,kind):
    value=str(value or '').strip().lower()
    if kind=='doi': value=re.sub(r'^(https?://(dx\.)?doi\.org/|doi:\s*)','',value)
    if kind=='arxiv': value=re.sub(r'^(https?://arxiv\.org/abs/|arxiv:\s*)','',value); value=re.sub(r'v\d+$','',value)
    return value


# Load the run-frozen configuration for every command after init.
def run_config(run_dir,fallback=None):
    path=Path(run_dir)/'config.json'
    if path.exists(): return normalize_config(json.loads(path.read_text(encoding='utf-8')))
    if fallback is None: raise ValueError('Run config missing; initialize the run first')
    return normalize_config(fallback)


# Persist one status field without inventing downstream completion.
def update_status(run_dir,**changes):
    path=Path(run_dir)/'status.json'; status=json.loads(path.read_text(encoding='utf-8')); status.update(changes); dump(path,status); return status


# Initialize immutable-input templates and retain any pre-existing run data.
def init_pilot(run_dir,config):
    run_dir=Path(run_dir); config=normalize_config(config); (run_dir/'private').mkdir(parents=True,exist_ok=True); (run_dir/'annotation_public').mkdir(parents=True,exist_ok=True)
    if (run_dir/'status.json').exists(): return json.loads((run_dir/'status.json').read_text(encoding='utf-8'))
    dump(run_dir/'config.json',config)
    templates={
      'real_papers_template.csv':['paper_id','title','abstract','year','source_url','synthetic','doi','arxiv_id','version','source_provider','retrieved_at','field'],
      'queries_template.csv':['query_id','topic_id','query_text','intent','author_type','split'],
      'family_map_template.csv':['paper_id','family_id','canonical','split','reviewer_id','rationale','updated_at'],
      'annotations_template.csv':['pool_version','blind_id','reviewer_id','relevance_0_1_2','unsure','rationale','rated_at','revision','change_reason'],
      'adjudication_template.csv':['pool_version','blind_id','adjudicator_id','final_grade','unresolved','rationale','adjudicated_at','revision']}
    for name,fields in templates.items(): write_csv(run_dir/name,[],fields=fields)
    dump(run_dir/'reviewers_template.json',{'reviewers':[{'reviewer_id':'','role':'reviewer'},{'reviewer_id':'','role':'reviewer'},{'reviewer_id':'','role':'adjudicator'}]})
    (run_dir/'QUERY_EXAMPLE_ONLY.csv').write_text('query_id,topic_id,query_text,intent,author_type,split\nexample_not_formal,agent_memory,How is episodic memory retrieved?,example,example,pilot\n',encoding='utf-8-sig')
    status={'schema_version':config.get('schema_version',SCHEMA),'test_only':bool(config.get('test_only',False)),'tooling_status':'initialized','data_status':'awaiting_real_corpus','query_status':'awaiting_human_queries',
      'family_status':'awaiting_family_map','reviewer_status':'awaiting_reviewers','freeze_status':'not_frozen','pool_status':'not_pooled',
      'annotation_status':'awaiting_human_labels','adjudication_status':'not_started','analysis_status':'not_started',
      'ready_for_scientific_claims':False,'network_requests':0,'llm_calls':0}; dump(run_dir/'status.json',status); return status


# Import, year-filter, and deterministically deduplicate user-supplied real metadata.
def import_corpus(input_path,run_dir,config=None):
    run_dir=Path(run_dir); config=run_config(run_dir,config); rows=read_records(input_path); cutoff=int(config['cutoff_year']); accepted=[]; excluded=[]; aliases=[]; seen_id={}; seen_text={}
    for index,row in enumerate(rows,1):
        required=('paper_id','title','abstract','year','source_url','synthetic'); missing=[key for key in required if key not in row or row.get(key) is None or not str(row.get(key)).strip()]
        is_false=row.get('synthetic') is False or str(row.get('synthetic','')).strip().lower() in ('false','0','no')
        if missing or not is_false: excluded.append({'row':index,'paper_id':row.get('paper_id'),'reason':'missing:'+','.join(missing) if missing else 'synthetic_must_be_false'}); continue
        try: year=int(row['year'])
        except (TypeError,ValueError): excluded.append({'row':index,'paper_id':row.get('paper_id'),'reason':'invalid_year'}); continue
        if year>=cutoff: excluded.append({'row':index,'paper_id':row['paper_id'],'reason':'year_not_before_cutoff'}); continue
        paper={'id':str(row['paper_id']).strip(),'paper_id':str(row['paper_id']).strip(),'title':str(row['title']).strip(),'abstract':str(row['abstract']).strip(),'year':year,
          'source_url':str(row['source_url']).strip(),'source_provider':str(row.get('source_provider') or 'unknown').strip(),'doi':normalize_identifier(row.get('doi'),'doi'),
          'arxiv_id':normalize_identifier(row.get('arxiv_id'),'arxiv'),'retrieved_at':str(row.get('retrieved_at') or '').strip(),'version':str(row.get('version') or '').strip(),
          'field':str(row.get('field') or 'unknown').strip(),'synthetic':False,'source_verification':'not_verified_offline'}
        identifiers=[('doi',paper['doi']),('arxiv',paper['arxiv_id'])]; text_key=normalize_text(paper['title']+' '+paper['abstract']); duplicate=None
        for key in identifiers:
            if key[1] and key in seen_id: duplicate=seen_id[key]
        if text_key in seen_text: duplicate=duplicate or seen_text[text_key]
        if duplicate:
            excluded.append({'row':index,'paper_id':paper['id'],'reason':'duplicate_of:'+duplicate}); aliases.append({'alias_paper_id':paper['id'],'canonical_paper_id':duplicate,'doi':paper['doi'],'arxiv_id':paper['arxiv_id'],'version':paper['version']}); continue
        if paper['id'] in {x['id'] for x in accepted}: raise ValueError('Duplicate paper_id')
        accepted.append(paper); seen_text[text_key]=paper['id']
        for key in identifiers:
            if key[1]: seen_id[key]=paper['id']
    near=[]
    for index,left in enumerate(accepted):
        for right in accepted[index+1:]:
            score=similarity(left['title']+' '+left['abstract'],right['title']+' '+right['abstract'])
            if .8<=score<1: near.append({'paper_id_a':left['id'],'paper_id_b':right['id'],'jaccard':score,'status':'awaiting_manual_review'})
    write_jsonl(run_dir/'corpus.jsonl',accepted); write_csv(run_dir/'corpus_exclusions.csv',excluded,fields=['row','paper_id','reason']); write_csv(run_dir/'version_aliases.csv',aliases,fields=['alias_paper_id','canonical_paper_id','doi','arxiv_id','version']); write_csv(run_dir/'near_duplicate_candidates.csv',near,fields=['paper_id_a','paper_id_b','jaccard','status'])
    minimum,maximum=config.get('target_documents',[30,50]); state='ready' if minimum<=len(accepted)<=maximum else 'insufficient_target_range'
    return update_status(run_dir,data_status=state,input_documents=len(rows),eligible_deduplicated_documents=len(accepted),excluded_documents=len(excluded),
      documents=len(accepted),excluded=len(excluded),version_aliases=len(aliases),near_duplicate_candidates=len(near))


# Import one-to-one document families and reject cross-split families or ambiguous canonicals.
def import_family_map(input_path,run_dir,config=None):
    run_dir=Path(run_dir); run_config(run_dir,config); rows=read_records(input_path); papers={row['id'] for row in read_jsonl(run_dir/'corpus.jsonl')}; by_paper={}; by_family=defaultdict(list)
    required={'paper_id','family_id','canonical','split','reviewer_id','rationale','updated_at'}
    for row in rows:
        if not required<=set(row) or any(not str(row.get(key,'')).strip() for key in required): raise ValueError('Incomplete family mapping')
        if row['paper_id'] not in papers or row['paper_id'] in by_paper: raise ValueError('Unknown or duplicate family paper')
        if row['split'] not in VALID_SPLITS: raise ValueError('Invalid family split')
        value=str(row['canonical']).lower() in ('1','true','yes'); normalized={**row,'canonical':value}; by_paper[row['paper_id']]=normalized; by_family[row['family_id']].append(normalized)
    if set(by_paper)!=papers: raise ValueError('Every eligible paper must have exactly one family')
    for family,values in by_family.items():
        if len({row['split'] for row in values})!=1: raise ValueError('Family crosses splits: '+family)
        if sum(row['canonical'] for row in values)!=1: raise ValueError('Family requires exactly one canonical: '+family)
    write_csv(run_dir/'private'/'family_map.csv',[by_paper[key] for key in sorted(by_paper)]); return update_status(run_dir,family_status='ready',families=len(by_family),canonical_documents=len(by_family))


# Import genuine free-text queries with a bounded pilot split vocabulary.
def import_queries(input_path,run_dir,config=None):
    run_dir=Path(run_dir); config=run_config(run_dir,config); rows=read_records(input_path); required={'query_id','topic_id','query_text','intent','author_type','split'}
    if any(not required<=set(row) or any(not str(row.get(key,'')).strip() for key in required) for row in rows): raise ValueError('Query columns missing')
    if any(row['topic_id'] not in config['topics'] or row['split'] not in VALID_SPLITS or row['query_id']=='example_not_formal' for row in rows): raise ValueError('Invalid formal query')
    if len({row['query_id'] for row in rows})!=len(rows): raise ValueError('Duplicate query_id')
    write_csv(run_dir/'queries.csv',rows); minimum,maximum=config.get('target_queries',[6,9]); state='ready' if minimum<=len(rows)<=maximum else 'insufficient_target_range'
    return update_status(run_dir,query_status=state,queries=len(rows))


# Register exactly two fixed rating roles and optional separate adjudicators.
def import_reviewers(input_path,run_dir,config=None):
    run_dir=Path(run_dir); run_config(run_dir,config); raw=json.loads(Path(input_path).read_text(encoding='utf-8')); rows=raw.get('reviewers',raw if isinstance(raw,list) else [])
    ids=[str(row.get('reviewer_id','')).strip() for row in rows]
    if not rows or any(not re.fullmatch(r'[A-Za-z0-9_-]+',value or '') for value in ids) or len(set(ids))!=len(ids): raise ValueError('Invalid reviewer registry')
    rating=[row for row in rows if row.get('role')=='reviewer']; adjudicators=[row for row in rows if row.get('role')=='adjudicator']
    if len(rating)!=2 or any(row.get('role') not in ('reviewer','adjudicator') for row in rows): raise ValueError('Exactly two fixed reviewers required')
    dump(run_dir/'private'/'reviewers.json',{'schema_version':SCHEMA,'reviewers':rows}); return update_status(run_dir,reviewer_status='ready',rating_reviewers=[row['reviewer_id'] for row in rating],adjudicators=[row['reviewer_id'] for row in adjudicators])


# Freeze all corpus/query/family/profile/source identities before candidate pooling.
def freeze(run_dir,root,config=None):
    run_dir=Path(run_dir); root=Path(root); config=run_config(run_dir,config); status=json.loads((run_dir/'status.json').read_text())
    required=(status['data_status']=='ready',status['query_status']=='ready',status['family_status']=='ready',status['reviewer_status']=='ready')
    if not all(required): raise ValueError('Corpus, queries, family map, and reviewers must meet pilot targets before freeze')
    inputs={'config.json':file_hash(run_dir/'config.json'),'corpus.jsonl':file_hash(run_dir/'corpus.jsonl'),'queries.csv':file_hash(run_dir/'queries.csv'),
      'private/family_map.csv':file_hash(run_dir/'private'/'family_map.csv'),'private/reviewers.json':file_hash(run_dir/'private'/'reviewers.json'),
      'closure_profile':file_hash(root/config['closure_gate']['profile_path']),'repaired_profile':file_hash(root/config['repaired_gate']['profile_path']),
      'stage_b_source':file_hash(root/'scimirror'/'stage_b_pilot.py'),'evaluator_source':file_hash(root/'scimirror'/'retrieval_evaluation.py')}
    frozen={'schema_version':SCHEMA,'evaluator_version':EVALUATOR_VERSION,'inputs':inputs,'pool_version':digest(inputs)[:20],'annotation_scope':config['annotation_scope'],'ranker_config':config['ranker_config'],'parameter_origin':config['parameter_origin']}
    dump(run_dir/'FROZEN_PILOT.json',frozen); return update_status(run_dir,freeze_status='frozen',pool_version=frozen['pool_version'])


# Compute BM25 scores with explicit zero-signal reporting.
def bm25_scores(query,papers,k1=1.2,b=.75):
    tokenize=lambda text:re.findall(r'[a-z0-9]+',str(text).lower()); docs={pid:tokenize(row['title']+' '+row['abstract']) for pid,row in papers.items()}; terms=tokenize(query)
    avg=sum(map(len,docs.values()))/max(1,len(docs)); df=Counter(term for term in set(terms) for words in docs.values() if term in words); result={}
    for pid,words in docs.items():
        counts=Counter(words); score=0.0
        for term in terms:
            freq=counts[term]
            if freq: score+=math.log(1+(len(docs)-df[term]+.5)/(df[term]+.5))*freq*(k1+1)/(freq+k1*(1-b+b*len(words)/max(avg,1)))
        result[pid]=score
    return result


# Verify frozen inputs before pooling or annotation import.
def verify_freeze(run_dir,root):
    run_dir=Path(run_dir); root=Path(root); frozen=json.loads((run_dir/'FROZEN_PILOT.json').read_text()); config=run_config(run_dir)
    current={'config.json':file_hash(run_dir/'config.json'),'corpus.jsonl':file_hash(run_dir/'corpus.jsonl'),'queries.csv':file_hash(run_dir/'queries.csv'),
      'private/family_map.csv':file_hash(run_dir/'private'/'family_map.csv'),'private/reviewers.json':file_hash(run_dir/'private'/'reviewers.json'),
      'closure_profile':file_hash(root/config['closure_gate']['profile_path']),'repaired_profile':file_hash(root/config['repaired_gate']['profile_path']),
      'stage_b_source':file_hash(root/'scimirror'/'stage_b_pilot.py'),'evaluator_source':file_hash(root/'scimirror'/'retrieval_evaluation.py')}
    if current!=frozen['inputs']: raise ValueError('Frozen pilot input changed; create a new run/pool version')
    return frozen


# Run three frozen methods and retain an explicit row for every query/ranker, including empty results.
def pool(run_dir,config,root):
    run_dir=Path(run_dir); root=Path(root); config=run_config(run_dir,config); frozen=verify_freeze(run_dir,root)
    if (run_dir/'private'/'ratings_history.jsonl').exists(): raise ValueError('Cannot overwrite pool after annotation has started')
    corpus=Corpus(run_dir/'corpus.jsonl',config['cutoff_year'],False); family_rows=read_records(run_dir/'private'/'family_map.csv'); canonical_ids={row['paper_id'] for row in family_rows if str(row['canonical']).lower() in ('1','true','yes')}; corpus.papers={pid:paper for pid,paper in corpus.papers.items() if pid in canonical_ids}
    if not corpus.papers: raise ValueError('No frozen canonical documents')
    topics=TopicModel(root/config['topics_path'],corpus.papers,1.0); queries=read_records(run_dir/'queries.csv'); runs=[]; mappings=[]
    closure=load_retrieval_profiles(root/config['closure_gate']['profile_path']); repaired=load_retrieval_profiles(root/config['repaired_gate']['profile_path']); rcfg=config['ranker_config']
    for query in queries:
        lexical=bm25_scores(query['query_text'],corpus.papers,float(rcfg['bm25_k1']),float(rcfg['bm25_b'])); zero_signal=max(lexical.values(),default=0)==0
        for ranker in config['rankers']:
            gate_rank={}; evidence={}
            if ranker=='bm25_lexical': eligible=sorted(corpus.papers)
            else:
                gate=config['closure_gate'] if ranker=='stage_a_semantic_guarded' else config['repaired_gate']; retrieval={**gate,'_profiles':closure if ranker=='stage_a_semantic_guarded' else repaired,'candidate_pool_size':rcfg['gate_pool_size'],'top_k':rcfg['gate_pool_size'],'max_per_near_duplicate_cluster':rcfg['max_per_near_duplicate_cluster'],'near_duplicate_threshold':rcfg['near_duplicate_threshold']}
                if ranker=='stage_a_semantic_guarded': _,audit=retrieve_stage_a_semantic_guarded(corpus.papers,topics,PolicyContext('balanced','retrieval',False,0),retrieval,query['topic_id'],'unknown',[],[])
                else: _,audit=retrieve_stage_a_repaired(corpus.papers,topics,PolicyContext('balanced','retrieval',False,0),retrieval,query['topic_id'],'unknown',[],[])
                eligible=audit['selected_ids']; gate_rank={pid:index+1 for index,pid in enumerate(eligible)}; evidence={row['paper_id']:row['evidence_score'] for row in audit['diagnostics']}
            peak=max((lexical[pid] for pid in eligible),default=0); score={pid:(lexical[pid] if ranker=='bm25_lexical' else evidence[pid]+float(rcfg['query_weight'])*(lexical[pid]/peak if peak else 0)) for pid in eligible}
            ranked=sorted(eligible,key=lambda pid:(-score[pid],pid)); returned=ranked[:int(rcfg['top_k'])]
            runs.append({'pool_version':frozen['pool_version'],'query_id':query['query_id'],'ranker':ranker,'eligible_count':len(eligible),'returned_count':len(returned),'empty':not returned,'zero_signal':zero_signal,'ranked_ids':returned})
            for rank,pid in enumerate(returned,1): mappings.append({'pool_version':frozen['pool_version'],'query_id':query['query_id'],'paper_id':pid,'ranker':ranker,'rank':rank,'pre_query_rank':gate_rank.get(pid),'score':score[pid],'zero_signal':zero_signal})
    write_jsonl(run_dir/'private'/'ranker_runs.jsonl',runs); write_csv(run_dir/'private'/'pool_mapping.csv',mappings)
    if config['annotation_scope']=='full_corpus': pairs=[(q,pid) for q in queries for pid in sorted(corpus.papers)]
    else:
        query_by_id={row['query_id']:row for row in queries}; pair_keys=sorted({(row['query_id'],row['paper_id']) for row in mappings}); pairs=[(query_by_id[qid],pid) for qid,pid in pair_keys]
    public=[]; private=[]
    for query,pid in pairs:
        blind=digest([frozen['pool_version'],query['query_id'],pid])[:20]; paper=corpus.papers[pid]
        public.append({'pool_version':frozen['pool_version'],'blind_id':blind,'query_id':query['query_id'],'topic_id':query['topic_id'],'query_text':query['query_text'],'intent':query['intent'],'title':paper['title'],'abstract':paper['abstract'],'year':paper['year'],'source_url':paper['source_url'],'source_provider':paper['source_provider']})
        private.append({'pool_version':frozen['pool_version'],'blind_id':blind,'query_id':query['query_id'],'paper_id':pid})
    write_csv(run_dir/'annotation_public'/'candidates.csv',public); write_csv(run_dir/'private'/'blind_mapping.csv',private)
    return update_status(run_dir,pool_status='pooled',pooled_pairs=len(public),ranker_run_rows=len(runs),annotation_status='awaiting_human_labels')


# Export independently randomized templates for the two frozen rating reviewers.
def export_annotation(run_dir):
    run_dir=Path(run_dir); frozen=json.loads((run_dir/'FROZEN_PILOT.json').read_text()); candidates=read_records(run_dir/'annotation_public'/'candidates.csv'); registry=json.loads((run_dir/'private'/'reviewers.json').read_text()); reviewers=[x['reviewer_id'] for x in registry['reviewers'] if x['role']=='reviewer']
    for reviewer in reviewers:
        ordered=list(candidates); random.Random(int(digest([frozen['pool_version'],reviewer])[:16],16)).shuffle(ordered)
        rows=[{**row,'reviewer_id':reviewer,'relevance_0_1_2':'','unsure':'','rationale':'','rated_at':'','revision':'1','change_reason':''} for row in ordered]
        write_csv(run_dir/'annotation_public'/f'ratings_{reviewer}.csv',rows)
    return {'status':'awaiting_human_labels','requested_ratings':2*len(candidates),'reviewers':reviewers,'pool_version':frozen['pool_version']}


# Import append-only rating revisions; exact repeats are idempotent and changes require reasons.
def import_annotations(input_path,run_dir,root=None):
    run_dir=Path(run_dir); incoming=read_records(input_path); frozen=json.loads((run_dir/'FROZEN_PILOT.json').read_text()); allowed={row['blind_id'] for row in read_records(run_dir/'annotation_public'/'candidates.csv')}; registry=json.loads((run_dir/'private'/'reviewers.json').read_text()); reviewers={x['reviewer_id'] for x in registry['reviewers'] if x['role']=='reviewer'}
    target=run_dir/'private'/'ratings_history.jsonl'; history=read_jsonl(target); latest={}
    for item in history: latest[(item['blind_id'],item['reviewer_id'])]=item
    seen_input=set(); appended=0
    for row in incoming:
        key=(row.get('blind_id'),row.get('reviewer_id'))
        if key in seen_input: raise ValueError('Duplicate rating row in import')
        seen_input.add(key)
        if row.get('pool_version')!=frozen['pool_version'] or key[0] not in allowed or key[1] not in reviewers: raise ValueError('Unknown ID or pool version mismatch')
        unsure=str(row.get('unsure','')).lower() in ('1','true','yes'); score=str(row.get('relevance_0_1_2','')).strip()
        if (not unsure and score not in ('0','1','2')) or not str(row.get('rationale','')).strip() or not str(row.get('rated_at','')).strip(): raise ValueError('Invalid or incomplete rating')
        revision=int(row.get('revision') or 1); record={'pool_version':frozen['pool_version'],'blind_id':key[0],'reviewer_id':key[1],'score':None if unsure else int(score),'unsure':unsure,'rationale':row['rationale'].strip(),'rated_at':row['rated_at'].strip(),'revision':revision,'change_reason':str(row.get('change_reason','')).strip()}
        old=latest.get(key)
        comparable=lambda x:(x['score'],x['unsure'],x['rationale'],x['rated_at'])
        if old and comparable(old)==comparable(record): continue
        if old and (revision<=old['revision'] or not record['change_reason']): raise ValueError('Changed rating requires a higher revision and change_reason')
        if not old and revision!=1: raise ValueError('First rating revision must be 1')
        history.append(record); latest[key]=record; appended+=1
    write_jsonl(target,history); state=coverage_status(run_dir,latest); update_status(run_dir,**state); return {'status':'imported','appended':appended,'history_rows':len(history),**state}


# Derive completion-state coverage from the fixed A/B reviewer registry.
def coverage_status(run_dir,latest=None):
    run_dir=Path(run_dir); candidates=read_records(run_dir/'annotation_public'/'candidates.csv') if (run_dir/'annotation_public'/'candidates.csv').exists() else []; registry=json.loads((run_dir/'private'/'reviewers.json').read_text()) if (run_dir/'private'/'reviewers.json').exists() else {'reviewers':[]}; reviewers=[x['reviewer_id'] for x in registry['reviewers'] if x['role']=='reviewer']; n=len(candidates)
    if latest is None:
        latest={};
        for row in read_jsonl(run_dir/'private'/'ratings_history.jsonl'): latest[(row['blind_id'],row['reviewer_id'])]=row
    submitted=sum((row['blind_id'],reviewer) in latest for row in candidates for reviewer in reviewers); double=sum(all((row['blind_id'],reviewer) in latest for reviewer in reviewers) for row in candidates); numeric=sum(all((row['blind_id'],reviewer) in latest and latest[(row['blind_id'],reviewer)]['score'] is not None for reviewer in reviewers) for row in candidates)
    expected=2*n; status='invalid_empty_scope' if n==0 else ('awaiting_human_labels' if submitted==0 else ('fully_submitted' if submitted==expected else 'partially_labeled'))
    return {'annotation_status':status,'expected_pairs':n,'expected_ratings':expected,'submitted_ratings':submitted,'submission_coverage':submitted/expected if expected else 0.0,'double_submission_coverage':double/n if n else 0.0,'double_numeric_coverage':numeric/n if n else 0.0}


# Compute fixed-reviewer agreement without adjudication or third-reviewer substitution.
def agreement_metrics(pairs):
    if not pairs: return {'agreement_pairs':0,'raw_agreement':None,'linear_weighted_kappa':None,'kappa_null_reason':'insufficient_double_numeric_ratings'}
    agreement=sum(a==b for a,b in pairs)/len(pairs); observed=sum(abs(a-b) for a,b in pairs)/len(pairs); expected=sum(abs(a-b) for a,_ in pairs for _,b in pairs)/(len(pairs)**2)
    return {'agreement_pairs':len(pairs),'raw_agreement':agreement,'linear_weighted_kappa':1-observed/expected if expected else None,'kappa_null_reason':None if expected else 'constant_ratings'}


# Export disagreements and unsure pairs for a separately registered adjudicator.
def export_adjudication(run_dir):
    run_dir=Path(run_dir); registry=json.loads((run_dir/'private'/'reviewers.json').read_text()); reviewers=[x['reviewer_id'] for x in registry['reviewers'] if x['role']=='reviewer']; adjudicators=[x['reviewer_id'] for x in registry['reviewers'] if x['role']=='adjudicator']; latest={}
    for row in read_jsonl(run_dir/'private'/'ratings_history.jsonl'): latest[(row['blind_id'],row['reviewer_id'])]=row
    candidates={row['blind_id']:row for row in read_records(run_dir/'annotation_public'/'candidates.csv')}; rows=[]
    for blind,public in candidates.items():
        values=[latest.get((blind,reviewer)) for reviewer in reviewers]
        if all(values) and (any(x['score'] is None for x in values) or values[0]['score']!=values[1]['score']): rows.append({**public,'reviewer_a_score':values[0]['score'],'reviewer_b_score':values[1]['score'],'adjudicator_id':adjudicators[0] if len(adjudicators)==1 else '','final_grade':'','unresolved':'','rationale':'','adjudicated_at':'','revision':'1'})
    write_csv(run_dir/'annotation_public'/'adjudication_queue.csv',rows); update_status(run_dir,adjudication_status='awaiting_adjudication' if rows else 'not_required',adjudication_queue=len(rows)); return {'queue_rows':len(rows),'adjudicators':adjudicators}


# Import append-only adjudication decisions without changing original A/B ratings.
def import_adjudication(input_path,run_dir):
    run_dir=Path(run_dir); incoming=read_records(input_path); frozen=json.loads((run_dir/'FROZEN_PILOT.json').read_text()); registry=json.loads((run_dir/'private'/'reviewers.json').read_text()); adjudicators={x['reviewer_id'] for x in registry['reviewers'] if x['role']=='adjudicator'}; queue={x['blind_id'] for x in read_records(run_dir/'annotation_public'/'adjudication_queue.csv')}; target=run_dir/'private'/'adjudication_history.jsonl'; history=read_jsonl(target); latest={x['blind_id']:x for x in history}
    for row in incoming:
        if row.get('pool_version')!=frozen['pool_version'] or row.get('blind_id') not in queue or row.get('adjudicator_id') not in adjudicators: raise ValueError('Invalid adjudication provenance')
        unresolved=str(row.get('unresolved','')).lower() in ('1','true','yes'); grade=str(row.get('final_grade','')).strip()
        if not unresolved and grade not in ('0','1','2'): raise ValueError('Invalid adjudicated grade')
        if not str(row.get('rationale','')).strip() or not str(row.get('adjudicated_at','')).strip(): raise ValueError('Adjudication rationale and time required')
        revision=int(row.get('revision') or 1); old=latest.get(row['blind_id'])
        if old and revision<=old['revision']: raise ValueError('Adjudication revision must increase')
        record={'pool_version':frozen['pool_version'],'blind_id':row['blind_id'],'adjudicator_id':row['adjudicator_id'],'final_grade':None if unresolved else int(grade),'unresolved':unresolved,'rationale':row['rationale'].strip(),'adjudicated_at':row['adjudicated_at'].strip(),'revision':revision}; history.append(record); latest[row['blind_id']]=record
    write_jsonl(target,history); return update_status(run_dir,adjudication_status='imported')


# Analyze fixed A/B agreement, resolution coverage, and rank metrics at 3/5/10.
def analyze(run_dir):
    run_dir=Path(run_dir); coverage=coverage_status(run_dir); candidates=read_records(run_dir/'annotation_public'/'candidates.csv') if (run_dir/'annotation_public'/'candidates.csv').exists() else []; registry=json.loads((run_dir/'private'/'reviewers.json').read_text()) if (run_dir/'private'/'reviewers.json').exists() else {'reviewers':[]}; reviewers=[x['reviewer_id'] for x in registry['reviewers'] if x['role']=='reviewer']; latest={}
    for row in read_jsonl(run_dir/'private'/'ratings_history.jsonl'): latest[(row['blind_id'],row['reviewer_id'])]=row
    adjudicated={}
    for row in read_jsonl(run_dir/'private'/'adjudication_history.jsonl'): adjudicated[row['blind_id']]=row
    pairs=[]; resolved={}; private={row['blind_id']:row for row in read_records(run_dir/'private'/'blind_mapping.csv')} if (run_dir/'private'/'blind_mapping.csv').exists() else {}
    for public in candidates:
        blind=public['blind_id']; values=[latest.get((blind,r)) for r in reviewers]
        if len(reviewers)==2 and all(values) and all(x['score'] is not None for x in values): pairs.append((values[0]['score'],values[1]['score']))
        grade=None
        if len(reviewers)==2 and all(values) and all(x['score'] is not None for x in values) and values[0]['score']==values[1]['score']: grade=values[0]['score']
        elif blind in adjudicated and not adjudicated[blind]['unresolved']: grade=adjudicated[blind]['final_grade']
        if blind in private: resolved[(private[blind]['query_id'],private[blind]['paper_id'])]=grade
    agreement=agreement_metrics(pairs)
    runs=read_jsonl(run_dir/'private'/'ranker_runs.jsonl'); mapping=read_records(run_dir/'private'/'blind_mapping.csv') if (run_dir/'private'/'blind_mapping.csv').exists() else []; documents=defaultdict(dict)
    for row in mapping: documents[row['query_id']][row['paper_id']]=resolved.get((row['query_id'],row['paper_id']))
    family_of={row['paper_id']:row['family_id'] for row in read_records(run_dir/'private'/'family_map.csv')} if (run_dir/'private'/'family_map.csv').exists() else {}
    metrics=[]; config=run_config(run_dir)
    for row in runs:
        grades=documents.get(row['query_id'],{}); selected={pid:grades.get(pid) for pid in row['ranked_ids']}
        for k in (3,5,10):
            result=ranked_metrics(row['ranked_ids'],k,selected,grades,config['annotation_scope']); returned=row['ranked_ids'][:k]; relevant_families={family_of[pid] for pid,grade in grades.items() if grade is not None and grade>=1 and pid in family_of}; found={family_of[pid] for pid in returned if selected.get(pid) is not None and selected[pid]>=1 and pid in family_of}
            result.update(returned_family_count=len({family_of[pid] for pid in returned if pid in family_of}),relevant_family_count=len(relevant_families),family_recall_at_k=len(found)/len(relevant_families) if relevant_families and all(value is not None for value in grades.values()) else None)
            metrics.append({**{x:row[x] for x in ('pool_version','query_id','ranker')},**result})
    write_csv(run_dir/'metrics_by_query_ranker.csv',metrics)
    query_topic={row['query_id']:row['topic_id'] for row in read_records(run_dir/'queries.csv')} if (run_dir/'queries.csv').exists() else {}
    topic_groups=defaultdict(list); macro_groups=defaultdict(list)
    for row in metrics: topic_groups[(query_topic.get(row['query_id'],'unknown'),row['ranker'],row['k'])].append(row); macro_groups[(row['ranker'],row['k'])].append(row)
    summarize=lambda values,key:(statistics.mean([row[key] for row in values if row.get(key) is not None]) if any(row.get(key) is not None for row in values) else None)
    import statistics
    topic_summary=[{'topic_id':key[0],'ranker':key[1],'k':key[2],'query_n':len(values),'mean_precision_at_k':summarize(values,'precision_at_k'),'mean_ndcg_at_k':summarize(values,'ndcg_at_k'),'mean_shortfall':summarize(values,'shortfall')} for key,values in sorted(topic_groups.items())]
    macro_summary=[{'ranker':key[0],'k':key[1],'query_n':len(values),'query_macro_precision_at_k':summarize(values,'precision_at_k'),'query_macro_ndcg_at_k':summarize(values,'ndcg_at_k'),'mean_shortfall':summarize(values,'shortfall')} for key,values in sorted(macro_groups.items())]
    write_csv(run_dir/'metrics_by_topic.csv',topic_summary); write_csv(run_dir/'metrics_query_macro.csv',macro_summary)
    n=coverage['expected_pairs']; resolved_count=sum(value is not None for value in resolved.values()); unresolved_queue=sum(value is None for value in resolved.values())
    complete=n>0 and coverage['submission_coverage']==1 and resolved_count==n
    status='analysis_completed' if complete else ('invalid_empty_scope' if n==0 else 'partial_analysis')
    result={'schema_version':SCHEMA,'evaluator_version':EVALUATOR_VERSION,'status':status,**coverage,'resolved_pairs':resolved_count,'resolved_coverage':resolved_count/n if n else 0.0,'unresolved_pairs':unresolved_queue,
      **agreement,'metric_rows':len(metrics),'topic_summary_rows':len(topic_summary),'macro_summary_rows':len(macro_summary),'ready_for_scientific_claims':False}
    dump(run_dir/'analysis.json',result); update_status(run_dir,analysis_status=status,annotation_status=coverage['annotation_status'],resolved_coverage=result['resolved_coverage']); return result
