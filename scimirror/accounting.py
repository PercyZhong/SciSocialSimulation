"""Effort accounting and conserved authorship credit for v0.2."""
from collections import defaultdict


# 向世界和目标项目追加一条不重复的模拟投入记录。
def add_effort(world, project_id, agent_id, tick, action, units):
    entry_id = f'effort:{world.seed}:{tick}:{project_id}:{agent_id}:{action}'
    if any(x['entry_id'] == entry_id for x in world.effort_ledger):
        raise ValueError('Duplicate effort entry')
    entry = {'entry_id': entry_id, 'project_id': project_id, 'agent_id': agent_id,
             'tick': tick, 'cycle': tick//6, 'action': action, 'units': units,
             'period': 'intervention' if tick//6 >= world.intervention_start_cycle else 'prefix'}
    world.effort_ledger.append(entry)
    world.projects[project_id].effort_ledger.append(entry_id)
    world.projects[project_id].remaining_budget = max(0, world.projects[project_id].remaining_budget-units)


# 按每个完成项目的历史正投入比例计算守恒的Agent成果份额。
def calculate_credits(world, start_cycle):
    rows = []
    completed = [p for p in world.projects.values() if p.status == 'completed' and p.created_tick//6 >= start_cycle]
    effort = defaultdict(float)
    for entry in world.effort_ledger:
        if entry['period'] == 'intervention':
            target = entry['project_id']
            seen = set()
            while world.projects[target].status == 'merged':
                if target in seen:
                    raise ValueError('Project merge cycle')
                seen.add(target)
                target = world.projects[target].merged_into
            effort[(target, entry['agent_id'])] += entry['units']
    for project in completed:
        positive = {aid: effort[(project.id, aid)] for aid in project.historical_contributors if effort[(project.id, aid)] > 0}
        total = sum(positive.values())
        if total <= 0:
            raise ValueError('Completed project has zero effort')
        for aid, units in sorted(positive.items()):
            rows.append({'seed': world.seed, 'project_id': project.id, 'agent_id': aid,
                         'effort_units': units, 'fractional_output_credit': units/total})
    return rows

