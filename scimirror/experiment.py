import argparse
import copy
import datetime
import json
import platform
import sys
from pathlib import Path
from .backend import Backend
from .common import dump, digest
from .corpus import Corpus
from .engine import step, event, POLICIES
from .events import Journal, replay
from .metrics import measure, write_csv, paired_effects
from .state import initialize

ROOT = Path(__file__).resolve().parents[1]


def validate_config(c):
    if type(c['agents']) is not int or not 2 <= c['agents'] <= 100:
        raise ValueError('agents must be integer 2..100')
    if c['ticks'] % 6 or c['branch_tick'] % 6 or not 0 < c['branch_tick'] < c['ticks']:
        raise ValueError('ticks and branch_tick must be cycle boundaries (multiples of 6)')
    if not c['seeds'] or len(c['seeds']) != len(set(c['seeds'])) or any(type(s) is not int for s in c['seeds']):
        raise ValueError('seeds must be distinct integers')
    if len(c['policies']) != len(set(c['policies'])) or not set(c['policies']) <= set(POLICIES) or 'balanced' not in c['policies']:
        raise ValueError('policies need balanced control and unique valid names')
    if not c['networks'] or len(c['networks']) != len(set(c['networks'])) or not set(c['networks']) <= {'open','closed'}:
        raise ValueError('networks need unique open/closed values')
    if c['backend'] not in ('mock','chat') or c['decision_temperature'] <= 0:
        raise ValueError('Invalid backend or decision temperature')


def load_config(path):
    c = json.loads(Path(path).read_text(encoding='utf-8'))
    validate_config(c)
    return c


