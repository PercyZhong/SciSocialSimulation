import csv
import math
import statistics
from collections import Counter
from .common import rng
from .corpus import similarity


def measure(w, start_cycle=0):
    ideas = [x for x in w.ideas.values() if x['status']=='evaluated' and x['cycle']>=start_cycle]
    texts = [' '.join(x['versions'][-1][k] for k in ('title', 'hypothesis', 'method')) for x in ideas]
    pairs = [1-similarity(a,b) for i,a in enumerate(texts) for b in texts[i+1:]]
    count = Counter(x['field'] for x in ideas)
    total = max(1, len(ideas))
    entropy = -sum((v/total)*math.log(v/total) for v in count.values())
    mixed = [len({w.agents[a].field for a in x['contributors']})>1 for x in ideas]
    return {'outputs': len(ideas), 'novelty_proxy': statistics.mean([x['novelty_proxy'] for x in ideas]) if ideas else 0,
            'text_diversity': statistics.mean(pairs) if pairs else 0,
            'owner_field_entropy': entropy, 'cross_field_rate': statistics.mean(mixed) if mixed else 0,
            'mean_team_size': statistics.mean([len(x['contributors']) for x in ideas]) if ideas else 0,
            'mean_pressure': statistics.mean(a.pressure for a in w.agents.values())}


def write_csv(path, rows):
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def paired_effects(rows):
    """Paired by world seed; percentile bootstrap across worlds, not agents."""
    out = []
    for network in sorted({r['network'] for r in rows}):
        subset = [r for r in rows if r['network'] == network]
        controls = {r['seed']: r for r in subset if r['policy'] == 'balanced'}
        for policy in ['novelty', 'recognition']:
            for metric in ['novelty_proxy', 'text_diversity', 'cross_field_rate', 'mean_team_size', 'outputs']:
                ds = [r[metric]-controls[r['seed']][metric] for r in subset if r['policy']==policy and r['seed'] in controls]
                if not ds:
                    continue
                lo = hi = ''
                if len(ds) >= 2:
                    rand = rng(1234, network, policy, metric, 'bootstrap')
                    boots = sorted(statistics.mean(rand.choices(ds, k=len(ds))) for _ in range(2000))
                    lo, hi = boots[49], boots[1949]
                out.append({'network': network, 'treatment': policy, 'control': 'balanced', 'metric': metric,
                            'n_world_pairs': len(ds), 'mean_difference': statistics.mean(ds),
                            'ci95_low': lo, 'ci95_high': hi,
                            'interpretation': 'simulation_internal_proxy_only'})
    return out
