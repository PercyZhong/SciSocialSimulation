"""Directed retrieval, blind-review and quality-accounting tests for v0.3."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from scimirror.corpus import Corpus
from scimirror.policy import context
from scimirror.topics import TopicModel
from scimirror.v03_pipeline import load_v03_config, simulation_adapter
from scimirror.v03_analysis import (analyze_reviews, condition_key, estimate_stratum_quality,
                                    paired_quality_effects)
from scimirror.v03_retrieval import build_query, document_overlap, duplicate_clusters, retrieve_relevance_gated
from scimirror.v03_review import (DIMENSIONS, RUBRIC_VERSION, agreement_rows, import_reviews,
                                  mock_review_records, quadratic_weighted_kappa, quality_scores, stratified_sample,
                                  read_csv, validate_public_payload, validate_review_package, write_csv)


ROOT = Path(__file__).resolve().parents[1]


class V03Tests(unittest.TestCase):
    # 加载冻结v0.3配置、截点语料和主题模型。
    def setUp(self):
        self.cfg = load_v03_config(ROOT/'configs/v03_mock_main.json')
        self.corpus = Corpus(ROOT/self.cfg['corpus'], self.cfg['cutoff_year'], True)
        self.topics = TopicModel(ROOT/self.cfg['topics'], self.corpus.papers, 1.0)

    # 验证查询实际展开topic描述和关键词而非只保留下划线ID。
    def test_query_expands_selected_topic(self):
        query = build_query(self.topics, 'agent_memory', 'agents', [], self.cfg['retrieval']['query_weights'])
        self.assertIn('memory', query['components']['topic'])
        self.assertIn('retrieval', query['components']['topic'])
        self.assertFalse(query['unknown_topic'])

    # 验证可区分主题fixture优先返回各自主题文献且结果确定。
    def test_changed_topic_changes_relevant_fixture(self):
        policy = context(self.cfg, 'balanced', 'retrieval')
        first, _ = retrieve_relevance_gated(self.corpus.papers, self.topics, policy, self.cfg['retrieval'],
                                             'agent_memory', 'agents', [], [])
        second, _ = retrieve_relevance_gated(self.corpus.papers, self.topics, policy, self.cfg['retrieval'],
                                              'agent_tools', 'agents', [], [])
        self.assertTrue(all('memory' in item['title'] for item in first))
        self.assertTrue(all('tool' in item['title'] for item in second))
        again, _ = retrieve_relevance_gated(self.corpus.papers, self.topics, policy, self.cfg['retrieval'],
                                             'agent_memory', 'agents', [], [])
        self.assertEqual([x['id'] for x in first], [x['id'] for x in again])

    # 验证全零相关时返回短缺而不以无关文献补满。
    def test_zero_relevance_returns_empty_with_reason(self):
        papers, audit = retrieve_relevance_gated(self.corpus.papers, self.topics,
            context(self.cfg,'balanced','retrieval'), self.cfg['retrieval'], 'unknown_xyz', 'unknownfield', [], [])
        self.assertEqual(papers, [])
        self.assertEqual(audit['fallback'], 'all_relevance_zero')
        self.assertTrue(all(item['normalized_relevance'] == 0 for item in audit['candidates']))

    # 验证论文主题预处理按field限制且截点后的论文仍被排除。
    def test_topic_field_and_year_boundary(self):
        self.assertEqual(self.topics.paper_topics['demo_agents_00'], ['agent_memory'])
        rows = list(self.corpus.papers.values()) + [{'id':'future','title':'memory retrieval','abstract':'x',
            'year':2025,'field':'agents','synthetic':True}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'papers.jsonl'
            path.write_text('\n'.join(json.dumps(row) for row in rows), encoding='utf-8')
            corpus = Corpus(path, 2025, True)
        self.assertNotIn('future', corpus.papers)

    # 验证确切重复被聚类而同主题不同方法不会在高阈值下误并。
    def test_near_duplicate_rule(self):
        items = [{'paper':{'id':'a','title':'memory method','abstract':'controlled graph trials'}},
                 {'paper':{'id':'b','title':'memory method','abstract':'controlled graph trials'}},
                 {'paper':{'id':'c','title':'memory method','abstract':'qualitative interview field study'}}]
        clusters = duplicate_clusters(items, .8)
        self.assertEqual(clusters['a'], clusters['b'])
        self.assertNotEqual(clusters['a'], clusters['c'])

    # 验证空集合重合率保持null并说明原因。
    def test_document_overlap_empty_reason(self):
        value = document_overlap([], [])
        self.assertIsNone(value['jaccard'])
        self.assertEqual(value['reason'], 'both_empty')

    # 验证v0.3适配使用独立缓存协议且不会修改原配置。
    def test_schema_adapter_and_cache_namespace(self):
        original = copy.deepcopy(self.cfg)
        adapted = simulation_adapter(self.cfg)
        self.assertEqual(adapted['cache_protocol'], 3)
        self.assertEqual(adapted['schema_version'], '0.2')
        self.assertEqual(self.cfg, original)

    # 构造含小层和空层的固定评审总体。
    def population(self):
        items = []
        for index in range(5):
            items.append({'project_id':f'p{index}','seed':42,'policy':'balanced','network':'closed',
                          'output_type':'solo'})
        items.append({'project_id':'team','seed':42,'policy':'balanced','network':'closed','output_type':'team'})
        return {'items':items}

    # 验证分层抽样可重复、无放回、概率正确且小层全取。
    def test_stratified_sampling(self):
        first, audit = stratified_sample(self.population(), 7, 3)
        second, _ = stratified_sample(self.population(), 7, 3)
        self.assertEqual([x[0]['project_id'] for x in first], [x[0]['project_id'] for x in second])
        self.assertEqual(len({x[0]['project_id'] for x in first}), 4)
        solo = next(x for x in audit if x['seed'] == 42 and x['policy'] == 'balanced' and x['network'] == 'closed' and x['output_type'] == 'solo')
        team = next(x for x in audit if x['output_type'] == 'team' and x['policy'] == 'balanced' and x['network'] == 'closed')
        self.assertEqual(solo['inclusion_probability'], .6)
        self.assertEqual(team['inclusion_probability'], 1.0)
        self.assertTrue(any(x['N_h'] == 0 and x['n_h'] == 0 for x in audit))

    # 验证公开评审材料拒绝条件映射和制度评分字段。
    def test_public_payload_leak_scan(self):
        validate_public_payload({'review_id':'rvw_random','title':'scientific policy design'})
        with self.assertRaises(ValueError):
            validate_public_payload({'review_id':'x','network':'open'})
        with self.assertRaises(ValueError):
            validate_public_payload({'title':'seed_42/novelty_open'})

    # 构造一行完整且可验证的评审记录。
    def review_row(self, review_id='rvw_x', reviewer='a', score=4):
        row = {'review_id':review_id,'reviewer_id':reviewer,'reviewer_kind':'human','model_version':'',
               'rubric_version':RUBRIC_VERSION,'evidence_access_status':'available','references_valid':'true',
               'is_mock':'false','started_at':'','completed_at':'','status':'completed'}
        for dimension in DIMENSIONS:
            row.update({dimension+'_score':score,dimension+'_reason':'supported reason',dimension+'_evidence_id':'e1'})
        return row

    # 验证导入拒绝越界、重复评分者、非法证据、错误rubric和mock评分。
    def test_review_import_rejections(self):
        with tempfile.TemporaryDirectory() as folder:
            review_dir = Path(folder); (review_dir/'review_public').mkdir(); (review_dir/'review_results').mkdir(); (review_dir/'review_private').mkdir()
            (review_dir/'review_public'/'ideas.jsonl').write_text(json.dumps({'review_id':'rvw_x','reference_ids':['e1']})+'\n', encoding='utf-8')
            (review_dir/'review_private'/'reviewer_provenance.json').write_text(json.dumps({'reviewers':[{'id':'a'}]}), encoding='utf-8')
            fields = list(self.review_row())
            write_csv(review_dir/'review_public'/'ratings_template.csv', [self.review_row()], fields)
            write_csv(review_dir/'review_results'/'validated_reviews.csv', [], fields)
            (review_dir/'review_results'/'raw_reviews.jsonl').write_text('', encoding='utf-8')
            (review_dir/'status.json').write_text(json.dumps({'status':'awaiting_external_reviews'}), encoding='utf-8')
            cases = []
            bad = self.review_row(); bad['novelty_score'] = 6; cases.append([bad])
            bad = self.review_row(); bad['rubric_version'] = 'wrong'; cases.append([bad])
            bad = self.review_row(); bad['is_mock'] = 'true'; cases.append([bad])
            bad = self.review_row(); bad['novelty_evidence_id'] = 'outside'; cases.append([bad])
            cases.append([self.review_row(), self.review_row()])
            for index, rows in enumerate(cases):
                path = review_dir/f'bad{index}.csv'; write_csv(path, rows, fields)
                with self.assertRaises(ValueError):
                    import_reviews(review_dir, path)

    # 验证两位评审可分批导入、原始记录追加且生命周期状态保持一致。
    def test_incremental_review_import_and_status(self):
        with tempfile.TemporaryDirectory() as folder:
            review_dir = Path(folder); public = review_dir/'review_public'; private = review_dir/'review_private'; results = review_dir/'review_results'
            public.mkdir(); private.mkdir(); results.mkdir()
            review_id = 'rvw_'+'1'*24
            (public/'ideas.jsonl').write_text(json.dumps({'review_id':review_id,'reference_ids':['e1']})+'\n', encoding='utf-8')
            (public/'evidence.jsonl').write_text(json.dumps({'evidence_id':'e1'})+'\n', encoding='utf-8')
            (private/'reviewer_provenance.json').write_text(json.dumps({
                'reviewers':[{'id':'a','kind':'human'},{'id':'b','kind':'human'}],
                'review_independence_status':'independent', 'review_scope':'formal'}), encoding='utf-8')
            write_csv(private/'review_key.csv', [{'review_id':review_id}])
            write_csv(private/'sample_manifest.csv', [{'review_id':review_id,'N_h':1,'n_h':1,'inclusion_probability':1}])
            template = [self.review_row(review_id,'a'), self.review_row(review_id,'b')]
            fields = list(template[0]); write_csv(public/'ratings_template.csv', template, fields)
            write_csv(results/'validated_reviews.csv', [], fields)
            (results/'raw_reviews.jsonl').write_text('', encoding='utf-8')
            (review_dir/'status.json').write_text(json.dumps({'status':'awaiting_external_reviews'}), encoding='utf-8')
            first = review_dir/'first.csv'; second = review_dir/'second.csv'
            write_csv(first, [template[0]], fields); write_csv(second, [template[1]], fields)
            self.assertEqual(len(import_reviews(review_dir, first)), 1)
            self.assertEqual(json.loads((review_dir/'status.json').read_text())['status'], 'reviews_partially_imported')
            self.assertTrue(validate_review_package(review_dir)['all_passed'])
            self.assertEqual(len(import_reviews(review_dir, second)), 2)
            validation = validate_review_package(review_dir)
            self.assertTrue(validation['all_passed']); self.assertTrue(validation['ratings_complete'])
            self.assertEqual(len((results/'raw_reviews.jsonl').read_text(encoding='utf-8').splitlines()), 2)
            imported = read_csv(results/'validated_reviews.csv')
            imported[0]['reviewer_kind'] = 'external_model'
            write_csv(results/'validated_reviews.csv', imported, fields)
            mismatch = validate_review_package(review_dir)
            self.assertFalse(mismatch['checks']['validated_reviewer_kinds_match_provenance'])
            with self.assertRaises(ValueError):
                import_reviews(review_dir, second)

    # 验证一致、分歧、恒定和无共同项目时的一致性边界。
    def test_agreement_boundaries(self):
        value, reason = quadratic_weighted_kappa([1,2,3],[1,2,3])
        self.assertAlmostEqual(value, 1.0); self.assertIsNone(reason)
        value, _ = quadratic_weighted_kappa([1,1,5,5],[5,5,1,1])
        self.assertLess(value, 0)
        value, reason = quadratic_weighted_kappa([3,3],[3,3])
        self.assertIsNone(value); self.assertEqual(reason, 'constant_ratings')
        value, reason = quadratic_weighted_kappa([3,3,3],[2,3,4])
        self.assertIsNone(value); self.assertEqual(reason, 'constant_ratings')
        value, reason = quadratic_weighted_kappa([],[])
        self.assertIsNone(value); self.assertEqual(reason, 'no_common_items')

    # 验证四维完整双评分才计算Q且缺失不会被填零。
    def test_quality_formula_and_missing(self):
        rows = [self.review_row(reviewer='a', score=5), self.review_row(reviewer='b', score=3)]
        quality = quality_scores(rows)['rvw_x']
        self.assertEqual(quality['Q'], .75)
        rows[1]['evidence_support_score'] = None
        self.assertIsNone(quality_scores(rows)['rvw_x']['Q'])

    # 验证完整双评分会生成非空配对效应、逐类型四维统计和权重敏感性表。
    def test_complete_quality_analysis_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); run_dir = root/'run'; review_dir = root/'review'; output = run_dir/'analysis'
            run_dir.mkdir(); (review_dir/'review_private').mkdir(parents=True); (review_dir/'review_results').mkdir()
            seeds = [1,2]
            config = copy.deepcopy(self.cfg); config['seeds'] = seeds
            (run_dir/'config.json').write_text(json.dumps(config), encoding='utf-8')
            summary = []; keys = []; sample = []; reviews = []
            for seed in seeds:
                for policy in ('balanced','novelty','recognition'):
                    for network in ('closed','open'):
                        summary.append({'seed':seed,'policy':policy,'network':network,'total_effort_units':100})
                        for output_type in ('solo','team'):
                            review_id = f'rvw_{seed}_{policy}_{network}_{output_type}'
                            keys.append({'review_id':review_id,'seed':seed,'policy':policy,'network':network,'output_type':output_type})
                            sample.append({'review_id':review_id,'N_h':1,'n_h':1})
                            score = {'balanced':3,'novelty':5,'recognition':2}[policy]
                            reviews.extend([self.review_row(review_id,'a',score),self.review_row(review_id,'b',score)])
            write_csv(run_dir/'summary.csv', summary)
            write_csv(review_dir/'review_private'/'review_key.csv', keys)
            write_csv(review_dir/'review_private'/'sample_manifest.csv', sample)
            write_csv(review_dir/'review_results'/'validated_reviews.csv', reviews)
            result = analyze_reviews(run_dir, review_dir, output)
            self.assertEqual(result['status'], 'completed')
            effects = read_csv(output/'quality_paired_effects.csv')
            sensitivity = read_csv(output/'quality_weight_sensitivity.csv')
            by_type = read_csv(output/'quality_by_output_type.csv')
            self.assertEqual(len(effects), 18); self.assertTrue(all(row['status'] == 'complete' for row in effects))
            self.assertEqual(len(sensitivity), 15); self.assertTrue(all(row['estimated_quality_total'] for row in sensitivity))
            self.assertEqual(len(by_type), 2); self.assertTrue(all(row['novelty_mean'] for row in by_type))

    # 验证评分缺失时配对效应保留样本量但不输出伪完整均值和区间。
    def test_quality_paired_effects_incomplete(self):
        rows = [{'seed':1,'policy':'balanced','network':'closed','estimated_mean_quality':.5,
                 'estimated_quality_per_100_effort':1.0}]
        effects = paired_quality_effects(rows, [1,2], 3, 20)
        self.assertTrue(effects)
        self.assertTrue(all(row['status'] == 'incomplete_external_reviews' for row in effects))
        self.assertTrue(all(row['mean_difference'] is None for row in effects))

    # 验证分层质量估计、全抽退化、缺失边界和零总体行为。
    def test_stratified_quality_estimator(self):
        complete = estimate_stratum_quality(10, [.2,.4])
        self.assertAlmostEqual(complete['estimated_total'], 3.0)
        census = estimate_stratum_quality(2, [.2,.4])
        self.assertAlmostEqual(census['estimated_total'], .6)
        missing = estimate_stratum_quality(10, [.2,None])
        self.assertIsNone(missing['estimated_total'])
        self.assertEqual((missing['lower_bound'],missing['upper_bound']), (1.0,6.0))
        self.assertEqual(estimate_stratum_quality(0, [])['estimated_total'], 0.0)

    # 验证CSV读回的seed字符串可在质量分析前规范化为整数。
    def test_csv_seed_normalization_contract(self):
        summary_key = (100, 'balanced', 'closed')
        review_key = condition_key('100', 'balanced', 'closed')
        self.assertEqual(summary_key, review_key)

    # 验证mock评分明确标为validation_only且不能伪装正式外评。
    def test_mock_review_is_validation_only(self):
        rows = mock_review_records([{'review_id':'rvw_fixture','reference_ids':['e1']}])
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['is_mock'] and row['validation_only'] for row in rows))


if __name__ == '__main__':
    unittest.main()