def estimate(c):
    calls_per_cycle = c['agents']+(c['agents']+1)//2
    n = len(c['seeds'])*calls_per_cycle*(c['branch_tick']//6 + len(c['policies'])*len(c['networks'])*((c['ticks']-c['branch_tick'])//6))
    return {'logical_calls_without_cache_or_retries': n, 'max_completion_tokens_without_retries': n*c['max_tokens'],
            'request_attempt_cap': c['max_calls'], 'backend': c['backend']}


def run(c, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    corpus = Corpus(ROOT/c['corpus'], c['cutoff_year'], c['allow_synthetic'])
    backend = Backend(c, ROOT)
    dump(output/'config.json', c)
    dump(output/'manifest.json', {'python': sys.version, 'platform': platform.platform(), 'config_hash': digest(c),
         'source_hash': digest({str(p.relative_to(ROOT)): p.read_text(encoding='utf-8') for p in sorted((ROOT/'scimirror').glob('*.py'))}),
         'corpus_hash': digest(corpus.papers), 'backend': c['backend'], 'model': backend.model,
         'synthetic_corpus': any(p['synthetic'] for p in corpus.papers.values()), 'estimate': estimate(c),
         'scientific_status': 'prototype; lexical proxies; no real-world causal claim'})
    rows, traces = [], []
    try:
        for seed in c['seeds']:
            w = initialize(c['agents'], seed)
            prefix_path = output/f'seed_{seed}'/'prefix'
            journal = Journal(prefix_path/'events.jsonl', f'{seed}/prefix')
            journal.commit(w, [event('system.initialized', 'system', {'seed': seed})])
            while w.tick < c['branch_tick']:
                w, evs = step(w, corpus, backend, c)
                journal.commit(w, evs)
            initial = w.export()
            dump(prefix_path/'fork.json', {'state_hash': digest(initial), 'state': initial})
            for network in c['networks']:
                for policy in c['policies']:
                    branch = f'{policy}_{network}'
                    b = copy.deepcopy(w)
                    b.policy, b.network = policy, network
                    folder = output/f'seed_{seed}'/branch
                    bj = Journal(folder/'events.jsonl', f'{seed}/{branch}')
                    bj.commit(b, [event('policy.intervention.started', 'system',
                              {'policy': policy, 'network': network, 'fork_state_hash': digest(initial)})])
                    while b.tick < c['ticks']:
                        b, evs = step(b, corpus, backend, c)
                        bj.commit(b, evs)
                        if b.tick % 6 == 0:
                            traces.append({'seed': seed, 'policy': policy, 'network': network,
                                           'tick': b.tick, **measure(b, c['branch_tick']//6)})
                    restored = replay(folder/'events.jsonl')
                    if digest(restored.export()) != digest(b.export()):
                        raise AssertionError('Replay mismatch')
                    dump(folder/'final_state.json', b.export())
                    rows.append({'seed': seed, 'policy': policy, 'network': network,
                                 **measure(b, c['branch_tick']//6)})
                    print(f'Completed seed={seed} branch={branch} tick={b.tick}', flush=True)
        write_csv(output/'summary.csv', rows)
        write_csv(output/'trajectories.csv', traces)
        effects = paired_effects(rows)
        if effects:
            write_csv(output/'paired_effects.csv', effects)
        export_review(output, c)
        report = ['# SciMirror run', '', f"Mode: {c['backend']}; agents: {c['agents']}; world seeds: {len(c['seeds'])}.",
                  '', 'Mock/synthetic results validate software only. Lexical novelty is not scientific novelty.',
                  'Recognition means text familiarity, not predicted citations. Owner field entropy is not topic entropy.',
                  'Intervals resample paired independent worlds. Small seed counts are exploratory only.', '',
                  '| policy | network | seed | outputs | novelty proxy | cross-field rate |', '|---|---|---:|---:|---:|---:|']
        for r in rows:
            report.append(f"|{r['policy']}|{r['network']}|{r['seed']}|{r['outputs']}|{r['novelty_proxy']:.4f}|{r['cross_field_rate']:.4f}|")
        (output/'REPORT.md').write_text('\n'.join(report)+'\n', encoding='utf-8')
        dump(output/'status.json', {'status': 'completed'})
    except Exception as exc:
        dump(output/'status.json', {'status': 'failed', 'error_type': type(exc).__name__,
                                   'guidance': 'Inspect terminal; completed checkpoints retained. No mock substitution.'})
        raise
    finally:
        dump(output/'usage.json', {'http_attempts': backend.calls, 'cache_hits': backend.hits,
              'prompt_tokens_reported': backend.prompt_tokens, 'completion_tokens_reported': backend.completion_tokens,
              'note': 'Token totals depend on provider usage reporting; no price assumptions.'})
    return output


def export_review(output, c):
    """Blind cards: no policy, reward, agent identity or network in rater CSV."""
    cards, mapping = [], []
    for path in sorted(output.glob('seed_*/*/final_state.json')):
        w = json.loads(path.read_text(encoding='utf-8'))
        for idea in w['ideas'].values():
            if idea['status'] != 'evaluated' or idea['cycle'] < c['branch_tick']//6:
                continue
            rid = digest([str(path.relative_to(output)), idea['id']])[:16]
            card = idea['versions'][-1]
            cards.append({'review_id': rid, 'title': card['title'], 'hypothesis': card['hypothesis'],
                          'method': card['method'], 'references': ';'.join(card['references']),
                          'novelty_1_to_5': '', 'feasibility_1_to_5': '', 'comment': ''})
            mapping.append({'review_id': rid, 'branch': str(path.parent.relative_to(output)), 'idea_id': idea['id']})
    if cards:
        write_csv(output/'blind_review.csv', sorted(cards, key=lambda r: r['review_id']))
        write_csv(output/'review_key_private.csv', mapping)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', default=str(ROOT/'configs/mock.json'))
    p.add_argument('--output')
    p.add_argument('--estimate', action='store_true')
    args = p.parse_args()
    cfg = load_config(args.config)
    if args.estimate:
        print(json.dumps(estimate(cfg), indent=2))
        return
    target = args.output or ROOT/'outputs'/datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    print(run(cfg, target))


if __name__ == '__main__':
    main()
