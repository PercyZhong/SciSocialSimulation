"""Offline real-document import, pooling, blind annotation, and analysis tooling."""
import csv, hashlib, json, math, re
from collections import Counter, defaultdict
from pathlib import Path

from .common import canonical, digest
from .corpus import Corpus
from .policy import PolicyContext
from .topics import TopicModel
from .v03_retrieval import load_retrieval_profiles, normalize_text, retrieve_stage_a_repaired, retrieve_stage_a_semantic_guarded
from .v03_review import write_csv


# Read CSV or JSONL records without changing source values.
def read_records(path):
    path=Path(path)
    if path.suffix.lower()=='.csv':
        with path.open(encoding='utf-8-sig',newline='') as stream: return list(csv.DictReader(stream))
    if path.suffix.lower()=='.jsonl': return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    raise ValueError('Input must be CSV or JSONL')


# Normalize DOI and arXiv identifiers for version-aware deduplication.
def normalize_identifier(value,kind):
    value=str(value or '').strip().lower()
    if kind=='doi': value=re.sub(r'^(https?://(dx\.)?doi\.org/|doi:\s*)','',value)
    if kind=='arxiv': value=re.sub(r'^(https?://arxiv\.org/abs/|arxiv:\s*)','',value); value=re.sub(r'v\d+$','',value)
    return value


# Initialize an empty, explicitly pending Stage B pilot directory.
def init_pilot(run_dir,config):
    run_dir=Path(run_dir); (run_dir/'private').mkdir(parents=True,exist_ok=True); (run_dir/'annotation_public').mkdir(parents=True,exist_ok=True)
    headers=['query_id','topic_id','query_text','intent','author_type','split']
    write_csv(run_dir/'queries_template.csv',[],fields=headers)
    write_csv(run_dir/'annotations_template.csv',[],fields=['blind_id','reviewer_id','relevance_0_1_2','unsure','rationale','rated_at'])
    write_csv(run_dir/'family_map_template.csv',[],fields=['paper_id','family_id','split','reviewer_id','rationale','updated_at'])
    (run_dir/'QUERY_EXAMPLE_ONLY.csv').write_text('query_id,topic_id,query_text,intent,author_type,split\nexample_not_formal,agent_memory,How do agents retrieve episodic memory?,diagnostic,example,test\n',encoding='utf-8-sig')
    status={'schema_version':config['schema_version'],'tooling_status':'initialized','data_status':'awaiting_real_corpus','query_status':'awaiting_human_queries',
      'annotation_status':'awaiting_human_labels','ready_for_scientific_claims':False,'network_requests':0,'llm_calls':0}
    (run_dir/'status.json').write_text(json.dumps(status,indent=2)+'\n',encoding='utf-8'); return status


# Validate and deduplicate locally supplied real papers without inventing missing abstracts.
def import_corpus(input_path,run_dir,config):
    run_dir=Path(run_dir); rows=read_records(input_path); required={'paper_id','title','abstract','year','source_url','synthetic'}; accepted=[]; excluded=[]; seen={}
    for index,row in enumerate(rows,1):
        missing=sorted(key for key in required if key not in row or str(row.get(key,'')).strip()=='')
        synthetic=row.get('synthetic')
        is_false=synthetic is False or str(synthetic).strip().lower() in ('false','0','no')
        if missing or not is_false:
            excluded.append({'row':index,'paper_id':row.get('paper_id'),'reason':'missing:'+','.join(missing) if missing else 'synthetic_must_be_false'}); continue
        try: year=int(row['year'])
        except (TypeError,ValueError): excluded.append({'row':index,'paper_id':row.get('paper_id'),'reason':'invalid_year'}); continue
        paper={'id':str(row['paper_id']).strip(),'paper_id':str(row['paper_id']).strip(),'title':str(row['title']).strip(),'abstract':str(row['abstract']).strip(),
          'year':year,'source_url':str(row['source_url']).strip(),'source_provider':str(row.get('source_provider','unknown')).strip() or 'unknown',
          'doi':normalize_identifier(row.get('doi'),'doi'),'arxiv_id':normalize_identifier(row.get('arxiv_id'),'arxiv'),
          'retrieved_at':str(row.get('retrieved_at','')).strip(),'version':str(row.get('version','')).strip(),'field':str(row.get('field','unknown')).strip() or 'unknown',
          'synthetic':False,'source_verification':'not_verified_offline'}
        key=('doi',paper['doi']) if paper['doi'] else (('arxiv',paper['arxiv_id']) if paper['arxiv_id'] else ('text',normalize_text(paper['title']+' '+paper['abstract'])))
        if key in seen: excluded.append({'row':index,'paper_id':paper['id'],'reason':'duplicate_of:'+seen[key]}); continue
        seen[key]=paper['id']; accepted.append(paper)
    if len({row['id'] for row in accepted})!=len(accepted): raise ValueError('Duplicate paper_id after normalization')
    (run_dir/'corpus.jsonl').write_text(''.join(canonical(row)+'\n' for row in accepted),encoding='utf-8'); write_csv(run_dir/'corpus_exclusions.csv',excluded,fields=['row','paper_id','reason'])
    status=json.loads((run_dir/'status.json').read_text()); status['data_status']='ready' if len(accepted)>=3 else 'insufficient_real_corpus'; status['documents']=len(accepted); status['excluded']=len(excluded)
    (run_dir/'status.json').write_text(json.dumps(status,indent=2)+'\n'); return status


