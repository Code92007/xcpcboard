"""Conservative cross-contest matching for public groups, notes and CF bindings."""
import copy
from collections import Counter
import secrets


def norm(value):
    return ''.join(str(value).split()).casefold()


def identity(team):
    return norm(team.get('name', '')), norm(team.get('organization', ''))


def roster(team):
    return frozenset(norm(m) for m in team.get('members', []) if norm(m))


def match_teams(before, after):
    old_names = Counter(identity(t) for t in before)
    old_rosters = Counter(roster(t) for t in before)
    result, ambiguous = {}, set()
    for team in before:
        name, people = identity(team), roster(team)
        same_name = [t for t in after if name[0] and identity(t) == name]
        candidates = [t for t in same_name if not (people and roster(t) and people != roster(t))]
        if old_names[name] != 1:
            candidates = []
        if len(candidates) != 1 and len(people) >= 2 and old_rosters[people] == 1:
            candidates = [t for t in after if roster(t) == people]
        if len(candidates) == 1:
            result[team['id']] = candidates[0]
        elif len(candidates) > 1 or (same_name and old_names[name] > 1):
            ambiguous.add(team['id'])
    return result, ambiguous


def plan_import(target, sources, names):
    profile = target['station']
    notes = copy.deepcopy(profile.get('notes', {}))
    groups = copy.deepcopy(profile.get('public_groups', []))
    bindings = copy.deepcopy(profile.get('cf_bindings', {}))
    report = dict(matches=[], unmatched=0, ambiguous=0, notes_added=0, bindings_added=0, conflicts=[])
    for source in sources:
        station = source['station']
        selected = [g for g in station.get('public_groups', []) if g['name'] in names]
        wanted = {tid for g in selected for tid in g['teams']}
        matched, ambiguous = match_teams(source['board']['teams'], target['board']['teams'])
        old = {t['id']:t for t in source['board']['teams']}
        report['ambiguous'] += len(wanted & ambiguous)
        report['unmatched'] += len(wanted - matched.keys() - ambiguous)
        for tid in sorted(wanted & matched.keys()):
            team = matched[tid]
            note = station.get('notes', {}).get(tid, '')
            report['matches'].append(dict(source=station['title'], old_id=tid, id=team['id'], name=team['name'],
                                          previous_name=old[tid]['name'], note=note))
            if note and not notes.get(team['id']):
                notes[team['id']] = note
                report['notes_added'] += 1
            elif note and notes.get(team['id']) != note:
                report['conflicts'].append(dict(id=team['id'], kept=notes[team['id']], skipped=note))
            for binding in station.get('cf_bindings', {}).get(tid, []):
                current = bindings.setdefault(team['id'], [])
                if (binding['member'] in team.get('members', []) and len(current) < 3
                        and all(b['member'] != binding['member'] and b['handle'].casefold() != binding['handle'].casefold() for b in current)):
                    current.append(copy.deepcopy(binding))
                    report['bindings_added'] += 1
        for group in selected:
            same = [g for g in groups if g['name'] == group['name']]
            if len(same) > 1:
                raise ValueError('目标有多个同名公共分组，请先合并：' + group['name'])
            if same:
                current = same[0]
            else:
                current = dict(id='g'+secrets.token_hex(8), name=group['name'], teams=[])
                groups.append(current)
            current['teams'] = list(dict.fromkeys(current['teams'] + [matched[t]['id'] for t in group['teams'] if t in matched]))
    return dict(notes=notes, public_groups=groups, cf_bindings={k:v for k,v in bindings.items() if v}, report=report)
