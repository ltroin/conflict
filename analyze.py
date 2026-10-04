"""Recompute prevalence, RQ1–RQ3, study size, repeat agreement, and the Codex check."""
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import statistics as st

import numpy as np
from scipy import stats

DATA = Path(__file__).resolve().parent / 'data'
MODELS = ('sonnet', 'haiku', 'opus')


def read(name):
    with (DATA / name).open(encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def mean(values):
    values = [v for v in values if v is not None]
    return st.mean(values) if values else None


def rate(values):
    values = [v for v in values if v is not None]
    return {'percent': 100 * st.mean(values) if values else None, 'n': len(values)}


def t_interval(values):
    if len(values) < 2:
        return None
    mu = st.mean(values)
    sd = st.stdev(values)
    half = stats.t.ppf(.975, len(values) - 1) * sd / math.sqrt(len(values))
    return {'difference_pp': mu * 100, 'ci95_pp': [(mu - half) * 100, (mu + half) * 100],
            'p': float(stats.ttest_1samp(values, 0).pvalue) if sd else 1.0, 'pairs': len(values)}


def bootstrap_result(mu, samples, **counts):
    samples = np.asarray(samples)
    lo, hi = np.percentile(samples, [2.5, 97.5])
    p = min(1., 2 * min(np.mean(samples >= 0), np.mean(samples <= 0)))
    return {'difference_pp': mu * 100, 'ci95_pp': [float(lo * 100), float(hi * 100)],
            'p': max(float(p), 1 / len(samples)), **counts}


def bootstrap_mean(rows, seed=3):
    by = defaultdict(list)
    for cid, value in rows:
        if value is not None:
            by[cid].append(value)
    if not by:
        return None
    values = np.array([st.mean(v) for v in by.values()])
    rng = np.random.default_rng(seed)
    draw = rng.integers(0, len(values), (10000, len(values)))
    samples = values[draw].mean(axis=1)
    # Use exact float summation for samples on the sign-test boundary.
    for i in np.flatnonzero(np.abs(samples) < 1e-12):
        samples[i] = st.mean(float(values[j]) for j in draw[i])
    return bootstrap_result(st.mean(values), samples, pairs=len(by), observations=sum(map(len, by.values())))


def bootstrap_difference(group1, group2, seed=4):
    """Difference of run means, resampling the union of pairs as clusters."""
    pairs = sorted({c for c, v in group1 + group2 if v is not None})
    if not pairs:
        return None
    index = {c: i for i, c in enumerate(pairs)}
    sums = np.zeros((len(pairs), 2))
    counts = np.zeros_like(sums)
    raw = [[[], []] for _ in pairs]
    for j, group in enumerate((group1, group2)):
        for cid, value in group:
            if value is not None:
                sums[index[cid], j] += value
                counts[index[cid], j] += 1
                raw[index[cid]][j].append(value)
    if np.any(counts.sum(axis=0) == 0):
        return None
    means = sums.sum(axis=0) / counts.sum(axis=0)
    mu = means[0] - means[1]
    rng = np.random.default_rng(seed)
    draw = rng.integers(0, len(pairs), (10000, len(pairs)))
    den = counts[draw].sum(axis=1)
    num = sums[draw].sum(axis=1)
    valid = np.all(den > 0, axis=1)
    means = num[valid] / den[valid]
    samples = means[:, 0] - means[:, 1]
    valid_draws = draw[valid]
    for i in np.flatnonzero(np.abs(samples) < 1e-12):
        samples[i] = st.mean(v for j in valid_draws[i] for v in raw[j][0]) - st.mean(v for j in valid_draws[i] for v in raw[j][1])
    return bootstrap_result(float(mu), samples,
                            runs_1=int(counts[:, 0].sum()), runs_2=int(counts[:, 1].sum()))


def holm(results):
    order = sorted(results, key=lambda k: results[k]['p'])
    previous = 0.
    for i, key in enumerate(order):
        previous = max(previous, min(1., (len(order) - i) * results[key]['p']))
        results[key]['p_holm'] = previous


def key(row):
    return row['case_id'], row['configuration'], row['model']


def first_runs(rows):
    result = {}
    for r in sorted(rows, key=lambda r: r['replicate']):
        result.setdefault(key(r), r)
    return result


def paired(runs, configuration, baseline, field, model=None, keep=lambda r: True):
    by = defaultdict(list)
    for (cid, config, m), r in runs.items():
        if config != configuration or (model and model != m) or not keep(r):
            continue
        base = runs.get((cid, baseline, m))
        if base and r.get(field) is not None and base.get(field) is not None:
            by[cid].append(r[field] - base[field])
    return t_interval([st.mean(v) for v in by.values()])


def stratified(units, sizes, predicate, condition=None):
    by = defaultdict(list)
    all_counts = Counter(u['stratum'] for u in units)
    for u in units:
        if condition is None or condition(u):
            by[u['stratum']].append(float(predicate(u)))
    numerator = variance = denominator = 0.
    for h, ys in by.items():
        N, n = sizes[h], len(ys)
        Nh = N * n / all_counts[h] if condition is not None else N
        p = sum(ys) / n
        numerator += Nh * p
        denominator += Nh
        if n > 1:
            variance += Nh ** 2 * (1 - n / max(N, n)) * p * (1 - p) / (n - 1)
    if not denominator:
        return None
    est, se = numerator / denominator, math.sqrt(variance) / denominator
    return {'estimate': round(est, 4), 'ci95': [round(max(0, est - 1.96 * se), 4), round(min(1, est + 1.96 * se), 4)],
            'n': sum(map(len, by.values()))}


def prevalence():
    units = read('prevalence.jsonl')
    sizes = json.loads((DATA / 'strata.json').read_text())['N']
    out = {}
    for scope in ('cross', 'within'):
        us = [u for u in units if u['scope'] == scope]
        confirmed = lambda u: u['confirmed']
        labelled = lambda u: u['confirmed'] and (u['scripts'] or u['A_type'] is not None)
        cell = lambda u: 'script' if u['scripts'] else u['A_type']
        counts = Counter(u['stratum'] for u in us)
        positives = Counter(u['stratum'] for u in us if u['confirmed'])
        out[scope] = {'sample': len(us), 'confirmed': sum(u['confirmed'] for u in us),
                      'prevalence': stratified(us, sizes, confirmed),
                      'estimated_families': round(sum(sizes[h] * positives[h] / counts[h] for h in counts)),
                      'cells': dict(Counter(cell(u) for u in us if u['confirmed'])),
                      'normative': stratified(us, sizes, lambda u: not u['scripts'] and u['A_type'] == 'normative', labelled),
                      'capability': stratified(us, sizes, lambda u: not u['scripts'] and u['A_type'] == 'capability', labelled),
                      'script': stratified(us, sizes, lambda u: u['scripts'], confirmed)}
    within = [u for u in units if u['scope'] == 'within']
    out['copied_collections'] = {
        'bulk': rate([u['confirmed'] for u in within if u['bulk_collection'] is True]),
        'other': rate([u['confirmed'] for u in within if u['bulk_collection'] is False]),
        'unresolved': sum(u['bulk_collection'] is None for u in within),
    }
    out['outside_project'] = rate([u['skill_outside_project'] for u in within if u['confirmed']])
    return out


def exclusive_shared(runs, cases, exclusive):
    effects, control = defaultdict(list), defaultdict(list)
    per_model = {m: defaultdict(list) for m in MODELS}
    levels = {a: defaultdict(list) for a in ('A_only', 'both', 'B_only')}
    for cid, case in cases.items():
        wanted = {str(i['rank']) for i in case['core_functions'] if i['exclusive'] == exclusive}
        for model in MODELS:
            rs = {a: runs.get((cid, a, model)) for a in levels}
            if not all(r and r.get('all_items') for r in rs.values()):
                continue
            selected = wanted.intersection(*({k for k, v in r['all_items'].items() if v is not None} for r in rs.values()))
            if not selected:
                continue
            values = {a: st.mean(r['all_items'][k] for k in selected) for a, r in rs.items()}
            for a, value in values.items():
                levels[a][cid].append(value)
            effects[cid].append(values['both'] - values['A_only'])
            control[cid].append(values['B_only'] - values['A_only'])
            per_model[model][cid].append(values['both'] - values['A_only'])
    estimate = lambda by: t_interval([st.mean(v) for v in by.values()])
    return {'both_minus_A_only': estimate(effects), 'B_only_minus_A_only': estimate(control),
            'by_model': {m: estimate(by) for m, by in per_model.items()},
            'levels_percent': {a: 100 * st.mean(st.mean(v) for v in by.values()) for a, by in levels.items()}}


def usage(row):
    other = 'C' if row['configuration'] == 'A_plus_C' else 'B'
    other_used = row['used_' + other]
    return 'both' if row['used_A'] and other_used else 'A' if row['used_A'] else other if other_used else 'neither'


def rq1(runs, cases):
    out = {}
    for config, base in (('both', 'A_only'), ('B_only', 'A_only'), ('A_plus_C', 'A_only'), ('both', 'A_plus_C')):
        out[f'{config}_minus_{base}'] = {
            m or 'pooled': {field: paired(runs, config, base, field, m) for field in ('used_A', 'top3_rate', 'completion')}
            for m in (None,) + MODELS}
    out['exclusive'] = exclusive_shared(runs, cases, True)
    out['shared'] = exclusive_shared(runs, cases, False)
    out['sensitivity'] = {field: paired(runs, 'both', 'A_only', field)
                          for field in ('first_item', 'all_rate', 'top3_no_start_rate')}
    for source in ('program', 'judge'):
        by = defaultdict(list)
        for (cid, a, m), r in runs.items():
            base = runs.get((cid, 'A_only', m))
            if a != 'both' or not base:
                continue
            v, b = r.get('top3_items') or {}, base.get('top3_items') or {}
            pr, pb = set(r['program_items']), set(base['program_items'])
            ranks = [k for k in v if v[k] is not None and b.get(k) is not None and
                     ((k in pr and k in pb) if source == 'program' else (k not in pr and k not in pb))]
            if ranks:
                by[cid].append(st.mean(v[k] - b[k] for k in ranks))
        out['sensitivity'][source] = t_interval([st.mean(v) for v in by.values()])
    cells = {}
    corrected = {}
    for cell in sorted({c['cell'] for c in cases.values()}):
        keep = lambda r: cases[r['case_id']]['cell'] == cell
        cells[cell] = {f: paired(runs, 'both', 'A_only', f, keep=keep) for f in ('used_A', 'top3_rate', 'completion')}
        for f in ('top3_rate', 'completion'):
            corrected[f'{cell}/{f}'] = cells[cell][f]
    holm(corrected)
    out['cells'] = cells
    rows = [r for r in runs.values() if r['configuration'] == 'both']
    losses = defaultdict(list)
    for r in rows:
        b = runs.get((r['case_id'], 'A_only', r['model']))
        if b and r['top3_rate'] is not None and b['top3_rate'] is not None:
            losses[usage(r)].append(r['top3_rate'] - b['top3_rate'])
    total = sum(sum(v) for v in losses.values())
    out['decomposition'] = {g: {'runs': sum(usage(r) == g for r in rows), 'paired_runs': len(v),
                                'fidelity_change_pp': 100 * st.mean(v), 'share_of_loss_percent': 100 * sum(v) / total}
                            for g, v in losses.items()}
    out['applicable_core_functions'] = sum(len(c['core_functions']) for c in cases.values())
    out['exclusive_core_functions'] = sum(i['exclusive'] for c in cases.values() for i in c['core_functions'])
    return out


def rq2(runs):
    out = {'order': {}}
    for model in (None,) + MODELS:
        by = defaultdict(list)
        for (cid, config, m), r in runs.items():
            s = runs.get((cid, 'both_swapped', m))
            if config != 'both' or not s or (model and m != model):
                continue
            if (r['first_listed'], s['first_listed']) == ('A', 'B'):
                by['A'].append(r['used_A'] - s['used_A'])
            elif (r['first_listed'], s['first_listed']) == ('B', 'A'):
                by['B'].append(s['used_A'] - r['used_A'])
        a, b = st.mean(by['A']), st.mean(by['B'])
        mu = (a + b) / 2
        se = math.sqrt(st.variance(by['A']) / len(by['A']) + st.variance(by['B']) / len(by['B'])) / 2
        out['order'][model or 'pooled'] = {'difference_pp': mu * 100, 'ci95_pp': [(mu - 1.96 * se) * 100, (mu + 1.96 * se) * 100],
                                         'rename_pp': (b - a) * 50}
    out['scope'] = {config: {field: paired(runs, config, 'A_only', field) for field in ('used_A', 'top3_rate', 'completion')}
                    for config in ('scope_personal', 'scope_plugin')}
    out['scope']['same_pairs_both'] = paired(runs, 'both', 'A_only', 'used_A',
                                            keep=lambda r: (r['case_id'], 'scope_plugin', r['model']) in runs)
    personal = [r for r in runs.values() if r['configuration'] == 'scope_personal']
    out['personal_A_listed'] = rate([r['A_listed'] for r in personal])
    substituted = [r for r in runs.values() if r['configuration'] == 'both' and usage(r) == 'B' and r['disclosed_choice'] is not None]
    out['substitution_disclosure'] = {f: rate([r[f] or 0 for r in substituted])
                                      for f in ('disclosed_multiple', 'disclosed_choice', 'asked_user')}
    probe = read('listing_probe.jsonl')
    out['default_budget_description_dropped'] = rate([bool(r['A_description_dropped']) or bool(r['B_description_dropped']) for r in probe])
    return out


def first_read():
    rows = read('first_read.jsonl')
    both = {(r['case_id'], r['model']): r for r in rows if r['configuration'] == 'both' and r['delta_primary'] is not None}
    swapped = {(r['case_id'], r['model']): r for r in rows if r['configuration'] == 'both_swapped' and r['delta_primary'] is not None}
    flips = []
    for k, x in both.items():
        y = swapped.get(k)
        if y and (x['first_skill'], y['first_skill']) == ('B', 'A'):
            flips.append((k[0], k[1], 0, x, y))
        elif y and (x['first_skill'], y['first_skill']) == ('A', 'B'):
            flips.append((k[0], k[1], 1, y, x))
    rng = np.random.default_rng(7)
    tests = {}
    for model in (None,) + MODELS:
        sub = [r for r in flips if not model or r[1] == model]
        pairs = sorted({r[0] for r in sub})
        index = {c: i for i, c in enumerate(pairs)}
        for field in ('delta_exclusive', 'delta_all', 'delta_primary'):
            sums = np.zeros((len(pairs), 2))
            counts = np.zeros_like(sums)
            raw = [[[], []] for _ in pairs]
            for cid, _, direction, b, a in sub:
                if b[field] is not None and a[field] is not None:
                    sums[index[cid], direction] += b[field] - a[field]
                    counts[index[cid], direction] += 1
                    raw[index[cid]][direction].append(b[field] - a[field])
            mu = np.mean(sums.sum(axis=0) / counts.sum(axis=0))
            draw = rng.integers(0, len(pairs), (20000, len(pairs)))
            num, den = sums[draw].sum(axis=1), counts[draw].sum(axis=1)
            values = np.divide(num, den, out=np.zeros_like(num), where=den > 0)
            n_directions = (den > 0).sum(axis=1)
            samples = values[n_directions > 0].sum(axis=1) / n_directions[n_directions > 0]
            valid_draws = draw[n_directions > 0]
            for i in np.flatnonzero(np.abs(samples) < 1e-12):
                directions = [[v for j in valid_draws[i] for v in raw[j][d]] for d in (0, 1)]
                samples[i] = st.mean(st.mean(v) for v in directions if v)
            tests[f'{model or "pooled"}/{field}'] = bootstrap_result(float(mu), samples, pairs=len(pairs), combinations=len(sub))
    holm(tests)
    out = {'within_pair': tests, 'before_first_read': {}, 'lost': {}}
    for config in ('both', 'both_swapped'):
        b = [r for r in rows if r['configuration'] == config and r['first_skill'] == 'B']
        out['before_first_read'][config] = rate([r['first_change_index'] is None or r['first_change_index'] >= r['first_read_index'] for r in b])
    for kind in ('exclusive', 'shared'):
        lost = sum(r[f'{kind}_lost'] for r in rows)
        fulfilled = sum(r[f'{kind}_fulfilled'] for r in rows)
        out['lost'][kind] = {'lost': lost, 'fulfilled_with_only_A': fulfilled, 'percent': lost / fulfilled * 100}
    return out


def guard_analysis(rows):
    guarded = [r for r in rows if r['configuration'] == 'both_guard']
    control = [r for r in rows if r['configuration'] == 'both']
    k = lambda r: (r['case_id'], r['model'], r['replicate'])
    g, c = {k(r): r for r in guarded}, {k(r): r for r in control}
    paired_keys = sorted(set(g) & set(c))
    out = {'B_first_guard': rate([r['first_skill'] == 'B' for r in guarded]),
           'B_first_control': rate([r['first_skill'] == 'B' for r in control])}
    denied = [r for r in guarded if r['first_skill'] == 'B' and r['denials'] > 0]
    out['compliance'] = {m or 'pooled': rate([r['used_A_after_first'] for r in denied if not m or r['model'] == m])
                         for m in (None,) + MODELS}
    for field in ('fidelity_exclusive', 'fidelity_all', 'completion'):
        valid = [p for p in paired_keys if g[p][field] is not None and c[p][field] is not None]
        group = lambda rs, first, model=None: [(r['case_id'], r[field]) for r in rs
                                                if r['first_skill'] == first and (not model or r['model'] == model)]
        out[field] = {
            'paired_all': bootstrap_mean([(p[0], g[p][field] - c[p][field]) for p in valid]),
            'paired_B_first': bootstrap_mean([(p[0], g[p][field] - c[p][field]) for p in valid if g[p]['first_skill'] == c[p]['first_skill'] == 'B']),
            'B_first': bootstrap_difference(group(guarded, 'B'), group(control, 'B')),
            'guardB_vs_controlA': bootstrap_difference(group(guarded, 'B'), group(control, 'A')),
            'controlB_vs_controlA': bootstrap_difference(group(control, 'B'), group(control, 'A')),
            'B_first_by_model': {m: bootstrap_difference(group(guarded, 'B', m), group(control, 'B', m)) for m in MODELS},
        }
    return out


def icc(groups):
    groups = [g for g in groups if len(g) >= 2]
    n, k = sum(map(len, groups)), len(groups)
    grand = sum(map(sum, groups)) / n
    between = sum(len(g) * (st.mean(g) - grand) ** 2 for g in groups) / (k - 1)
    within = sum((v - st.mean(g)) ** 2 for g in groups for v in g) / (n - k)
    k0 = (n - sum(len(g) ** 2 for g in groups) / n) / (k - 1)
    return (between - within) / (between + (k0 - 1) * within)


def repeats(rows):
    groups = defaultdict(list)
    for r in rows:
        if r['configuration'] != 'A_plus_C':
            groups[key(r)].append(r)
    repeated = [g for g in groups.values() if len(g) >= 2]
    use = [[int(r['used_A']) for r in g] for g in repeated]
    fidelity = [[r['script_fidelity'] for r in g if r['script_fidelity'] is not None] for g in repeated]
    agreement = [a == b for g in use for i, a in enumerate(g) for b in g[i + 1:]]
    return {'combinations': len(repeated), 'runs': sum(map(len, repeated)), 'use_A_agreement': rate(agreement),
            'use_A_ICC': icc(use), 'script_fidelity_ICC': icc(fidelity)}


def codex_check(runs, cases):
    cx = {key(r): r for r in read('codex.jsonl')}
    pairs = {k[0] for k in cx}
    cl = {k: r for k, r in runs.items() if k[0] in pairs and k[1] in ('A_only', 'both') and r['all_items'] is not None}
    def exclusive(r):
        ranks = {str(i['rank']) for i in cases[r['case_id']]['core_functions'] if i['exclusive']}
        return mean([v for k, v in (r['top3_items'] or {}).items() if k in ranks])
    out = {}
    for platform, records in (('codex', cx), ('claude_same_pairs', cl)):
        table = {}
        for field, measure in (('use_A', lambda r: int(r['used_A'])), ('fidelity_exclusive_primary', exclusive)):
            by = defaultdict(list)
            for (cid, config, m), r in records.items():
                b = records.get((cid, 'both', m))
                if config != 'A_only' or b is None:
                    continue
                x, y = measure(r), measure(b)
                if x is not None and y is not None:
                    by[cid].append(y - x)
            table[field] = bootstrap_mean([(cid, st.mean(by[cid])) for cid in sorted(by)], seed=7)
        out[platform] = table
    return out


def scale(rows, guard, runs):
    groups = defaultdict(list)
    for r in rows:
        group = r['configuration'] if runs[key(r)] is r else 'repeats'
        groups[group].append(r)
    for r in guard:
        groups['guard/' + r['configuration']].append(r)
    groups['total'] = rows + guard
    out = {}
    for name, rs in groups.items():
        out[name] = {'runs': len(rs), 'pairs': len({r['case_id'] for r in rs}),
                     'timeouts': sum(r['status'] == 'timeout' for r in rs),
                     **{f: sum(r[f] or 0 for r in rs) for f in ('seconds', 'tool_calls')}}
    out['main_runs'] = len(runs)
    out['main_timeouts'] = sum(r['status'] == 'timeout' for r in runs.values())
    return out


def levels(runs):
    out = {}
    for config in sorted({r['configuration'] for r in runs.values()}):
        out[config] = {}
        for model in (None,) + MODELS:
            rs = [r for r in runs.values() if r['configuration'] == config and (not model or model == r['model'])]
            out[config][model or 'pooled'] = {
                **{f: rate([r[f] for r in rs]) for f in ('used_A', 'used_B', 'used_C', 'top3_rate', 'completion', 'disclosed_multiple', 'disclosed_choice', 'asked_user')},
                'skill_use': dict(Counter(usage(r) for r in rs)),
            }
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('results.json'))
    args = parser.parse_args()
    cases = {r['case_id']: r for r in read('cases.jsonl')}
    rows, guard = read('runs.jsonl'), read('guard.jsonl')
    runs = first_runs(rows)
    results = {'prevalence': prevalence(), 'scale': scale(rows, guard, runs), 'levels': levels(runs),
               'rq1': rq1(runs, cases), 'rq2': rq2(runs), 'rq3': first_read(),
               'guard': guard_analysis(guard), 'repeats': repeats(rows), 'codex': codex_check(runs, cases)}
    args.output.write_text(json.dumps(results, indent=2, allow_nan=False) + '\n')
    print(f'Cases: {len(cases)}; main runs: {len(runs)}; guard runs: {len(guard)}; total Claude Code runs: {len(rows) + len(guard)}')
    fmt = lambda x: f"{x['difference_pp']:+.1f} [{x['ci95_pp'][0]:+.1f}, {x['ci95_pp'][1]:+.1f}] pp"
    print('RQ1 use of A:', fmt(results['rq1']['both_minus_A_only']['pooled']['used_A']))
    print('RQ1 exclusive fidelity:', fmt(results['rq1']['exclusive']['both_minus_A_only']))
    print('RQ2 listing order:', fmt(results['rq2']['order']['pooled']))
    print('RQ3 first-read exclusive fidelity:', fmt(results['rq3']['within_pair']['pooled/delta_exclusive']))
    print('Guard exclusive fidelity:', fmt(results['guard']['fidelity_exclusive']['paired_all']))
    print('Wrote', args.output)


if __name__ == '__main__':
    main()
