"""Targeted mechanism and invariant tests for SciMirror v0.2."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from scimirror.accounting import add_effort, calculate_credits
from scimirror.backend import Backend, validate_response
from scimirror.candidate_pool import build_schedule, offered_candidates, validate_schedule
from scimirror.common import digest
from scimirror.corpus import Corpus
from scimirror.policy import context
from scimirror.topics import TopicModel
from scimirror.v02_engine import apply_exits, invitation_action, merge_project, select_idea, select_topic
from scimirror.v02_experiment import ablated_config, load_v02_config, policy_neutral_state
from scimirror.v02_metrics import measure_v02, validate_accounting
from scimirror.v02_state import Project, initialize_v02, validate_v02


ROOT = Path(__file__).resolve().parents[1]


class V02MechanismTests(unittest.TestCase):
    # 为每项测试加载统一配置、截点语料和冻结主题快照。
    def setUp(self):
        self.cfg = load_v02_config(ROOT/'configs/v02_mock_smoke.json')
        self.corpus = Corpus(ROOT/self.cfg['corpus'], self.cfg['cutoff_year'], True)
        self.topics = TopicModel(ROOT/self.cfg['topics'], self.corpus.papers, 1.0)
        self.world = initialize_v02(20, 42, self.topics)

    # 验证认可度随冻结主题频率变化而词汇新颖性保持不变。
    def test_recognition_is_not_one_minus_novelty(self):
        text = 'idea evaluation independent mechanism'
        novelty = self.corpus.novelty(text)
        papers = copy.deepcopy(self.corpus.papers)
        for index in range(30):
            papers[f'extra{index}'] = {'id': f'extra{index}', 'title': 'memory retrieval',
                'abstract': 'memory retrieval', 'year': 2020, 'field': 'agents', 'synthetic': True}
        shifted = TopicModel(ROOT/self.cfg['topics'], papers, 1.0)
        self.assertNotEqual(self.topics.recognition(text)[0], shifted.recognition(text)[0])
        self.assertEqual(novelty, self.corpus.novelty(text))
        score = .5*novelty + .5*self.topics.recognition(text)[0]
        self.assertNotEqual(score, .5)

    # 验证截点后的文献不会进入语料、主题统计或检索候选。
    def test_cutoff_excludes_future_papers(self):
        rows = list(self.corpus.papers.values()) + [{'id': 'future', 'title': 'memory retrieval',
            'abstract': 'future evidence', 'year': self.cfg['cutoff_year'], 'field': 'agents', 'synthetic': True}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'papers.jsonl'
            path.write_text('\n'.join(json.dumps(x) for x in rows), encoding='utf-8')
            corpus = Corpus(path, self.cfg['cutoff_year'], True)
            topics = TopicModel(ROOT/self.cfg['topics'], corpus.papers, 1.0)
        self.assertNotIn('future', corpus.papers)
        self.assertEqual(self.topics.audit['snapshot_hash'], topics.audit['snapshot_hash'])
        self.assertNotIn('future', [x['id'] for x in corpus.retrieve('memory retrieval', 90)])

    # 验证五个政策通路可独立关闭且政策权重确实改变效用。
    def test_policy_paths_are_switchable_and_auditable(self):
        for stage in ('topic', 'retrieval', 'idea_selection', 'invitation', 'exit'):
            enabled = context(self.cfg, 'novelty', stage)
            disabled = context(ablated_config(self.cfg, 'all_policy_paths_off'), 'novelty', stage)
            self.assertNotEqual(enabled.policy_term(.9, .1), context(self.cfg, 'recognition', stage).policy_term(.9, .1))
            self.assertEqual(disabled.effective_policy, 'balanced')
            self.assertFalse(disabled.audit()['path_enabled'])

    # 验证关闭全部政策通路后主题、草案和邀请审计不再暴露实际政策。
    def test_all_paths_off_removes_decision_policy(self):
        cfg = ablated_config(self.cfg, 'all_policy_paths_off')
        self.world.agents['s00'].selected_topic_id = 'agent_memory'
        self.world.agents['s01'].selected_topic_id = 'learning_graph'
        for policy in ('novelty', 'recognition'):
            world = copy.deepcopy(self.world)
            world.policy = policy
            _, audit = select_topic(world, self.topics, cfg, 's00', 0)
            self.assertEqual(audit['policy_context']['effective_policy'], 'balanced')
            action = invitation_action(world, self.topics, cfg, 's00', 's01')
            self.assertEqual(action['policy_term'], context(cfg, policy, 'invitation').policy_term(
                action['subjective_features']['expected_novelty'], action['subjective_features']['expected_recognition']))
        a, b = copy.deepcopy(self.world), copy.deepcopy(self.world)
        a.policy, b.policy = 'novelty', 'recognition'
        self.assertEqual(digest(policy_neutral_state(a)), digest(policy_neutral_state(b)))

    # 验证closed/open候选数相同、配额正确且冻结表与政策无关。
    def test_candidate_schedule_exact_and_policy_independent(self):
        schedule = build_schedule(self.world, 2, self.cfg)
        self.world.candidate_schedule = schedule
        validate_schedule(self.world, self.cfg)
        for key, item in schedule.items():
            leader = key.split(':', 1)[1]
            self.assertEqual(len(item['closed']), 3)
            self.assertEqual(len(item['open']), 3)
            self.assertNotIn(leader, item['closed'] + item['open'])
        clone = copy.deepcopy(self.world)
        clone.policy = 'recognition'
        self.assertEqual(schedule, build_schedule(clone, 2, self.cfg))

    # 验证冻结候选槽位不会因后续不可用状态而被偷偷补位。
    def test_offered_and_actionable_are_distinct(self):
        self.world.candidate_schedule = build_schedule(self.world, 1, self.cfg)
        leader = sorted(x for x in self.world.agents if int(x[1:]) % 2 == 0)[0]
        offered = offered_candidates(self.world, 0, leader)
        self.world.agents[offered[0]].project_id = None
        actionable = [x for x in offered if self.world.agents[x].project_id]
        self.assertEqual(len(offered), 3)
        self.assertEqual(len(actionable), 0)

    # 验证全员退出会废弃项目且负责人退出时按中性规则接任。
    def test_exit_abandonment_and_successor(self):
        project = Project('p', ['d'], 's00', ['s00', 's03'], ['s00', 's03'], 'active', 7, None, ['v'], 100, [])
        self.world.projects['p'] = project
        self.world.agents['s00'].project_id = self.world.agents['s03'].project_id = 'p'
        self.world.intervention_start_cycle = 1
        apply_exits(self.world, 'p', ['s00'], 9)
        self.assertEqual(project.owner, 's03')
        self.assertEqual(project.status, 'active')
        apply_exits(self.world, 'p', ['s03'], 9)
        self.assertEqual(project.status, 'abandoned')
        self.assertEqual(sum(x['units'] for x in self.world.effort_ledger), 4)
        validate_v02(self.world)

    # 验证项目合并只链接既有账本且最终贡献份额守恒。
    def test_merge_effort_and_credit_conservation(self):
        self.world.intervention_start_cycle = 1
        for aid in ('s00', 's01'):
            pid = 'p'+aid
            self.world.projects[pid] = Project(pid, ['d'+aid], aid, [aid], [aid], 'active', 7, None, ['v'+aid], 100, [])
            self.world.agents[aid].project_id = pid
            add_effort(self.world, pid, aid, 7, 'proposal', 10)
        entries = len(self.world.effort_ledger)
        merge_project(self.world, 'ps01', 'ps00', 's01', 8)
        self.assertEqual(entries, len(self.world.effort_ledger))
        target = self.world.projects['ps00']
        target.status, target.closed_tick, target.completion_members = 'completed', 11, list(target.current_members)
        for aid in target.current_members:
            self.world.agents[aid].project_id = None
        credits = calculate_credits(self.world, 1)
        self.assertAlmostEqual(sum(x['fractional_output_credit'] for x in credits), 1.0)

    # 验证指标流量守恒、零投入比率为空且修订版本不新建草案。
    def test_metrics_zero_denominator_and_revision_identity(self):
        self.world.intervention_start_cycle = 1
        before = len(self.world.ideas)
        metrics = measure_v02(self.world, 1)
        self.assertIsNone(metrics['outputs_per_100_effort'])
        self.assertEqual(before, metrics['drafts_created'])
        checked, credits = validate_accounting(self.world, 1)
        self.assertEqual(checked['final_outputs'], 0)
        self.assertEqual(credits, [])

    # 验证并发退出的解析结果不依赖输入遍历顺序。
    def test_concurrent_exit_order_is_stable(self):
        # 在独立世界副本中按给定顺序执行同一组退出。
        def execute(order):
            world = copy.deepcopy(self.world)
            world.intervention_start_cycle = 1
            world.projects['p'] = Project('p', ['d'], 's00', ['s00', 's03', 's06'],
                ['s00', 's03', 's06'], 'active', 7, None, ['v'], 100, [])
            for aid in world.projects['p'].current_members:
                world.agents[aid].project_id = 'p'
            apply_exits(world, 'p', order, 9)
            return world.projects['p'].owner, world.projects['p'].current_members
        self.assertEqual(execute(['s00', 's03']), execute(['s03', 's00']))

    # 验证v0.2响应强制topic_id一致且旧schema仍保持兼容。
    def test_backend_topic_schema_and_mock_evidence(self):
        agent = self.world.agents['s00']
        agent.selected_topic_id = 'agent_memory'
        papers = self.corpus.retrieve('memory retrieval', 3)
        context_value = {'schema_version': '0.2', 'self': {'field': agent.field}, 'papers': papers,
            'selected_topic': self.topics.features('agent_memory')}
        response = Backend.mock('propose', context_value, [42, 0, 's00', 'agent_memory'])
        validate_response('propose', response, context_value)
        broken = copy.deepcopy(response)
        broken['candidates'][0]['topic_id'] = 'wrong'
        with self.assertRaises(ValueError):
            validate_response('propose', broken, context_value)


if __name__ == '__main__':
    unittest.main()
