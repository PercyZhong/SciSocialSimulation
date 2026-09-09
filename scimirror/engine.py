"""Six phases per project cycle; each tick observes a frozen snapshot."""
import copy
import math
from dataclasses import asdict
from .common import rng, clip
from .state import validate_world
from .backend import validate_response

POLICIES = {'balanced': (.5, .5), 'novelty': (1., 0.), 'recognition': (0., 1.)}


def event(kind, actor, payload, audience=None):
    return {'type': kind, 'actor': actor, 'audience': audience or [], 'payload': payload}


def observation(world, aid, corpus):
    a = world.agents[aid]
    visible = [asdict(a)]  # Only the focal agent's private fields may enter context.
    peers = [{'id': b.id, 'field': b.field, 'reputation': b.reputation,
              'available': b.team is None} for b in world.agents.values() if b.id != aid]
    return {'self': visible[0], 'peers': peers, 'papers': corpus.retrieve(a.field),
            'policy': world.policy, 'tick': world.tick}


def utility(a, candidate, policy):
    n, f, r = (candidate[k] for k in ['expected_novelty', 'expected_feasibility', 'expected_recognition'])
    wn, wr = POLICIES[policy]
    return (a.values['novelty']*n + a.values['reputation']*r
            - a.values['risk']*(1-f) + .5*(wn*n+wr*r))


def select(a, candidates, policy, random_source, temperature):
    scores = [utility(a, c, policy) for c in candidates]
    weights = [math.exp((s-max(scores))/temperature) for s in scores]
    return random_source.choices(range(len(candidates)), weights=weights)[0], scores


