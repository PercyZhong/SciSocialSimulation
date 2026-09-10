"""Policy-independent fixed-size collaboration candidate schedules for v0.2."""
from collections import Counter

from .common import digest, rng


# 生成每个seed、周期、负责人在closed/open条件下的冻结候选日程。
def build_schedule(world, cycles, cfg):
    requested = cfg['candidate_pool']['size']
    same_quota = cfg['candidate_pool']['open_same_field']
    other_quota = cfg['candidate_pool']['open_other_field']
    if same_quota + other_quota != requested:
        raise ValueError('Open candidate quotas must sum to candidate pool size')
    schedule = {}
    ids = sorted(world.agents)
    for cycle in range(cycles):
        leaders = [aid for i, aid in enumerate(ids) if (i+cycle) % 2 == 0]
        followers = [aid for aid in ids if aid not in leaders]
        for leader in leaders:
            field_name = world.agents[leader].field
            same = [x for x in followers if world.agents[x].field == field_name]
            other = [x for x in followers if world.agents[x].field != field_name]
            feasible = min(requested, len(same), len(followers))
            if feasible < requested:
                raise ValueError(f'Candidate K={requested} infeasible for standard schedule at cycle={cycle}, leader={leader}')
            if len(same) < same_quota or len(other) < other_quota:
                raise ValueError('Open cross-field quota infeasible')
            order = lambda aid, kind: digest([world.seed, cycle, leader, kind, aid])
            closed = sorted(same, key=lambda x: order(x, 'same'))[:requested]
            open_same = sorted(same, key=lambda x: order(x, 'same'))[:same_quota]
            open_other = sorted(other, key=lambda x: order(x, 'other'))[:other_quota]
            opened = sorted(open_same + open_other)
            key = f'{cycle}:{leader}'
            schedule[key] = {'closed': closed, 'open': opened, 'size': requested,
                             'source_hash': digest({'seed': world.seed, 'cycle': cycle, 'leader': leader,
                                                    'followers': followers, 'requested': requested})}
    return schedule


# 检查冻结日程的候选数、配额、唯一性、自身排除和政策无关性。
def validate_schedule(world, cfg):
    requested = cfg['candidate_pool']['size']
    for key, entry in world.candidate_schedule.items():
        _, leader = key.split(':', 1)
        closed, opened = entry['closed'], entry['open']
        if len(closed) != requested or len(opened) != requested:
            raise ValueError('Candidate count mismatch')
        if len(set(closed)) != requested or len(set(opened)) != requested or leader in closed or leader in opened:
            raise ValueError('Duplicate or self candidate')
        leader_field = world.agents[leader].field
        if any(world.agents[x].field != leader_field for x in closed):
            raise ValueError('Closed list contains other field')
        counts = Counter(world.agents[x].field == leader_field for x in opened)
        if counts[True] != cfg['candidate_pool']['open_same_field'] or counts[False] != cfg['candidate_pool']['open_other_field']:
            raise ValueError('Open list quota mismatch')


# 返回某负责人当前网络条件下的冻结候选列表。
def offered_candidates(world, cycle, leader):
    return list(world.candidate_schedule[f'{cycle}:{leader}'][world.network])


