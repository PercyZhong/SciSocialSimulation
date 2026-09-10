"""Versioned Agent, Project and World state for SciMirror v0.2."""
from dataclasses import asdict, dataclass, field

from .common import rng


FIELDS = ['agents', 'learning', 'science']


@dataclass
class AgentV02:
    id: str
    field: str
    values: dict
    simulated_reputation: float = .5
    pressure: float = .2
    energy: int = 100
    project_id: str | None = None
    trust: dict = field(default_factory=dict)
    memories: list = field(default_factory=list)
    read_ids: list = field(default_factory=list)
    read_topic_ids: list = field(default_factory=list)
    public_topics: list = field(default_factory=list)
    selected_topic_id: str | None = None
    topic_choice_history: list = field(default_factory=list)
    last_exit_cycle: int | None = None


@dataclass
class Project:
    id: str
    origin_draft_ids: list
    owner: str
    current_members: list
    historical_contributors: list
    status: str
    created_tick: int
    closed_tick: int | None
    version_ids: list
    remaining_budget: int
    effort_ledger: list
    merged_into: str | None = None
    completion_members: list = field(default_factory=list)


@dataclass
class WorldV02:
    seed: int
    tick: int
    agents: dict
    ideas: dict = field(default_factory=dict)
    projects: dict = field(default_factory=dict)
    invitations: list = field(default_factory=list)
    invitation_history: list = field(default_factory=list)
    candidate_schedule: dict = field(default_factory=dict)
    policy: str = 'balanced'
    network: str = 'open'
    policy_paths: dict = field(default_factory=dict)
    effort_ledger: list = field(default_factory=list)
    decision_audit: list = field(default_factory=list)
    history: list = field(default_factory=list)
    intervention_start_cycle: int = 1

    # 将v0.2世界状态及嵌套实体导出为普通字典。
    def export(self):
        return asdict(self)

    # 从快照字典恢复v0.2世界、Agent和Project实体。
    @classmethod
    def restore(cls, raw):
        data = dict(raw)
        data['agents'] = {k: AgentV02(**v) for k, v in data['agents'].items()}
        data['projects'] = {k: Project(**v) for k, v in data.get('projects', {}).items()}
        return cls(**data)


# 初始化领域均衡且偏好不与领域机械绑定的v0.2 Agent总体。
def initialize_v02(n, seed, topic_model):
    agents = {}
    topics_by_field = {f: sorted(t for t, v in topic_model.topics.items() if v['field'] == f) for f in FIELDS}
    for i in range(n):
        aid = f's{i:02d}'
        field_name = FIELDS[i % len(FIELDS)]
        random_source = rng(seed, 'v02_profile', aid)
        values = {k: random_source.uniform(.2, .8) for k in ['novelty', 'reputation', 'risk', 'cooperation']}
        public_topics = random_source.sample(topics_by_field[field_name], k=min(2, len(topics_by_field[field_name])))
        agents[aid] = AgentV02(aid, field_name, values, public_topics=sorted(public_topics))
    return WorldV02(seed, 0, agents)


# 用显式异常检查v0.2成员、项目状态、账本和资源等关键不变量。
def validate_v02(world):
    memberships = {}
    valid_status = {'active', 'completed', 'abandoned', 'merged', 'censored'}
    for project in world.projects.values():
        if project.status not in valid_status or len(project.current_members) != len(set(project.current_members)):
            raise ValueError('Invalid project status or duplicate member')
        if project.owner not in project.historical_contributors or project.remaining_budget < 0:
            raise ValueError('Invalid project owner or budget')
        if not set(project.current_members) <= set(project.historical_contributors):
            raise ValueError('Current member missing from historical contributors')
        if project.status == 'active' and not project.current_members:
            raise ValueError('Active project has no members')
        if project.status == 'merged' and not project.merged_into:
            raise ValueError('Merged project missing target')
        if project.status == 'active':
            for aid in project.current_members:
                if aid in memberships:
                    raise ValueError('Agent belongs to multiple active projects')
                memberships[aid] = project.id
    for aid, agent in world.agents.items():
        if not 0 <= agent.energy <= 100 or not 0 <= agent.pressure <= 1 or not 0 <= agent.simulated_reputation <= 1:
            raise ValueError('Agent bounded state violated')
        if agent.project_id != memberships.get(aid):
            raise ValueError('Agent/project membership mismatch')
    ledger_ids = [entry['entry_id'] for entry in world.effort_ledger]
    if len(ledger_ids) != len(set(ledger_ids)) or any(entry['units'] <= 0 for entry in world.effort_ledger):
        raise ValueError('Effort ledger must have unique positive entries')