# Import human-authored queries and reject examples or malformed topic assignments.
def import_queries(input_path,run_dir,config):
    run_dir=Path(run_dir); rows=read_records(input_path); required={'query_id','topic_id','query_text','intent','author_type','split'}
    if any(not required<=set(row) for row in rows): raise ValueError('Query columns missing')
    if any(not all(str(row[key]).strip() for key in required) or row['topic_id'] not in config['topics'] or row['query_id']=='example_not_formal' for row in rows): raise ValueError('Invalid formal query')
    if len({row['query_id'] for row in rows})!=len(rows): raise ValueError('Duplicate query_id')
    write_csv(run_dir/'queries.csv',rows); status=json.loads((run_dir/'status.json').read_text()); status['query_status']='ready'; status['queries']=len(rows)
    (run_dir/'status.json').write_text(json.dumps(status,indent=2)+'\n'); return status


# Compute a standard-library BM25 score for one free-text query.
def bm25_scores(query,papers,k1=1.2,b=.75):
    tokenize=lambda text:re.findall(r'[a-z0-9]+',str(text).lower()); docs={pid:tokenize(row['title']+' '+row['abstract']) for pid,row in papers.items()}; terms=tokenize(query)
    avg=sum(map(len,docs.values()))/max(1,len(docs)); df=Counter(term for term in set(terms) for words in docs.values() if term in words); result={}
    for pid,words in docs.items():
        counts=Counter(words); score=0.0
        for term in terms:
            idf=math.log(1+(len(docs)-df[term]+.5)/(df[term]+.5)); freq=counts[term]
            score+=idf*freq*(k1+1)/(freq+k1*(1-b+b*len(words)/max(avg,1))) if freq else 0
        result[pid]=score
    return result


# Run three frozen rankers while ensuring query_text affects every ranking.
def pool(run_dir,config,root):
    run_dir=Path(run_dir); root=Path(root)
    if not (run_dir/'corpus.jsonl').exists() or not (run_dir/'queries.csv').exists():
        raise ValueError('Real corpus and human queries are required before pooling')
    corpus=Corpus(run_dir/'corpus.jsonl',config['cutoff_year'],False); topics=TopicModel(root/'data/topics.json',corpus.papers,1.0); queries=read_records(run_dir/'queries.csv'); mappings=[]; public={}
    closure_profiles=load_retrieval_profiles(root/'data/retrieval_topic_profiles_v1.json'); repaired_profiles=load_retrieval_profiles(root/'data/retrieval_topic_profiles_repair_v1.json')
    for query in queries:
        lexical=bm25_scores(query['query_text'],corpus.papers)
        for ranker in config['rankers']:
            if ranker=='bm25_lexical': candidates=sorted(corpus.papers,key=lambda pid:(-lexical[pid],pid))[:config['top_k']]; scores=lexical
            else:
                if ranker=='stage_a_semantic_guarded':
                    rc={'_profiles':closure_profiles,'minimum_relevance':.8,'match_window_tokens':18,'field_context_weight':0,'candidate_pool_size':20,'top_k':20,'relevance_weight':1,'near_duplicate_threshold':.97,'max_per_near_duplicate_cluster':1}
                    _,audit=retrieve_stage_a_semantic_guarded(corpus.papers,topics,PolicyContext('balanced','retrieval',False,.5),rc,query['topic_id'],'unknown',[],[])
                else:
                    rc={'_profiles':repaired_profiles,'minimum_relevance':.8,'match_window_tokens':18,'alpha':1,'beta_field':0,'beta_memory':0,'memory_enabled':False,'candidate_pool_size':20,'top_k':20,'near_duplicate_threshold':.97,'max_per_near_duplicate_cluster':1}
                    _,audit=retrieve_stage_a_repaired(corpus.papers,topics,PolicyContext('balanced','retrieval',False,.5),rc,query['topic_id'],'unknown',[],[])
                eligible=audit['selected_ids']; peak=max((lexical[pid] for pid in eligible),default=0); scores={pid:(next(x['evidence_score'] for x in audit['diagnostics'] if x['paper_id']==pid)+(.2*lexical[pid]/peak if peak else 0)) for pid in eligible}
                candidates=sorted(eligible,key=lambda pid:(-scores[pid],pid))[:config['top_k']]
            for rank, pid in enumerate(candidates,1):
                blind=digest([query['query_id'],pid])[:20]; mappings.append({'blind_id':blind,'query_id':query['query_id'],'paper_id':pid,'ranker':ranker,'original_rank':rank,'score':scores[pid]})
                paper=corpus.papers[pid]; public[blind]={'blind_id':blind,'query_id':query['query_id'],'topic_id':query['topic_id'],'query_text':query['query_text'],
                  'intent':query['intent'],'paper_id':pid,'title':paper['title'],'abstract':paper['abstract'],'year':paper['year'],'source_url':paper['source_url'],'source_provider':paper['source_provider']}
    write_csv(run_dir/'private'/'pool_mapping.csv',mappings); write_csv(run_dir/'annotation_public'/'candidates.csv',[public[key] for key in sorted(public)])
    status=json.loads((run_dir/'status.json').read_text()); status['pool_status']='ready'; status['pooled_pairs']=len(public); (run_dir/'status.json').write_text(json.dumps(status,indent=2)+'\n'); return status


