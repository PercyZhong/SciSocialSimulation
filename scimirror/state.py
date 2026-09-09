from dataclasses import dataclass, field, asdict
from .common import rng

FIELDS = ['agents', 'learning', 'science']


@dataclass
class Agent:
    id: str
    field: str
    values: dict
    reputation: float = 0.5
    pressure: float = 0.2
    energy: int = 100
    team: str | None = None
    trust: dict = field(default_factory=dict)
    memories: list = field(default_factory=list)
    read_ids: list = field(default_factory=list)


@dataclass
class World:
    seed: int
    tick: int
    agents: dict
    ideas: dict = field(default_factory=dict)
    teams: dict = field(default_factory=dict)
    invitations: list = field(default_factory=list)
    policy: str = 'balanced'
    network: str = 'open'
    history: list = field(default_factory=list)

    def export(self):
        return asdict(self)

    @classmethod
    def restore(cls, raw):
        data = dict(raw)
        data['agents'] = {k: Agent(**v) for k, v in data['agents'].items()}
        return cls(**data)


def initialize(n, seed):
    agents = {}
    for i in range(n):
        aid = f's{i:02d}'
        r = rng(seed, 'profile', aid)
        agents[aid] = Agent(aid, FIELDS[i % len(FIELDS)],
                            {k: r.uniform(.2, .8) for k in ['novelty', 'reputation', 'risk', 'cooperation']})
    return World(seed, 0, agents)


def validate_world(w):
    seen = set()
    for tid, team in w.teams.items():
        assert 1 <= len(team['members']) <= 2, 'team capacity'
        assert team['leader'] in team['members']
        for aid in team['members']:
            assert aid not in seen, 'multiple simultaneous teams'
            seen.add(aid)
            assert w.agents[aid].team == tid
    for aid, a in w.agents.items():
        assert 0 <= a.energy <= 100
        assert all(0 <= v <= 1 for v in a.values.values())
        assert 0 <= a.pressure <= 1 and 0 <= a.reputation <= 1
        assert a.team is None or aid in w.teams[a.team]['members']
    for idea in w.ideas.values():
        assert idea['owner'] in w.agents and idea['versions']