def step(w, corpus, backend, cfg):
    """Work on a copy; failed LLM/schema calls cannot commit partial world state."""
    old = w
    w = copy.deepcopy(old)
    tick, phase = old.tick, old.tick % 6
    cycle = tick // 6
    events = []
    for a in w.agents.values():
        a.energy = min(100, a.energy+8)
    if phase == 0:
        w.teams = {}
        w.invitations = []
        for a in w.agents.values():
            a.team = None
        for aid in old.agents:
            a = w.agents[aid]
            ctx = observation(old, aid, corpus)
            response = backend.generate('propose', ctx, [w.seed, tick, aid])
            validate_response('propose', response, ctx)
            cs = response['candidates']
            chosen, scores = select(a, cs, w.policy, rng(w.seed, tick, aid, 'choice'), cfg['decision_temperature'])
            card = cs[chosen]
            iid = f'idea_{cycle}_{aid}'
            w.ideas[iid] = {'id': iid, 'owner': aid, 'cycle': cycle, 'field': a.field,
                            'versions': [copy.deepcopy(card)], 'status': 'draft', 'contributors': [aid]}
            a.read_ids = sorted(set(a.read_ids) | {p['id'] for p in ctx['papers']})
            a.energy -= 20
            events.append(event('knowledge.paper.read', aid, {'paper_ids': [p['id'] for p in ctx['papers']]}, [aid]))
            events.append(event('decision.recorded', aid, {'candidates': cs, 'utilities': scores, 'selected': chosen}, [aid]))
            events.append(event('knowledge.idea.created', aid, {'idea_id': iid, 'version': 1}, [aid]))
    elif phase == 1:
        # Leaders alternate each cycle so the same scientists are not always leaders.
        ids = sorted(old.agents)
        leaders = [aid for i, aid in enumerate(ids) if (i+cycle) % 2 == 0]
        followers = [aid for aid in ids if aid not in leaders]
        for aid in leaders:
            a = w.agents[aid]
            tid = f'team_{cycle}_{aid}'
            w.teams[tid] = {'leader': aid, 'members': [aid], 'idea': f'idea_{cycle}_{aid}'}
            a.team = tid
            candidates = [bid for bid in followers if w.network == 'open' or old.agents[bid].field == a.field]
            if not candidates:
                continue
            weights = [.2 + a.trust.get(bid, .5) + .3*(old.agents[bid].field != a.field)
                       for bid in candidates]
            target = rng(w.seed, tick, aid, 'invite').choices(candidates, weights)[0]
            inv = {'id': f'inv_{cycle}_{aid}', 'sender': aid, 'target': target, 'team': tid, 'status': 'pending'}
            w.invitations.append(inv)
            events.append(event('collaboration.invitation.created', aid, inv.copy(), [aid, target]))
    elif phase == 2:
        # Every target chooses at most one invitation on the same pre-tick state.
        intents = []
        targets = sorted({x['target'] for x in old.invitations})
        for target in targets:
            a = old.agents[target]
            choices = [x for x in old.invitations if x['target'] == target and x['status'] == 'pending']
            chosen = max(choices, key=lambda x: (a.trust.get(x['sender'], .5),
                          rng(w.seed, tick, target, x['sender'], 'tie').random()))
            willing = (a.energy >= 15 and a.team is None and
                       rng(w.seed, tick, target, 'accept').random() < .3+.65*a.values['cooperation'])
            intents.append((target, chosen['id'] if willing else None))
        for target, accepted in intents:
            for inv in w.invitations:
                if inv['target'] != target:
                    continue
                inv['status'] = 'accepted' if inv['id'] == accepted else 'rejected'
                if inv['status'] == 'accepted':
                    a = w.agents[target]
                    team = w.teams[inv['team']]
                    if a.team is not None or len(team['members']) >= 2:
                        raise AssertionError('Capacity invariant violated')
                    team['members'].append(target)
                    a.team = inv['team']
                    a.energy -= 15
                events.append(event('collaboration.invitation.'+inv['status'], target, inv.copy(), [target, inv['sender']]))
    elif phase == 3:
        for tid, team in old.teams.items():
            if len(team['members']) == 2:
                for aid in team['members']:
                    iid = f'idea_{cycle}_{aid}'
                    events.append(event('knowledge.idea.shared', aid, {'idea_id': iid, 'card': old.ideas[iid]['versions'][-1]}, team['members']))
    elif phase == 4:
        # Revise leader card only. Paired member drafts remain measurable but not final outputs.
        for tid, team in old.teams.items():
            leader = team['leader']
            members = team['members']
            iid = team['idea']
            ctx = {'self': {'id': leader, 'field': old.agents[leader].field},
                   'idea': old.ideas[iid]['versions'][-1],
                   'shared_cards': [old.ideas[f'idea_{cycle}_{a}']['versions'][-1] for a in members if a != leader]}
            reply = backend.generate('revise', ctx, [w.seed, tick, leader])
            validate_response('revise', reply, ctx)
            version = copy.deepcopy(w.ideas[iid]['versions'][-1])
            version.update(hypothesis=reply['revised_hypothesis'], method=reply['revised_method'])
            w.ideas[iid]['versions'].append(version)
            w.ideas[iid]['contributors'] = members.copy()
            for aid in members:
                w.agents[aid].energy -= 10
            events.append(event('communication.critique.provided', leader, {'idea_id': iid, 'text': reply['critique']}, members))
            events.append(event('knowledge.idea.revised', leader, {'idea_id': iid, 'version': len(w.ideas[iid]['versions'])}, members))
    else:
        # Only final team lead cards + unaffiliated scientists enter final metrics.
        for iid, idea in w.ideas.items():
            if idea['cycle'] != cycle:
                continue
            owner = w.agents[idea['owner']]
            if owner.team and w.teams[owner.team]['leader'] != owner.id:
                idea['status'] = 'contributed'
                continue
            card = idea['versions'][-1]
            text = card['title']+' '+card['hypothesis']+' '+card['method']
            novelty = corpus.novelty(text)
            # Recognition is a lexical familiarity proxy, NOT real citations or acceptance.
            recognition = 1-novelty
            wn, wr = POLICIES[w.policy]
            reward = wn*novelty+wr*recognition
            idea.update(status='evaluated', novelty_proxy=novelty, recognition_proxy=recognition, reward=reward)
            events.append(event('evaluation.completed', 'system', {'idea_id': iid, 'novelty_proxy': novelty,
                                  'recognition_proxy': recognition, 'institutional_reward': reward}, idea['contributors']))
            for aid in idea['contributors']:
                a = w.agents[aid]
                a.reputation = clip(a.reputation+.05*(reward-.5))
                a.pressure = clip(a.pressure+.03*(.5-reward))
                # Values fixed by default: dynamic learning would add a confounded mechanism.
                if cfg['dynamic_values']:
                    a.values['risk'] = clip(a.values['risk']+.02*(.5-reward))
                a.memories = (a.memories+[{'tick': tick, 'idea_id': iid, 'reward': reward}])[-12:]
                for bid in idea['contributors']:
                    if aid != bid:
                        a.trust[bid] = clip(a.trust.get(bid, .5)+.02*(reward-.5))
        w.history.append({'cycle': cycle, 'tick': tick})
    w.tick += 1
    validate_world(w)
    return w, events
