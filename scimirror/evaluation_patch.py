"""Evaluation-patch reanalysis, pending-input delivery, and reproducible archive."""
import csv
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path

from .common import digest, dump
from .retrieval_evaluation import EVALUATOR_VERSION, build_gold, evaluate_selection, regression_checks
from .retrieval_gold_audit import export_human_review
from .stage_a import file_hash, read_jsonl, write_jsonl
from .stage_a_repair import dataset_registry, load_config, load_dataset
from .stage_b_retrieval import init_pilot
from .v03_review import write_csv

SCHEMA='evaluation_patch_1'


# Recompute Stage A metrics from frozen selected IDs without calling a retriever.
def reanalyze(previous_run,output,root):
    previous_run=Path(previous_run); output=Path(output); root=Path(root); output.mkdir(parents=True,exist_ok=True)
    core=read_jsonl(previous_run/'core_results.jsonl')
    if len(core)!=1944: raise ValueError('Previous run must contain exactly 1944 core rows')
    config=load_config(root/'configs'/'stage_a_repair.json',root); registry=dataset_registry(config,root); datasets={name:load_dataset(config,root,registry,name) for name in ('original_legacy','supplement_unchanged')}; evaluated=[]
    for row in core:
        _,_,doc_family,relevant=datasets[row['dataset_id']]; spec=registry[row['dataset_id']]; judged=None
        if spec['qrels_path']:
            _,judged,_=build_gold(read_jsonl(root/spec['document_map_path']),read_jsonl(root/spec['qrels_path']),set(doc_family))
        evaluated.append({**row,**evaluate_selection(row['topic_id'],row['selected_ids'],row.get('candidate_ids',[]),doc_family,relevant,judged=judged)})
    grouped=defaultdict(list)
    for row in evaluated: grouped[(row['dataset_id'],row['ranker'],row['topic_id'],row['memory_condition'])].append(row)
    topic=[]
    for (dataset,ranker,topic_id,memory),values in sorted(grouped.items()):
        combos={(x['agent_field'],x['policy']) for x in values}
        if len(values)!=9 or len(combos)!=9: raise ValueError('Incomplete field/policy stratum')
        cov_num=sum(x['capacity_coverage_numerator'] for x in values); cov_den=sum(x['capacity_coverage_denominator'] for x in values); prec_num=sum(x['relevant_selected_count'] for x in values); prec_den=sum(x['document_precision_denominator'] for x in values)
        topic.append({'schema_version':SCHEMA,'evaluator_version':EVALUATOR_VERSION,'dataset_id':dataset,'ranker':ranker,'topic_id':topic_id,'memory_condition':memory,'n':len(values),
          'coverage_n':sum(x['capacity_coverage'] is not None for x in values),'capacity_coverage_numerator':cov_num,'capacity_coverage_denominator':cov_den,'mean_capacity_coverage':cov_num/cov_den if cov_den else None,
          'precision_n':sum(x['document_precision'] is not None for x in values),'document_precision_numerator':prec_num,'document_precision_denominator':prec_den,'mean_document_precision':prec_num/prec_den if prec_den else None,
          'empty_results':sum(x['empty_result'] for x in values),'unjudged_selected_count':sum(x['unjudged_selected_count'] for x in values)})
    thresholds=config['quality_thresholds']; checks=regression_checks(topic,thresholds['max_coverage_drop'],thresholds['max_precision_drop']); failures=[row for row in checks if row['status']=='failed']; incomplete=[row for row in checks if row['status']=='incomplete']; failed_topics=sorted({x['topic_id'] for x in failures})
    unresolved=[]
    for row in topic:
        if row['dataset_id']=='supplement_unchanged' and row['ranker']=='stage_a_repaired_v1' and ((row['mean_capacity_coverage'] is not None and row['mean_capacity_coverage']<.8) or row['empty_results']>0):
            unresolved.append({'topic_id':row['topic_id'],'memory_condition':row['memory_condition'],'mean_capacity_coverage':row['mean_capacity_coverage'],'empty_results':row['empty_results'],'n':row['n'],'reason':'low_coverage_or_empty_return'})
    cross_memory=[]; cross_groups=defaultdict(list)
    for row in evaluated: cross_groups[(row['dataset_id'],row['ranker'],row['topic_id'])].append(row)
    for (dataset,ranker,topic_id),values in sorted(cross_groups.items()):
        cov_num=sum(x['capacity_coverage_numerator'] for x in values); cov_den=sum(x['capacity_coverage_denominator'] for x in values); prec_num=sum(x['relevant_selected_count'] for x in values); prec_den=sum(x['document_precision_denominator'] for x in values)
        cross_memory.append({'dataset_id':dataset,'ranker':ranker,'topic_id':topic_id,'case_n':len(values),'capacity_coverage_numerator':cov_num,'capacity_coverage_denominator':cov_den,
          'cross_memory_capacity_coverage':cov_num/cov_den if cov_den else None,'document_precision_numerator':prec_num,'document_precision_denominator':prec_den,'cross_memory_document_precision':prec_num/prec_den if prec_den else None})
    macro=[]
    macro_groups=defaultdict(list)
    for row in evaluated: macro_groups[(row['dataset_id'],row['ranker'])].append(row)
    for (dataset,ranker),values in sorted(macro_groups.items()):
        cov_num=sum(x['capacity_coverage_numerator'] for x in values); cov_den=sum(x['capacity_coverage_denominator'] for x in values); prec_num=sum(x['relevant_selected_count'] for x in values); prec_den=sum(x['document_precision_denominator'] for x in values)
        topic_values=[row for row in cross_memory if row['dataset_id']==dataset and row['ranker']==ranker]; topic_cov=[row['cross_memory_capacity_coverage'] for row in topic_values if row['cross_memory_capacity_coverage'] is not None]; topic_prec=[row['cross_memory_document_precision'] for row in topic_values if row['cross_memory_document_precision'] is not None]
        macro.append({'dataset_id':dataset,'ranker':ranker,'case_n':len(values),'coverage_n':sum(x['capacity_coverage'] is not None for x in values),'capacity_coverage_numerator':cov_num,'capacity_coverage_denominator':cov_den,'case_weighted_capacity_coverage':cov_num/cov_den if cov_den else None,
          'topic_n':len(topic_cov),'topic_macro_capacity_coverage':statistics.mean(topic_cov) if topic_cov else None,'precision_n':sum(x['document_precision'] is not None for x in values),'document_precision_numerator':prec_num,'document_precision_denominator':prec_den,
          'case_weighted_document_precision':prec_num/prec_den if prec_den else None,'precision_topic_n':len(topic_prec),'topic_macro_document_precision':statistics.mean(topic_prec) if topic_prec else None})
    selected_hash=digest({row['core_case_id']:row['selected_ids'] for row in sorted(core,key=lambda x:x['core_case_id'])}); old_failures=read_jsonl(previous_run/'failure_cases.jsonl')
    old_metrics=[]
    if (previous_run/'metrics_by_dataset_topic.csv').exists():
        with (previous_run/'metrics_by_dataset_topic.csv').open(encoding='utf-8-sig',newline='') as stream: old_metrics=list(csv.DictReader(stream))
    old_lookup={(row['dataset_id'],row['ranker'],row['topic_id'],row['memory_condition']):row for row in old_metrics}; metric_diff=[]
    parse=lambda value:None if value in (None,'') else float(value)
    for row in topic:
        key=(row['dataset_id'],row['ranker'],row['topic_id'],row['memory_condition']); old=old_lookup.get(key,{})
        for old_name,new_name in (('mean_capacity_coverage','mean_capacity_coverage'),('mean_document_precision','mean_document_precision')):
            before=parse(old.get(old_name)); after=row[new_name]
            metric_diff.append({'dataset_id':key[0],'ranker':key[1],'topic_id':key[2],'memory_condition':key[3],'metric':new_name,'before':before,'after':after,
              'difference':after-before if before is not None and after is not None else None,'change_source':'evaluator_v2_unknown_semantics_or_weighted_denominator' if before!=after else 'unchanged'})
    old_topics=sorted({row.get('topic_id') for row in old_failures if row.get('topic_id')}); diff={'old_failure_record_count':len(old_failures),'old_failed_topic_count':len(old_topics),'old_failed_topics':old_topics,
      'patched_failure_record_count':len(failures),'patched_failed_topic_count':len(failed_topics),'patched_failed_topics':failed_topics,
      'explanation':'Patched records preserve memory strata; selected IDs are unchanged and no retrieval/calibration was run.'}
    write_jsonl(output/'evaluated_results_v2.jsonl',evaluated); write_csv(output/'metrics_by_dataset_topic_memory.csv',topic); write_csv(output/'metrics_by_dataset_topic_cross_memory.csv',cross_memory); write_csv(output/'macro_metrics.csv',macro); write_csv(output/'metric_diff.csv',metric_diff); write_jsonl(output/'regression_checks_v2.jsonl',checks); write_jsonl(output/'regression_failures_v2.jsonl',failures); write_jsonl(output/'incomplete_comparisons_v2.jsonl',incomplete); write_csv(output/'unresolved_topics.csv',unresolved); dump(output/'failure_diff.json',diff)
    result={'schema_version':SCHEMA,'evaluator_version':EVALUATOR_VERSION,'status':'completed','source_run':str(previous_run.resolve()),'source_core_sha256':file_hash(previous_run/'core_results.jsonl'),
      'core_rows':len(core),'selected_ids_hash_before':selected_hash,'selected_ids_hash_after':selected_hash,'selected_ids_unchanged':True,'retrieval_calls':0,'calibration_calls':0,
      'failure_record_count':len(failures),'incomplete_comparison_count':len(incomplete),'failed_topic_count':len(failed_topics),'failed_topics':failed_topics,'unresolved_topic_rows':len(unresolved)}
    dump(output/'REANALYSIS_STATUS.json',result); return result