# Export an empty two-reviewer rating template without private ranking metadata.
def export_annotation(run_dir):
    run_dir=Path(run_dir); candidates=read_records(run_dir/'annotation_public'/'candidates.csv'); rows=[]
    for reviewer in ('reviewer_a','reviewer_b'):
        rows.extend({'blind_id':row['blind_id'],'reviewer_id':reviewer,'relevance_0_1_2':'','unsure':'','rationale':'','rated_at':''} for row in candidates)
    write_csv(run_dir/'annotation_public'/'ratings_template.csv',rows); return {'status':'awaiting_human_labels','requested_ratings':len(rows),'reviewers':2}


# Import genuine reviewer records incrementally and reject fabricated or invalid scores.
def import_annotations(input_path,run_dir):
    run_dir=Path(run_dir); incoming=read_records(input_path); allowed={row['blind_id'] for row in read_records(run_dir/'annotation_public'/'candidates.csv')}; target=run_dir/'private'/'ratings.jsonl'
    existing=[json.loads(line) for line in target.read_text().splitlines() if line] if target.exists() else []; keyed={(row['blind_id'],row['reviewer_id']):row for row in existing}
    for row in incoming:
        if row.get('blind_id') not in allowed or not str(row.get('reviewer_id','')).strip() or str(row.get('reviewer_id')).lower().startswith('codex'): raise ValueError('Invalid annotation provenance')
        if not str(row.get('rationale','')).strip() or not str(row.get('rated_at','')).strip():
            raise ValueError('Human annotations require rationale and rated_at')
        unsure=str(row.get('unsure','')).lower() in ('1','true','yes'); score=str(row.get('relevance_0_1_2','')).strip()
        if not unsure and score not in ('0','1','2'): raise ValueError('Rating must be 0, 1, 2, or unsure')
        keyed[(row['blind_id'],row['reviewer_id'])]={'blind_id':row['blind_id'],'reviewer_id':row['reviewer_id'],'score':None if unsure else int(score),'unsure':unsure,
          'rationale':str(row.get('rationale','')).strip(),'rated_at':str(row.get('rated_at','')).strip()}
    target.write_text(''.join(canonical(row)+'\n' for row in keyed.values()),encoding='utf-8'); return {'status':'imported','ratings':len(keyed)}


# Compute agreement only after two independent reviewers have completed comparable ratings.
def analyze(run_dir):
    run_dir=Path(run_dir); target=run_dir/'private'/'ratings.jsonl'; ratings=[json.loads(line) for line in target.read_text().splitlines() if line] if target.exists() else []
    by=defaultdict(list)
    for row in ratings:
        if row['score'] is not None: by[row['blind_id']].append(row)
    paired=[]
    for rows in by.values():
        distinct={row['reviewer_id']:row for row in rows}
        if len(distinct)>=2: paired.append([distinct[key] for key in sorted(distinct)[:2]])
    agreement=sum(rows[0]['score']==rows[1]['score'] for rows in paired)/len(paired) if paired else None
    kappa=None; reason='insufficient_double_ratings'
    if paired:
        a=[rows[0]['score'] for rows in paired]; b=[rows[1]['score'] for rows in paired]; observed=sum(abs(x-y) for x,y in zip(a,b))/len(a)
        expected=sum(abs(x-y) for x in a for y in b)/(len(a)*len(b)); kappa=1-observed/expected if expected else None; reason=None if expected else 'constant_ratings'
    result={'status':'completed' if paired else 'awaiting_human_labels','ratings':len(ratings),'double_rated_pairs':len(paired),'raw_agreement':agreement,
      'linear_weighted_kappa':kappa,'kappa_null_reason':reason,'judged_coverage_status':'pending_without_complete_pool_labels','ready_for_scientific_claims':False}
    (run_dir/'analysis.json').write_text(json.dumps(result,indent=2)+'\n'); return result


# The versioned small-pilot implementation supersedes these compatibility definitions.
from .stage_b_pilot import (agreement_metrics, analyze, bm25_scores, coverage_status, export_adjudication,
    export_annotation, freeze, import_adjudication, import_annotations, import_corpus,
    import_family_map, import_queries, import_reviewers, init_pilot, normalize_identifier,
    pool, read_records, run_config, verify_freeze)
