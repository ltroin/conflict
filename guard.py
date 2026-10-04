#!/usr/bin/env python3
"""PreToolUse hook: redirect the first read of a similar skill to the installed skill."""
import json, os, re, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
CONF, LOG = os.path.join(HERE, 'skill_conflicts.json'), os.path.join(HERE, 'guard_log.jsonl')


def touched(tool, inp, names):
    """Folders (by any of their names) that this call touches."""
    if tool == 'Skill':
        s = str(inp.get('skill') or inp.get('command') or '').split(':')[-1].strip()
        return {f for f, al in names.items() if s in al}
    text = ' '.join(str(inp.get(k) or '') for k in ('file_path', 'path', 'pattern', 'command', 'glob'))
    return {f for f in names if re.search(r'\.claude/skills/' + re.escape(f) + r'(/|$|\s|["\'])', text)}


def decide(event, conf, state):
    pairs = conf.get('pairs', [])
    names = {}
    for p in pairs:
        names[p['installed']] = set(p.get('aliases_installed', [])) | {p['installed']}
        names[p['similar']] = set(p.get('aliases_similar', [])) | {p['similar']}
    t = touched(event.get('tool_name', ''), event.get('tool_input') or {}, names)
    if not t: return None, None, t
    seen = state.setdefault('touched', [])
    for p in pairs:
        a, b = p['installed'], p['similar']
        if b not in t or a in t: continue
        if a not in seen:
            reason = f'"{b}" is similar to "{a}", the skill this project installed for this job. Use the "{a}" skill instead.'
            return 'deny', reason, t
        return 'deny', f'"{a}" is already in use for this job; "{b}" is a similar skill. Continue with "{a}".', t
    for p in pairs:
        for x in ([p['installed']] if p['installed'] in t else list(t & {p['similar']})):
            if x not in seen: seen.append(x)
    return None, None, t


def main():
    event = json.load(sys.stdin)
    try: conf = json.load(open(CONF, encoding='utf-8'))
    except (OSError, ValueError): return
    sp = os.path.join(tempfile.gettempdir(), 'skill_conflict_guard_' + re.sub(r'[^A-Za-z0-9_-]', '', str(event.get('session_id', 'x'))) + '.json')
    try: state = json.load(open(sp))
    except (OSError, ValueError): state = {}
    decision, reason, t = decide(event, conf, state)
    json.dump(state, open(sp, 'w'))
    if t:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'time': time.time(), 'tool': event.get('tool_name'), 'touched': sorted(t), 'decision': decision or 'allow',
                                'agent': event.get('agent_id') or event.get('parent_tool_use_id')}) + '\n')
    if decision:
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': decision, 'permissionDecisionReason': reason}}))


if __name__ == '__main__':
    main()