# Copy a complete importable source snapshot without historical output trees.
def stage_source(root,output):
    root=Path(root); source=Path(output)/'reproduction'/'source'
    if source.exists(): shutil.rmtree(source)
    for rel in ('scimirror','tests','configs','data'): shutil.copytree(root/rel,source/rel,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ('execute_evaluation_patch.py','execute_stage_b_retrieval.py','run.py','requirements.txt','AGENTS.md','README.md'):
        if (root/name).exists(): shutil.copy2(root/name,source/name)
    return source


# Execute evaluator and Stage B test-only suites from a separately extracted tree.
def reproduce(root,output):
    root=Path(root); output=Path(output); source=stage_source(root,output); temporary=Path(tempfile.mkdtemp(prefix='scimirror_eval_patch_repro_')); archive=temporary/'source.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(x for x in source.rglob('*') if x.is_file()): bundle.write(path,path.relative_to(source))
    extracted=temporary/'extracted'; zipfile.ZipFile(archive).extractall(extracted); env=dict(os.environ); env.pop('PYTHONPATH',None)
    command=[sys.executable,'-m','unittest','tests.test_retrieval_evaluation','tests.test_stage_b_pilot']; process=subprocess.run(command,cwd=extracted,env=env,text=True,encoding='utf-8',errors='replace',capture_output=True)
    probe=subprocess.run([sys.executable,'-c','import scimirror.retrieval_evaluation as m; print(m.__file__)'],cwd=extracted,env=env,text=True,encoding='utf-8',errors='replace',capture_output=True); loaded=Path(probe.stdout.strip()).resolve() if probe.returncode==0 else None; inside=loaded is not None and extracted.resolve() in loaded.parents
    result={'status':'passed' if process.returncode==0 and inside else 'failed','command':' '.join(command),'exit_code':process.returncode,'actual_loaded_module':str(loaded) if loaded else None,'module_inside_extracted_tree':inside,'python':sys.version,'platform':platform.platform(),'stdout':process.stdout[-2000:],'stderr':process.stderr[-2000:]}
    dump(output/'reproduction'/'REPRODUCTION_RESULT.json',result); shutil.rmtree(temporary,ignore_errors=True); return result


# Create reports, validation, checksums, and a non-recursive delivery ZIP.
def package(root,output,tests,reanalysis,human,pilot,repository,reproduction):
    root=Path(root); output=Path(output); stage_source(root,output)
    validation={'schema_version':SCHEMA,'code_patch_status':'passed' if tests['status']=='passed' else 'failed','evaluation_unit_status':tests['status'],
      'stage_a_reanalysis_status':reanalysis.get('status','missing_previous_run'),'legacy_quality_status':'failed' if reanalysis.get('failed_topic_count',0) else ('not_assessed' if reanalysis.get('status')!='completed' else 'passed'),
      'human_gold_review_status':human['status'],'stage_b_tooling_status':'passed' if tests['status']=='passed' else 'failed','real_data_status':pilot['data_status'],
      'annotation_status':pilot['annotation_status'],'adjudication_status':pilot['adjudication_status'],'analysis_status':pilot['analysis_status'],'reproduction_status':reproduction['status'],
      'repository_regression_status':repository['status'],'tool_delivery_completed':tests['status']=='passed' and reproduction['status']=='passed' and repository['status']=='passed','ready_for_scientific_claims':False}
    dump(output/'DELIVERY_VALIDATION.json',validation); dump(output/'tests_report.json',tests)
    (output/'CODE_CHANGE_REPORT.md').write_text('# Code changes\n\nPatched memory-stratified regression, explicit unknown labels, frozen Stage B inputs, family/version audit, fixed A/B append-only review history, adjudication, completion coverage, and rank metrics. Retrieval algorithms, c05, gold, and social mechanisms were not changed.\n',encoding='utf-8')
    (output/'ANNOTATION_GUIDE_ZH.md').write_text('# 标注指南\n\n评分对象是查询与文献的相关性：0=无关，1=部分相关，2=直接相关；信息不足选择 unsure。每条必须填写依据和时间。A/B 独立评分，分歧由预登记裁决者处理。不得参考检索器、得分或另一人的评分。\n',encoding='utf-8')
    next_lines=['# 下一步所需输入','','1. 30–50 篇、2024 年及以前、带标题摘要和来源的真实文献。','2. 6–9 条人工自由文本查询，每个目标主题 2–3 条。','3. 每篇文献的 family/canonical 映射。','4. 两名固定独立评分者及可选裁决者登记。','5. 完成两份盲评表及必要裁决。','','下一条命令：`python3 execute_stage_b_retrieval.py import-corpus --run-dir <pilot> --input real_papers.jsonl`。']
    (output/'NEXT_INPUTS.md').write_text('\n'.join(next_lines)+'\n',encoding='utf-8')
    report=['# 评价修补与真实文献小试验准备报告','',f'- 代码与测试：`{validation["code_patch_status"]}`。',f'- Stage A 旧结果重分析：`{validation["stage_a_reanalysis_status"]}`；selected IDs 保持：`{reanalysis.get("selected_ids_unchanged")}`。',
      f'- 修补后失败记录：{reanalysis.get("failure_record_count")}；失败主题数：{reanalysis.get("failed_topic_count")}。','- 变化来自评价器完整保留 memory 分层和未知标签，不来自检索重跑或重新调参。',
      f'- 人工 gold：`{human["status"]}`。',f'- Stage B 真实数据：`{pilot["data_status"]}`；标注：`{pilot["annotation_status"]}`；分析：`{pilot["analysis_status"]}`。','- 当前没有真实论文、人工查询或人工评分被自动生成；科研因果主张状态为 false。']
    (output/'FINAL_REPORT_ZH.md').write_text('\n'.join(report)+'\n',encoding='utf-8')
    head=subprocess.run(['git','rev-parse','HEAD'],cwd=root,text=True,encoding='utf-8',errors='replace',capture_output=True).stdout.strip(); status=subprocess.run(['git','status','--short'],cwd=root,text=True,encoding='utf-8',errors='replace',capture_output=True).stdout.splitlines()
    dump(output/'runtime_manifest.json',{'schema_version':SCHEMA,'actual_head':head,'working_tree_status':status,'python':sys.version,'platform':platform.platform(),'network_requests':0,'llm_calls':0,'paid_api_calls':0})
    checks=[]
    for path in sorted(x for x in output.rglob('*') if x.is_file() and x.suffix!='.zip' and x.name!='CHECKSUMS.sha256'): checks.append(f'{file_hash(path)}  {path.relative_to(output).as_posix()}')
    (output/'CHECKSUMS.sha256').write_text('\n'.join(checks)+'\n',encoding='utf-8'); archive=output/'evaluation_patch_delivery.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(x for x in output.rglob('*') if x.is_file() and x!=archive and x.suffix!='.zip'): bundle.write(path,Path('delivery')/path.relative_to(output))
    return validation,{'path':str(archive),'sha256':file_hash(archive),'files':len(zipfile.ZipFile(archive).namelist())}
