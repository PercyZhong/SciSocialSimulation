"""Six-stage SciMirror v0.2 engine with auditable policy paths and projects."""
import copy
import math
from dataclasses import asdict

from .accounting import add_effort
from .candidate_pool import offered_candidates
from .common import clip, digest, rng
from .policy import WEIGHTS, choose, context
from .v02_state import Project, validate_v02
from .backend import validate_response


# 构造一条v0.2领域事件。
def event(kind, actor, payload, audience=None):
    return {'type': kind, 'actor': actor, 'audience': audience or [], 'payload': payload}


# 返回某周期按固定轮换规则确定的负责人和候选合作者。
def cycle_roles(world, cycle):
    ids = sorted(world.agents)
    leaders = [aid for i, aid in enumerate(ids) if (i+cycle) % 2 == 0]
    return leaders, [aid for aid in ids if aid not in leaders]


# 构造不依赖实际policy的固定主题候选集合。
def topic_candidates(world, topic_model, aid, cycle, count):
    agent = world.agents[aid]
    own = sorted(t for t, v in topic_model.topics.items() if v['field'] == agent.field)
    other = sorted(t for t, v in topic_model.topics.items() if v['field'] != agent.field)
    own = sorted(own, key=lambda t: digest([world.seed, cycle, aid, 'topic-own', t]))
    other = sorted(other, key=lambda t: digest([world.seed, cycle, aid, 'topic-other', t]))
    candidates = own[:1] + other[:max(0, count-1)]
    if len(candidates) < count:
        candidates += [t for t in own[1:] if t not in candidates][:count-len(candidates)]
    return sorted(candidates)


# 为Agent选择主题并返回可审计的效用分解。
def select_topic(world, topic_model, cfg, aid, cycle):
    agent = world.agents[aid]
    policy_context = context(cfg, world.policy, 'topic')
    candidates = topic_candidates(world, topic_model, aid, cycle, cfg['topic_candidates'])
    actions = []
    for topic_id in candidates:
        known = topic_id in agent.public_topics
        novelty = .2 if known else .8
        recognition = topic_model.attention[topic_id]
        individual = .6*(1.0 if topic_model.topics[topic_id]['field'] == agent.field else .4) + .4*(1-novelty)
        actions.append({'candidate_id': topic_id, 'subjective_features': {'expected_novelty': novelty,
                        'expected_recognition': recognition, 'familiarity': 1-novelty},
                        'individual_utility': individual, 'policy_term': policy_context.policy_term(novelty, recognition),
                        'cost': 0.0, 'temperature': cfg['decision_temperature'], 'evidence_ids': agent.public_topics})
    key = [world.seed, world.tick, aid, 'topic-choice']
    return choose(aid, world.tick, 'topic', actions, rng(*key), policy_context, key)


# 构建仅含本Agent私有状态、同伴公开投影和所选证据的模型上下文。
def proposal_observation(world, aid, papers, topic_model, effective_policy):
    agent = world.agents[aid]
    peers = [{'id': b.id, 'field': b.field, 'simulated_reputation': b.simulated_reputation,
              'public_topics': b.public_topics, 'available': b.project_id is None}
             for b in world.agents.values() if b.id != aid]
    return {'schema_version': '0.2', 'self': asdict(agent), 'peers': peers, 'papers': papers,
            'selected_topic': topic_model.features(agent.selected_topic_id),
            'policy': effective_policy, 'tick': world.tick}


# 在两个生成候选中按独立主观特征和idea-selection通路选择草案。
def select_idea(world, cfg, aid, candidates):
    agent = world.agents[aid]
    policy_context = context(cfg, world.policy, 'idea_selection')
    actions = []
    for index, card in enumerate(candidates):
        novelty, feasibility, recognition = (card[k] for k in
            ['expected_novelty', 'expected_feasibility', 'expected_recognition'])
        individual = (agent.values['novelty']*novelty + agent.values['reputation']*recognition
                      + .5*feasibility)
        cost = agent.values['risk']*(1-feasibility)
        actions.append({'candidate_id': f'candidate:{world.seed}:{world.tick//6}:{aid}:{index}',
                        'subjective_features': {'expected_novelty': novelty, 'expected_recognition': recognition,
                                                'expected_feasibility': feasibility},
                        'individual_utility': individual, 'policy_term': policy_context.policy_term(novelty, recognition),
                        'cost': cost, 'temperature': cfg['decision_temperature'],
                        'evidence_ids': card['references'], 'candidate_index': index})
    key = [world.seed, world.tick, aid, 'idea-selection']
    selected, audit = choose(aid, world.tick, 'idea_selection', actions, rng(*key), policy_context, key)
    index = next(a['candidate_index'] for a in actions if a['candidate_id'] == selected)
    return index, audit


