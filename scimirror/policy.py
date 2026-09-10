"""Auditable policy contexts and stable discrete decisions for v0.2."""
import math
from dataclasses import dataclass


WEIGHTS = {'balanced': (.5, .5), 'novelty': (1.0, 0.0), 'recognition': (0.0, 1.0)}


@dataclass(frozen=True)
class PolicyContext:
    policy: str
    stage: str
    enabled: bool
    lambda_stage: float

    # 返回本阶段实际使用的政策；关闭通路时固定为balanced。
    @property
    def effective_policy(self):
        return self.policy if self.enabled else 'balanced'

    # 返回本阶段实际使用的新颖性与认可权重。
    @property
    def weights(self):
        return WEIGHTS[self.effective_policy]

    # 计算规范化主观特征对应的制度效用项。
    def policy_term(self, novelty, recognition):
        wn, wr = self.weights
        return self.lambda_stage * (wn*novelty + wr*recognition)

    # 导出不会泄漏已关闭实际政策标签的审计上下文。
    def audit(self):
        return {'stage': self.stage, 'effective_policy': self.effective_policy,
                'path_enabled': self.enabled, 'lambda_stage': self.lambda_stage}


# 根据配置构造指定决策阶段的可消融政策上下文。
def context(cfg, policy, stage):
    return PolicyContext(policy, stage, bool(cfg['policy_paths'].get(stage, False)), float(cfg['stage_lambda']))


# 使用数值稳定softmax计算候选效用对应的选择概率。
def softmax(values, temperature):
    if temperature <= 0 or not values:
        raise ValueError('Positive temperature and nonempty values required')
    top = max(values)
    weights = [math.exp((v-top)/temperature) for v in values]
    total = sum(weights)
    return [w/total for w in weights]


# 按稳定顺序和给定随机流选择行动并生成完整决策审计记录。
def choose(agent_id, tick, stage, actions, random_source, policy_context, random_key):
    actions = sorted(actions, key=lambda a: a['candidate_id'])
    if not actions:
        return None, {'agent': agent_id, 'tick': tick, 'stage': stage, 'candidates': [],
                      'selected_action': 'no_action', 'policy_context': policy_context.audit(),
                      'random_stream_key': list(random_key), 'evidence_ids': []}
    totals = [a['individual_utility'] + a['policy_term'] - a['cost'] for a in actions]
    probabilities = softmax(totals, actions[0].get('temperature', .2))
    index = random_source.choices(range(len(actions)), weights=probabilities)[0]
    candidates = []
    for action, total, probability in zip(actions, totals, probabilities):
        candidates.append({**action, 'total_utility': total, 'probability': probability})
    selected = actions[index]['candidate_id']
    evidence = sorted({e for a in actions for e in a.get('evidence_ids', [])})
    return selected, {'agent': agent_id, 'tick': tick, 'stage': stage, 'candidates': candidates,
                      'selected_action': selected, 'policy_context': policy_context.audit(),
                      'random_stream_key': list(random_key), 'evidence_ids': evidence}