# 计算给定公开候选人的邀请效用特征而不使用field差异捷径。
def invitation_action(world, topic_model, cfg, leader, candidate):
    source, target = world.agents[leader], world.agents[candidate]
    policy_context = context(cfg, world.policy, 'invitation')
    need = {source.selected_topic_id}
    coverage_gain = len((need - set(source.public_topics)) & set(target.public_topics))/max(1, len(need))
    topic_match = float(source.selected_topic_id in target.public_topics or
                        target.selected_topic_id == source.selected_topic_id)
    public_reputation = target.simulated_reputation
    trust = source.trust.get(candidate, .5)
    target_attention = topic_model.attention.get(target.selected_topic_id, 0.0)
    novelty = .5*coverage_gain + .5*topic_match
    recognition = .5*target_attention + .5*public_reputation
    individual = .35*trust + .35*topic_match + .30*public_reputation
    cost = .25*(1-trust)
    return {'candidate_id': candidate, 'subjective_features': {'knowledge_coverage_gain': coverage_gain,
            'topic_match': topic_match, 'public_reputation': public_reputation,
            'historical_collaboration_quality': trust, 'expected_coordination_cost': cost,
            'expected_novelty': novelty, 'expected_recognition': recognition},
            'individual_utility': individual, 'policy_term': policy_context.policy_term(novelty, recognition),
            'cost': cost, 'temperature': cfg['decision_temperature'],
            'evidence_ids': sorted(set(source.public_topics + target.public_topics))}


# 将被接受邀请的个人项目无重复成本地合并进目标项目。
def merge_project(world, source_id, target_id, member, tick):
    source, target = world.projects[source_id], world.projects[target_id]
    if source.status != 'active' or target.status != 'active':
        raise ValueError('Only active projects may merge')
    source.status, source.closed_tick, source.merged_into = 'merged', tick, target_id
    source.current_members = []
    target.origin_draft_ids = sorted(set(target.origin_draft_ids + source.origin_draft_ids))
    target.historical_contributors = sorted(set(target.historical_contributors + [member]))
    target.current_members.append(member)
    world.agents[member].project_id = target_id


# 解析一个项目内并发退出意图并执行中性负责人接任或项目废弃。
def apply_exits(world, project_id, exiting, tick):
    project = world.projects[project_id]
    exiting = sorted(set(exiting) & set(project.current_members))
    for aid in exiting:
        add_effort(world, project_id, aid, tick, 'exit_switching', 2)
        world.agents[aid].project_id = None
        world.agents[aid].last_exit_cycle = tick//6
    remaining = sorted(set(project.current_members) - set(exiting))
    project.current_members = remaining
    if not remaining:
        project.status, project.closed_tick = 'abandoned', tick
    elif project.owner in exiting:
        project.owner = sorted(remaining, key=lambda aid: (-world.agents[aid].simulated_reputation, aid))[0]


# 沿合并链将历史投入映射到最终存续项目且不复制账本条目。
def resolved_project_id(world, project_id):
    seen = set()
    while world.projects[project_id].status == 'merged':
        if project_id in seen:
            raise ValueError('Project merge cycle')
        seen.add(project_id)
        project_id = world.projects[project_id].merged_into
    return project_id


# 在状态副本上执行一个v0.2 tick并返回新世界和完整审计事件。
def step_v02(original, corpus, topic_model, backend, cfg):
    world = copy.deepcopy(original)
    tick, phase, cycle = original.tick, original.tick % 6, original.tick//6
    events = []
    for agent in world.agents.values():
        agent.energy = min(100, agent.energy+8)
    if phase == 0:
        world.invitations = []
        for aid in sorted(original.agents):
            agent = world.agents[aid]
            if agent.last_exit_cycle == cycle:
                events.append(event('agent.waited_after_exit', aid, {'cycle': cycle}, [aid]))
                continue
            selected_topic, topic_audit = select_topic(original, topic_model, cfg, aid, cycle)
            agent.selected_topic_id = selected_topic
            agent.topic_choice_history.append({'cycle': cycle, 'topic_id': selected_topic})
            world.decision_audit.append(topic_audit)
            retrieval_context = context(cfg, world.policy, 'retrieval')
            query = f"{agent.field} {selected_topic} " + ' '.join(agent.public_topics)
            papers, retrieval_audit = corpus.retrieve_v02(query, agent.read_ids, topic_model, retrieval_context,
                                                           cfg['retrieval_candidate_pool'], cfg['retrieval_top_k'],
                                                           selected_topic, agent.field, agent.public_topics)
            if len(papers) < 2:
                retrieval_audit.update({'agent': aid, 'tick': tick, 'stage': 'retrieval'})
                world.decision_audit.append(retrieval_audit)
                agent.energy -= 4
                events.append(event('knowledge.retrieval.shortfall', aid, {'selected_topic': selected_topic,
                                    'selected_count': len(papers), 'fallback': retrieval_audit.get('fallback'),
                                    'query_id': retrieval_audit.get('query_id')}, [aid]))
                continue
            model_context = proposal_observation(world, aid, papers, topic_model, retrieval_context.effective_policy)
            response = backend.generate('propose', model_context, [world.seed, tick, aid, selected_topic])
            validate_response('propose', response, model_context)
            selected_index, idea_audit = select_idea(world, cfg, aid, response['candidates'])
            draft_id = f'draft:{world.seed}:{cycle}:{aid}'
            candidate_ids = [f'candidate:{world.seed}:{cycle}:{aid}:{i}' for i in range(2)]
            card = copy.deepcopy(response['candidates'][selected_index])
            world.ideas[draft_id] = {'id': draft_id, 'candidate_ids': candidate_ids, 'owner': aid, 'cycle': cycle,
                                     'topic_id': selected_topic, 'versions': [card], 'version_ids':
                                     [f'version:{world.seed}:{cycle}:{aid}:0'], 'status': 'selected_draft',
                                     'historical_contributors': [aid], 'candidate_count': 2}
            agent.read_ids = sorted(set(agent.read_ids) | {p['id'] for p in papers})
            agent.read_topic_ids.extend(topic for paper in papers for topic in topic_model.paper_topics.get(paper['id'], []))
            agent.energy -= 20
            world.decision_audit.extend([retrieval_audit | {'agent': aid, 'tick': tick, 'stage': 'retrieval'}, idea_audit])
            events.extend([event('decision.topic.recorded', aid, topic_audit, [aid]),
                           event('knowledge.paper.read', aid, {'paper_ids': [p['id'] for p in papers],
                                 'retrieval_audit': retrieval_audit}, [aid]),
                           event('decision.idea.recorded', aid, idea_audit, [aid]),
                           event('knowledge.draft.created', aid, {'draft_id': draft_id,
                                 'candidate_ids': candidate_ids, 'topic_id': selected_topic}, [aid])])
    elif phase == 1:
        leaders, _ = cycle_roles(world, cycle)
        for aid in sorted(original.agents):
            draft_id = f'draft:{world.seed}:{cycle}:{aid}'
            if draft_id not in world.ideas:
                continue
            project_id = f'project:{world.seed}:{cycle}:{aid}'
            world.projects[project_id] = Project(project_id, [draft_id], aid, [aid], [aid], 'active', tick,
                                                  None, list(world.ideas[draft_id]['version_ids']), 100, [])
            world.agents[aid].project_id = project_id
            add_effort(world, project_id, aid, tick, 'topic_selection', 1)
            add_effort(world, project_id, aid, tick, 'retrieval', 3)
            add_effort(world, project_id, aid, tick, 'proposal', 20)
            events.append(event('project.started', aid, {'project_id': project_id, 'draft_id': draft_id}, [aid]))
        for leader in leaders:
            project_id = world.agents[leader].project_id
            if not project_id:
                continue
            offered = offered_candidates(world, cycle, leader)
            actionable = [aid for aid in offered if world.agents[aid].project_id and
                          world.projects[world.agents[aid].project_id].status == 'active']
            policy_context = context(cfg, world.policy, 'invitation')
            actions = [invitation_action(world, topic_model, cfg, leader, candidate) for candidate in offered]
            key = [world.seed, tick, leader, 'invitation']
            selected, audit = choose(leader, tick, 'invitation', actions, rng(*key), policy_context, key)
            audit.update({'offered_ids': offered, 'offered_count': len(offered), 'actionable_ids': actionable,
                          'actionable_count': len(actionable), 'network': world.network})
            world.decision_audit.append(audit)
            if selected not in actionable:
                events.append(event('collaboration.invitation.failed', leader,
                                    {'target': selected, 'reason': 'selected_slot_unavailable', **audit}, [leader]))
                continue
            invitation = {'id': f'invitation:{world.seed}:{cycle}:{leader}', 'sender': leader, 'target': selected,
                          'project_id': project_id, 'status': 'pending', 'offered_count': len(offered),
                          'actionable_count': len(actionable), 'candidate_ids': offered}
            world.invitations.append(invitation)
            events.append(event('collaboration.invitation.created', leader, invitation.copy(), [leader, selected]))
    elif phase == 2:
        intents = []
        for target in sorted({x['target'] for x in original.invitations if x['status'] == 'pending'}):
            invitations = sorted([x for x in original.invitations if x['target'] == target and x['status'] == 'pending'],
                                 key=lambda x: x['id'])
            policy_context = context(cfg, world.policy, 'invitation')
            actions = []
            for invitation in invitations:
                sender = original.agents[invitation['sender']]
                target_agent = original.agents[target]
                topic_match = float(sender.selected_topic_id in target_agent.public_topics)
                recognition = .5*sender.simulated_reputation + .5*topic_model.attention[sender.selected_topic_id]
                novelty = .5*topic_match + .5*float(sender.selected_topic_id not in target_agent.public_topics)
                actions.append({'candidate_id': invitation['id'], 'subjective_features':
                               {'expected_novelty': novelty, 'expected_recognition': recognition,
                                'topic_match': topic_match}, 'individual_utility':
                               .5*target_agent.trust.get(sender.id, .5)+.5*target_agent.values['cooperation'],
                               'policy_term': policy_context.policy_term(novelty, recognition), 'cost': .15,
                               'temperature': cfg['decision_temperature'], 'evidence_ids': sender.public_topics})
            actions.append({'candidate_id': 'wait', 'subjective_features': {'expected_novelty': 0.0,
                            'expected_recognition': 0.0}, 'individual_utility': .3, 'policy_term': 0.0,
                            'cost': 0.0, 'temperature': cfg['decision_temperature'], 'evidence_ids': []})
            key = [world.seed, tick, target, 'acceptance']
            selected, audit = choose(target, tick, 'invitation', actions, rng(*key), policy_context, key)
            intents.append((target, selected, audit))
        for target, selected, audit in intents:
            world.decision_audit.append(audit)
            for invitation in world.invitations:
                if invitation['target'] != target or invitation['status'] != 'pending':
                    continue
                accepted = invitation['id'] == selected
                invitation['status'] = 'accepted' if accepted else 'rejected'
                if accepted:
                    source_id = world.agents[target].project_id
                    merge_project(world, source_id, invitation['project_id'], target, tick)
                    events.append(event('project.merged', target, {'source_project_id': source_id,
                                        'target_project_id': invitation['project_id']}, [target, invitation['sender']]))
                events.append(event('collaboration.invitation.'+invitation['status'], target,
                                    invitation.copy(), [target, invitation['sender']]))
                world.invitation_history.append(invitation.copy())
    elif phase == 3:
        exit_intents = {}
        for project in sorted(original.projects.values(), key=lambda p: p.id):
            if project.status != 'active':
                continue
            for aid in sorted(project.current_members):
                events.append(event('knowledge.project.shared', aid, {'project_id': project.id,
                                    'version_ids': list(project.version_ids)}, list(project.current_members)))
                draft = original.ideas[project.origin_draft_ids[0]]
                card = draft['versions'][-1]
                policy_context = context(cfg, world.policy, 'exit')
                expected_gain = .5*card['expected_feasibility'] + .5*original.agents[aid].simulated_reputation
                policy_gain = policy_context.policy_term(card['expected_novelty'], card['expected_recognition'])
                remaining_cost = cfg['project_exit']['remaining_cost']
                outside = cfg['project_exit']['outside_option']
                switching = cfg['project_exit']['switching_cost']
                actions = [{'candidate_id': 'continue', 'subjective_features': {'expected_novelty': card['expected_novelty'],
                            'expected_recognition': card['expected_recognition'], 'expected_individual_gain': expected_gain},
                            'individual_utility': expected_gain, 'policy_term': policy_gain, 'cost': remaining_cost,
                            'temperature': cfg['decision_temperature'], 'evidence_ids': project.origin_draft_ids},
                           {'candidate_id': 'exit', 'subjective_features': {'outside_option': outside},
                            'individual_utility': outside, 'policy_term': 0.0, 'cost': switching,
                            'temperature': cfg['decision_temperature'], 'evidence_ids': []}]
                key = [world.seed, tick, aid, project.id, 'exit']
                selected, audit = choose(aid, tick, 'exit', actions, rng(*key), policy_context, key)
                world.decision_audit.append(audit)
                events.append(event('decision.exit.recorded', aid, audit, [aid]))
                if cfg['project_exit']['enabled'] and selected == 'exit':
                    exit_intents.setdefault(project.id, []).append(aid)
        for project_id in sorted(exit_intents):
            old_owner = world.projects[project_id].owner
            exiting = exit_intents[project_id]
            apply_exits(world, project_id, exiting, tick)
            project = world.projects[project_id]
            events.append(event('project.members.exited', 'system', {'project_id': project_id,
                                'exiting': sorted(exiting), 'status': project.status,
                                'old_owner': old_owner, 'new_owner': project.owner}, []))
    elif phase == 4:
        for project in sorted(original.projects.values(), key=lambda p: p.id):
            if project.status != 'active':
                continue
            draft = world.ideas[project.origin_draft_ids[0]]
            card = draft['versions'][-1]
            shared = [world.ideas[x]['versions'][-1] for x in project.origin_draft_ids[1:]]
            ctx = {'schema_version': '0.2', 'self': {'id': project.owner,
                   'field': world.agents[project.owner].field}, 'idea': card, 'shared_cards': shared,
                   'selected_topic': topic_model.features(draft['topic_id'])}
            reply = backend.generate('revise', ctx, [world.seed, tick, project.id])
            validate_response('revise', reply, ctx)
            version = copy.deepcopy(card)
            version.update(hypothesis=reply['revised_hypothesis'], method=reply['revised_method'])
            version_id = f'version:{world.seed}:{cycle}:{project.owner}:{len(draft["versions"])}'
            draft['versions'].append(version)
            draft['version_ids'].append(version_id)
            project.version_ids.append(version_id)
            for aid in project.current_members:
                add_effort(world, project.id, aid, tick, 'revision', 10)
                world.agents[aid].energy -= 10
            events.append(event('knowledge.project.revised', project.owner,
                                {'project_id': project.id, 'version_id': version_id}, list(project.current_members)))
    else:
        feedback_policy = ('balanced' if not any(cfg['policy_paths'].values()) else world.policy)
        wn, wr = WEIGHTS[feedback_policy]
        for project in sorted(world.projects.values(), key=lambda p: p.id):
            if project.status != 'active':
                continue
            draft = world.ideas[project.origin_draft_ids[0]]
            card = draft['versions'][-1]
            text = card['title']+' '+card['hypothesis']+' '+card['method']
            lexical_novelty = corpus.novelty(text)
            recognition, recognized_topics = topic_model.recognition(text)
            institutional_reward = wn*lexical_novelty + wr*recognition
            project.status, project.closed_tick = 'completed', tick
            project.completion_members = list(project.current_members)
            draft.update(status='final_output', lexical_novelty_proxy=lexical_novelty,
                         community_attention_proxy=recognition, recognized_topic_ids=recognized_topics,
                         institutional_reward=institutional_reward, completed_project_id=project.id)
            add_effort(world, project.id, project.owner, tick, 'submission', 4)
            for aid in project.current_members:
                agent = world.agents[aid]
                agent.simulated_reputation = clip(agent.simulated_reputation + .05*(institutional_reward-.5))
                agent.memories = (agent.memories + [{'tick': tick, 'project_id': project.id,
                                  'institutional_reward': institutional_reward}])[-12:]
                agent.project_id = None
            events.append(event('project.completed', project.owner, {'project_id': project.id,
                                'lexical_novelty_proxy': lexical_novelty,
                                'community_attention_proxy': recognition,
                                'recognized_topic_ids': recognized_topics,
                                'institutional_reward': institutional_reward,
                                'feedback_policy': feedback_policy}, list(project.historical_contributors)))
        world.history.append({'cycle': cycle, 'tick': tick})
    world.tick += 1
    validate_v02(world)
    return world, events
