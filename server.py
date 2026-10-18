#!/usr/bin/env python3
"""Reusable XCPCIO spectator board. Python 3.10+, standard library only."""
import copy
import gzip
import hashlib
import hmac
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit, unquote, urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler

ROOT = Path(__file__).resolve().parent
POLL = max(10, int(os.getenv('POLL_SECONDS', '15')))
LOG = logging.getLogger('xcpcboard')
AC = {'AC', 'OK', 'CORRECT', 'ACCEPTED'}
PENDING = {'PD', 'PENDING', 'WAITING', 'COMPILING', 'JUDGING', 'FROZEN'}
REJECTED = {'WA', 'WRONG_ANSWER', 'RJ', 'REJECTED', 'INCORRECT', 'NO', 'NO_OUTPUT',
            'TL', 'TLE', 'TIME_LIMIT_EXCEEDED', 'ML', 'MLE', 'MEMORY_LIMIT_EXCEEDED',
            'RE', 'RT', 'RTE', 'RUNTIME_ERROR', 'OL', 'OLE', 'OUTPUT_LIMIT_EXCEEDED',
            'IL', 'ILE', 'IDLENESS_LIMIT_EXCEEDED', 'JE', 'JUDGEMENT_FAILED', 'HACKED'}
SHORT = {'ACCEPTED': 'AC', 'CORRECT': 'AC', 'OK': 'AC', 'WRONG_ANSWER': 'WA',
         'TIME_LIMIT_EXCEEDED': 'TLE', 'MEMORY_LIMIT_EXCEEDED': 'MLE', 'RUNTIME_ERROR': 'RE',
         'COMPILATION_ERROR': 'CE', 'PRESENTATION_ERROR': 'PE', 'OUTPUT_LIMIT_EXCEEDED': 'OLE',
         'JUDGEMENT_FAILED': 'JE', 'REJECTED': 'RJ', 'INCORRECT': 'RJ', 'TL': 'TLE',
         'ML': 'MLE', 'RT': 'RE', 'RTE': 'RE', 'OL': 'OLE', 'PD': 'PENDING'}


def localized(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        texts = value.get('texts', {})
        return str(texts.get('zh-CN') or texts.get(value.get('fallback_lang')) or
                   value.get('fallback') or next(iter(texts.values()), ''))
    return '' if value is None else str(value)


def epoch(value):
    if isinstance(value, (int, float)):
        return value / 1000 if value > 10**11 else float(value)
    return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()


def entries(data):
    if isinstance(data, list):
        return [dict(v, id=v.get('id', v.get('team_id', v.get('submission_id', str(i))))) for i, v in enumerate(data)]
    if isinstance(data, dict):
        return [dict(v, id=v.get('id', k)) for k, v in data.items()]
    raise ValueError('榜单数据应为数组或对象')


def board_source(value):
    """Only accept paths under the known public data host; never proxy arbitrary URLs."""
    value = str(value).strip()
    if not value:
        return '', '', ''
    u = urlsplit(value)
    path = unquote(u.path).strip('/')
    if u.scheme != 'https' or u.fragment:
        raise ValueError('请输入 XCPCIO 或 rank.iccpc.cn 的 HTTPS 榜单链接')
    if u.netloc == 'rank.iccpc.cn':
        match = re.fullmatch(r'(?:r/|upload/outrank_attach/)([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:/rank\.json)?', path)
        if not match:
            raise ValueError('长春榜单链接应为 https://rank.iccpc.cn/r/榜单ID')
        uid = match[1].lower()
        return 'iccpc:' + uid, 'https://rank.iccpc.cn/r/' + uid, 'official'
    if u.netloc != 'board.xcpcio.com':
        raise ValueError('支持 board.xcpcio.com 和 rank.iccpc.cn 的榜单链接')
    if path.startswith('data/'):
        path = path[5:]
    path = re.sub(r'/config\.json$', '', path)
    if not re.fullmatch(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)+', path):
        raise ValueError('榜单路径格式不正确')
    group = parse_qs(u.query).get('group', ['official'])[0][:80]
    return path, f'https://board.xcpcio.com/{path}?group={group}', group


def source_key(path):
    return hashlib.sha256(path.encode()).hexdigest()[:20]


def warmup_path(path):
    # Only this naming convention identifies the same contest, year and site.
    return path + '-warmup' if path and not path.startswith('iccpc:') and not path.endswith('-warmup') else ''


def matching_teams(old, new):
    """Verify team identity before carrying preferences across scoreboard IDs."""
    def identities(raw):
        orgs = {str(o['id']): localized(o.get('name')) for o in entries(raw.get('organizations', []))}
        return {str(t['id']): tuple(''.join(v.split()).casefold() for v in (
            localized(t.get('name', t.get('team_name'))),
            orgs.get(str(t.get('organization_id', '')), localized(t.get('organization', t.get('organization_id'))))))
                for t in entries(raw['teams'])}
    before, after = identities(old), identities(new)
    by_identity, old_counts = {}, {}
    for identity in before.values():
        old_counts[identity] = old_counts.get(identity, 0) + 1
    for tid, identity in after.items():
        by_identity.setdefault(identity, []).append(tid)
    result = {}
    for tid, identity in before.items():
        if not identity[0]:
            continue
        if after.get(tid) == identity:
            result[tid] = tid
        elif len(by_identity.get(identity, [])) == 1 and old_counts[identity] == 1:
            result[tid] = by_identity[identity][0]
    return result


def merge_public_profile(target, previous, team_map):
    """Keep current edits and merge verified members into existing named groups."""
    notes = target.setdefault('notes', {})
    for tid, note in previous.get('notes', {}).items():
        if tid in team_map:
            notes.setdefault(team_map[tid], note)
    groups = target.setdefault('public_groups', [])
    group_map = {}
    for old in previous.get('public_groups', []):
        members = [team_map[t] for t in old['teams'] if t in team_map]
        if old['teams'] and not members:
            continue
        same_name = [g for g in groups if g['name'] == old['name']]
        group = next((g for g in groups if g['id'] == old['id']), None)
        if group is None and len(same_name) == 1:
            group = same_name[0]
        if group is None:
            group = dict(id=old['id'], name=old['name'], teams=[])
            groups.append(group)
        group['teams'] = list(dict.fromkeys(group['teams'] + members))
        group_map[old['id']] = group['id']
    if 'default_group' not in target:
        target['default_group'] = group_map.get(previous.get('default_group'), 'all')


def room_id(value):
    value = str(value).strip()
    if not value:
        return ''
    if value.isdigit() and len(value) <= 16:
        return value
    u = urlsplit(value)
    if u.scheme == 'https' and u.netloc == 'live.bilibili.com':
        part = u.path.strip('/').split('/')[0]
        if part.isdigit() and len(part) <= 16:
            return part
    raise ValueError('直播请填写 B 站房间号或 https://live.bilibili.com/房间号')


def media_item(value):
    """Normalize Bilibili identifiers; embed URLs are always constructed here."""
    if not isinstance(value, dict) or not isinstance(value.get('url'), str):
        raise ValueError('播放列表格式不正确')
    text = value['url'].strip()
    title = str(value.get('title', '')).strip()[:100]
    page = 1
    video = ''
    room = ''
    if text.isascii() and text.isdigit() and len(text) <= 16:
        room = text
    elif re.fullmatch(r'(?i)(BV[0-9a-z]{10}|av[0-9]{1,20})', text):
        video = text
    else:
        u = urlsplit(text)
        if u.scheme not in ['http', 'https'] or u.netloc not in ['live.bilibili.com', 'www.bilibili.com', 'bilibili.com', 'm.bilibili.com', 'player.bilibili.com'] or u.fragment:
            raise ValueError('请填写 B 站直播间、视频完整链接或 BV/av 号')
        query = parse_qs(u.query)
        if u.netloc == 'live.bilibili.com':
            room = room_id('https://live.bilibili.com' + u.path)
        else:
            if u.netloc == 'player.bilibili.com' and u.path == '/player.html':
                video = query.get('bvid', [''])[0] or ('av' + query.get('aid', [''])[0])
            else:
                match = re.fullmatch(r'/video/((?i:BV[0-9a-z]{10}|av[0-9]{1,20}))/?', u.path)
                if match:
                    video = match[1]
            p = query.get('p', ['1'])[0]
            if not re.fullmatch(r'[0-9]{1,4}', p) or not 1 <= int(p) <= 1000:
                raise ValueError('视频分 P 序号应在 1–1000 之间')
            page = int(p)
    if room:
        url = 'https://live.bilibili.com/' + room
        embed = 'https://www.bilibili.com/blackboard/live/live-activity-player.html?' + urlencode(dict(cid=room, quality=4, autoplay=0))
        kind, fallback = 'live', 'B 站直播 · ' + room
    elif re.fullmatch(r'(?i)BV[0-9a-z]{10}', video) or re.fullmatch(r'(?i)av[0-9]{1,20}', video):
        video = ('BV' + video[2:]) if video[:2].upper() == 'BV' else ('av' + str(int(video[2:])))
        url = 'https://www.bilibili.com/video/' + video + ('?p=' + str(page) if page != 1 else '')
        params = {'bvid': video} if video.startswith('BV') else {'aid': video[2:]}
        embed = 'https://player.bilibili.com/player.html?' + urlencode(dict(params, p=page, autoplay=0, poster=1))
        kind, fallback = 'video', 'B 站视频 · ' + video + (' · P' + str(page) if page != 1 else '')
    else:
        raise ValueError('仅支持 B 站直播间和 BV/av 普通视频，请复制完整链接')
    return dict(id='m' + hashlib.sha256(url.encode()).hexdigest()[:16], type=kind,
                title=title or fallback, url=url, embed_url=embed)


def station_media(station):
    items = station.get('media')
    if items is None:
        items = [dict(url=station['room'])] if station.get('room') else []
    return [media_item(item) for item in items]


def clean_media(items):
    if not isinstance(items, list) or len(items) > 20:
        raise ValueError('每个赛站最多支持 20 个直播或视频')
    result, seen = [], set()
    for item in items:
        clean = media_item(item)
        if clean['id'] in seen:
            raise ValueError('播放列表中有重复链接，同一视频不同分 P 可分别添加')
        seen.add(clean['id'])
        result.append({key: clean[key] for key in ['type', 'title', 'url']})
    return result


def build_board(raw, station, now=None):
    if raw.get('provider') == 'iccpc':
        return build_iccpc(raw, station, now)
    now = time.time() if now is None else now
    c = raw['config']
    start, end = epoch(c['start_time']), epoch(c['end_time'])
    duration = end - start
    if duration <= 0:
        raise ValueError('比赛起止时间不正确')
    upstream_freeze = epoch(c['freeze_time']) - start if c.get('freeze_time') is not None else duration - float(c.get('frozen_time') or 0)
    freeze = max(0, min(duration, upstream_freeze, float(station.get('freeze_after', 4)) * 3600))
    reveal = bool(station.get('reveal', False))
    frozen = not reveal and freeze < duration and now >= start + freeze
    ps = c.get('problems')
    if ps is None:
        ps = [dict(id=str(i), label=p, balloon_color=(c.get('balloon_color', []) + [{}] * 30)[i])
              for i, p in enumerate(c.get('problem_id', []))]
    problems = [dict(id=str(p['id']), label=str(p.get('label', p['id'])), accepted=0,
                     color=p.get('balloon_color', {}).get('background_color', '#e6e9ef')) for p in ps]
    if not problems:
        raise ValueError('未找到题目信息')
    pmap = {p['id']: i for i, p in enumerate(problems)}
    orgs = {str(o['id']): localized(o.get('name')) for o in entries(raw.get('organizations', []))}
    teams = {}
    for t in entries(raw['teams']):
        groups = t.get('group', [])[:]
        for g in ['official', 'unofficial', 'girl']:
            if t.get(g) and g not in groups:
                groups.append(g)
        members = t.get('members') or []
        if isinstance(members, str):
            members = [members]
        teams[str(t['id'])] = dict(id=str(t['id']), name=localized(t.get('name', t.get('team_name'))),
            organization=orgs.get(str(t.get('organization_id', '')), localized(t.get('organization'))),
            groups=groups, location=str(t.get('location', '')), members=[localized(m.get('name') if isinstance(m, dict) else m) for m in members],
            solved=0, penalty=0, last_solve=0, problems=[dict(state='none', wrong=0, pending=0, minute=None, first=False) for _ in problems])
    unit = c.get('options', {}).get('submission_timestamp_unit', 'second')
    penalty_mode = c.get('options', {}).get('calculation_of_penalty', 'in_minutes')
    first = {}
    events = []
    runs = entries(raw['runs'])
    runs.sort(key=lambda r: (float(r['timestamp']), str(r['team_id']), str(r.get('status', '')).upper() not in AC, str(r['id'])))
    seen = set()
    for r in runs:
        rid, tid, pid = str(r['id']), str(r['team_id']), str(r['problem_id'])
        if rid in seen:
            continue
        seen.add(rid)
        sec = float(r['timestamp']) / (1000 if unit == 'millisecond' else 1)
        if tid not in teams or pid not in pmap or sec < 0 or sec >= duration or sec > now - start:
            continue
        status = str(r.get('status', 'UNKNOWN')).upper().replace(' ', '_')
        if frozen and sec >= freeze:
            status = 'FROZEN'
        status_short = 'AC' if status in AC else SHORT.get(status, status)
        if status in PENDING and status != 'FROZEN':
            status_short = 'PENDING'
        team = teams[tid]
        p = team['problems'][pmap[pid]]
        events.append(dict(id=rid, team_id=tid, problem=problems[pmap[pid]]['label'], status=status_short, second=sec))
        if p['state'] == 'ac' or r.get('is_ignore'):
            continue
        if status in AC:
            p.update(state='ac', minute=math.floor(sec / 60), pending=0)
            p['first'] = sec == first.setdefault(pid, sec)
            problems[pmap[pid]]['accepted'] += 1
            team['solved'] += 1
            solve_penalty = sec if penalty_mode != 'in_minutes' else math.floor(sec / 60) * 60
            team['penalty'] += solve_penalty + p['wrong'] * float(c.get('penalty', 1200))
            team['last_solve'] = math.floor(sec / 60)
        elif status in PENDING:
            p['pending'] += 1
            p['state'] = 'frozen' if status == 'FROZEN' else 'pending'
        elif status in REJECTED:
            p['wrong'] += 1
            if not p['pending']:
                p['state'] = 'wa'
    rows = list(teams.values())
    if penalty_mode == 'accumulate_in_seconds_and_finally_to_the_minute':
        for t in rows:
            t['penalty'] = math.floor(t['penalty'] / 60) * 60
    rows.sort(key=lambda t: (-t['solved'], t['penalty'], t['last_solve'], t['name']))
    # Ranks are computed across the upstream group, independently of spectator views.
    for group in ['all'] + sorted({g for t in rows for g in t['groups']}):
        subset = rows if group == 'all' else [t for t in rows if group in t['groups']]
        prev = None
        rank = 0
        for i, t in enumerate(subset, 1):
            score = (t['solved'], t['penalty'], t['last_solve'])
            if score != prev:
                rank = i
            t.setdefault('ranks', {})[group] = rank
            prev = score
    for t in rows:
        t['penalty'] = round(t['penalty'] / 60, 2)
    state = 'pending' if now < start else ('finished' if now >= end else ('frozen' if frozen else 'running'))
    return dict(title=localized(c.get('contest_name')), start=start, end=end, freeze_at=start + freeze,
                frozen=frozen, revealed=reveal, state=state, problems=problems, teams=rows,
                events=sorted(events, key=lambda e: (e['second'], e['id']))[-800:], groups=c.get('group', {}),
                total_runs=len(events), source=source_key(board_source(station['board_url'])[0]))


def iccpc_raw(payload, path):
    """Keep the public provider snapshot intact on disk, never infer hidden IDs."""
    if not isinstance(payload, dict) or not isinstance(payload.get('ccpc_meta'), dict):
        raise ValueError('CCPC 榜单元数据缺失，保留最近快照')
    if not all(isinstance(payload.get(k), list) for k in ['team', 'problem', 'solution']):
        raise ValueError('CCPC 榜单数据格式不正确')
    return dict(provider='iccpc', path=path, config=payload['contest'], teams=payload['team'],
                runs=payload['solution'], organizations=[], problems=payload['problem'],
                ccpc_meta=payload['ccpc_meta'], time_context=payload.get('time_context', {}),
                contest_group=payload.get('contest_group', []), fetched_at=time.time())


def build_iccpc(raw, station, now=None):
    now = time.time() if now is None else now
    def instant(value):
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(raw.get('time_context', {}).get('wall_clock_timezone', 'Asia/Shanghai')))
        return parsed.timestamp()
    def tid(value):
        text = str(value)
        return text.split('_', 1)[1] if text.startswith('#cpc') and '_' in text else text
    def cell():
        return dict(state='none', wrong=0, pending=0, minute=None, first=False)
    c, meta = raw['config'], raw['ccpc_meta']
    start, end = instant(c['start_time']), instant(c['end_time'])
    duration = end - start
    if duration <= 0:
        raise ValueError('比赛起止时间不正确')
    freeze = max(0, min(duration, duration - float(c.get('frozen_minute') or 0) * 60,
                        float(station.get('freeze_after', 4)) * 3600))
    reveal = bool(station.get('reveal', False))
    frozen = not reveal and freeze < duration and now >= start + freeze
    show_all = meta.get('view') == 'referee' or bool(meta.get('frozen_show_all'))
    teams, stats = {}, {}
    for t in raw['teams']:
        team_id = tid(t['team_id'])
        kind = int(t.get('tkind') or 0)
        groups = ['unofficial'] if kind == 2 else ['official']
        if kind == 1:
            groups.append('girl')
        members = str(t.get('tmember') or '').split('、')
        teams[team_id] = dict(id=team_id, name=str(t.get('name') or team_id), organization=str(t.get('school') or ''),
            location=str(t.get('room') or ''), groups=groups, members=[m for m in members if m],
            solved=0, penalty=0, last_solve=0)
        stats[team_id] = {}
    verdicts = {4:'AC', 5:'PE', 6:'WA', 7:'TLE', 8:'MLE', 9:'OLE', 10:'RE', 11:'CE'}
    events, seen, accepted = [], set(), {}
    ordered = sorted(raw['runs'], key=lambda r: (instant(r['in_date']), int(r['solution_id'])))
    for r in ordered:
        rid, team_id, pid = str(r['solution_id']), tid(r['team_id']), str(r['problem_id'])
        sec = instant(r['in_date']) - start
        if rid in seen or team_id not in teams or not 0 <= sec < duration or sec > now - start:
            continue
        seen.add(rid)
        result = int(r['result'])
        status = 'FROZEN' if result < 0 or (frozen and sec >= freeze) else verdicts.get(result, 'PENDING')
        events.append(dict(id=rid, team_id=team_id, _pid=pid, status=status, second=sec))
        p = stats[team_id].setdefault(pid, cell())
        if p['state'] == 'ac':
            continue
        if status == 'AC':
            p.update(state='ac', minute=math.floor(sec / 60), pending=0, _last=sec)
            accepted.setdefault(pid, {})[team_id] = sec
            teams[team_id]['solved'] += 1
            teams[team_id]['penalty'] += math.floor(sec) + p['wrong'] * 1200
        elif status in ['FROZEN', 'PENDING']:
            p['pending'] += 1
            p.update(state='frozen' if status == 'FROZEN' else 'pending', _last=sec)
        elif 5 <= result <= 10:
            p['wrong'] += 1
            p['_last'] = sec
            if not p['pending']:
                p['state'] = 'wa'
    threshold = int(meta.get('threshold') or min(50, max(1, math.floor(.2 * len(teams)))))
    problem_map = {}
    for p in raw['problems']:
        num = p.get('num')
        pid = str(p['problem_id'])
        if num is not None and str(num).isdigit() and (show_all or len(accepted.get(pid, {})) >= threshold):
            problem_map[pid] = dict(id=str(int(num)), label=chr(65 + int(num)), color=p.get('color') or '#e6e9ef',
                                    accepted=len(accepted.get(pid, {})))
    public_ids = sorted(problem_map, key=lambda pid: int(problem_map[pid]['id']))
    for team_id, t in teams.items():
        own = stats[team_id]
        for pid in public_ids:
            p = own.setdefault(pid, cell())
            if p['state'] == 'ac':
                p['first'] = p['_last'] == min(accepted[pid].values())
        hidden = [p for pid, p in own.items() if pid not in problem_map and p['state'] != 'none'] if not show_all else []
        hidden.sort(key=lambda p: (p['state'] != 'ac', p.get('_last', 0)))
        # Neither provider IDs nor implied column numbers leave this adapter for hidden cells.
        t['hidden_problems'] = [{k:v for k,v in p.items() if not k.startswith('_')} for p in hidden]
        t['problems'] = [{k:v for k,v in own[pid].items() if not k.startswith('_')} for pid in public_ids]
    for e in events:
        pid = e.pop('_pid')
        e['problem'] = problem_map[pid]['label'] if pid in problem_map else '?'
    rows = sorted(teams.values(), key=lambda t: (-t['solved'], t['penalty'], t['id']))
    for group in ['all', 'official', 'unofficial', 'girl']:
        previous, rank = None, 0
        for i, t in enumerate([t for t in rows if group == 'all' or group in t['groups']], 1):
            score = (t['solved'], math.floor(t['penalty'] / 60))
            if score != previous:
                rank = i
            t.setdefault('ranks', {})[group] = rank
            previous = score
    for t in rows:
        t['penalty'] = math.floor(t['penalty'] / 60)
    state = 'pending' if now < start else ('finished' if now >= end else ('frozen' if frozen else 'running'))
    return dict(title=c.get('title', ''), start=start, end=end, freeze_at=start + freeze, frozen=frozen,
                revealed=reveal, state=state, problems=[problem_map[pid] for pid in public_ids], teams=rows,
                events=events[-800:], total_runs=len(events), source=source_key(raw['path']),
                groups=dict(official='正式队伍', unofficial='打星队伍', girl='女队'),
                problem_total=int(meta.get('n_problems') or len(raw['problems'])),
                problem_reveal=dict(threshold=threshold, show_all=show_all, hidden=not show_all))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('数据源返回了重定向')


class Fetcher:
    def __init__(self):
        self.cache = {}
        self.opener = build_opener(NoRedirect)
        self.lock = threading.Lock()

    def get(self, url, ttl=0):
        if not url.startswith('https://board.xcpcio.com/data/') and not re.fullmatch(r'https://rank\.iccpc\.cn/upload/outrank_attach/[0-9a-f-]{36}/rank\.json', url) and url != 'https://cpc.csgrandeur.cn/upload/outrank_attach/88998553-ce66-46ec-a062-1beb357d774c/rank.json':
            raise ValueError('不支持的数据源')
        with self.lock:
            old = self.cache.get(url)
        if old and time.time() - old['time'] < ttl:
            return old['data']
        headers = {'User-Agent': 'Wannafly-XCPCBoard/2.0 (15s cached spectator board)', 'Accept': 'application/json'}
        if old:
            headers.update(old['validators'])
        try:
            with self.opener.open(Request(url, headers=headers), timeout=12) as r:
                content = r.read(32 * 1024 * 1024 + 1)
                if len(content) > 32 * 1024 * 1024:
                    raise ValueError('上游数据超过大小限制')
                data = json.loads(content)
                validators = {}
                for key, target in [('ETag', 'If-None-Match'), ('Last-Modified', 'If-Modified-Since')]:
                    if r.headers.get(key):
                        validators[target] = r.headers[key]
        except HTTPError as exc:
            if exc.code != 304 or not old:
                raise
            data, validators = old['data'], old['validators']
        with self.lock:
            self.cache[url] = dict(data=data, validators=validators, time=time.time())
        return data


def atomic_json(path, data, compressed=False):
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()
    if compressed:
        payload = gzip.compress(payload)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with open(tmp, 'wb') as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.refresh_locks = {}
        self.raw = {}
        self.payload_cache = {}
        self.preference_cache = {}
        self.health = {}
        self.fetcher = Fetcher()
        self.wakeup = threading.Event()
        self.config_path = self.directory / 'config.json'
        if self.config_path.exists():
            self.config = json.loads(self.config_path.read_text())
        else:
            self.config = {'contests': [
                dict(id='xian', title='ICPC 西安', board_url='https://board.xcpcio.com/icpc/51st/xian-warmup?group=official', room='', freeze_after=4, reveal=False, profiles={}),
                dict(id='changchun', title='CCPC 长春', board_url='https://rank.iccpc.cn/r/8a3a28e3-f6b3-4a58-b4ac-fa8039fb2c72', room='', freeze_after=4, reveal=False, profiles={})]}
            atomic_json(self.config_path, self.config)
        for station in self.config['contests']:
            path = board_source(station['board_url'])[0]
            if path:
                self.load(path)
                previous = warmup_path(path)
                if previous:
                    self.load(previous)
                self.inherit_preferences(path)

    def load(self, path):
        key = source_key(path)
        if key in self.raw:
            return
        for suffix in ['.json.gz', '.previous.json.gz']:
            try:
                data = json.loads(gzip.decompress((self.directory / (key + suffix)).read_bytes()))
                if data['path'] != path:
                    continue
                self.raw[key] = data
                self.health[key] = dict(last_success=data['fetched_at'], error='', checking=False)
                return
            except (OSError, ValueError, KeyError, EOFError):
                continue

    def preference_sources(self, path):
        previous = warmup_path(path)
        if not previous:
            return []
        key, old_key = source_key(path), source_key(previous)
        with self.lock:
            old, new = self.raw.get(old_key), self.raw.get(key)
            if not old or not new:
                return []
            cached = self.preference_cache.get(key)
            if not cached or cached[0] is not old or cached[1] is not new:
                cached = (old, new, matching_teams(old, new))
                self.preference_cache[key] = cached
            return [dict(source=old_key, team_map=cached[2])] if cached[2] else []

    def inherit_preferences(self, path):
        with self.lock:
            links = self.preference_sources(path)
            if not links:
                return
            config = copy.deepcopy(self.config)
            changed = False
            for station in config['contests']:
                if board_source(station['board_url'])[0] != path:
                    continue
                profiles = station.setdefault('profiles', {})
                target = profiles.setdefault(source_key(path), {})
                for link in links:
                    previous = profiles.get(link['source'], {})
                    if link['source'] in target.get('inherited_sources', []) or not (previous.get('notes') or previous.get('public_groups')):
                        continue
                    merge_public_profile(target, previous, link['team_map'])
                    target.setdefault('inherited_sources', []).append(link['source'])
                    changed = True
            if changed:
                atomic_json(self.config_path, config)
                self.config = config

    def public_station(self, station):
        path, _, group = board_source(station['board_url'])
        profile = station.get('profiles', {}).get(source_key(path), {})
        links = copy.deepcopy(self.preference_sources(path))
        for link in links:
            previous = station.get('profiles', {}).get(link['source'], {})
            link['public_group_map'] = {}
            for old in previous.get('public_groups', []):
                matches = [g for g in profile.get('public_groups', []) if g['id'] == old['id'] or g['name'] == old['name']]
                if len(matches) == 1:
                    link['public_group_map'][old['id']] = matches[0]['id']
        return {**{k: v for k, v in station.items() if k != 'profiles'}, 'source': source_key(path), 'media': station_media(station),
                'preference_sources': links,
                'upstream_group': group, 'notes': profile.get('notes', {}),
                'public_groups': profile.get('public_groups', []), 'default_group': profile.get('default_group', 'all')}

    def stations(self):
        with self.lock:
            return [self.public_station(s) for s in self.config['contests']]

    def station(self, sid):
        with self.lock:
            return copy.deepcopy(next(s for s in self.config['contests'] if s['id'] == sid))

    def update(self, sid, data):
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,39}', sid):
            raise ValueError('赛站 ID 仅支持小写字母、数字和短横线')
        with self.lock:
            config = copy.deepcopy(self.config)
            station = next((s for s in config['contests'] if s['id'] == sid), None)
            if station is None:
                if len(config['contests']) >= 20:
                    raise ValueError('最多支持 20 个赛站')
                station = dict(id=sid, title=sid, board_url='', room='', freeze_after=4, reveal=False, profiles={})
                config['contests'].append(station)
            if 'title' in data:
                station['title'] = str(data['title']).strip()[:80] or sid
            if 'board_url' in data:
                station['board_url'] = board_source(data['board_url'])[1]
            if 'room' in data:
                station['room'] = room_id(data['room'])
                if 'media' in station and 'media' not in data:
                    items = copy.deepcopy(station['media'])
                    index = next((i for i, item in enumerate(items) if item['type'] == 'live'), None)
                    if station['room']:
                        item = dict(url=station['room'], title=items[index]['title'] if index is not None else '')
                        if index is None:
                            items.insert(0, item)
                        else:
                            items[index] = item
                    elif index is not None:
                        items.pop(index)
                    station['media'] = clean_media(items)
            if 'media' in data:
                station['media'] = clean_media(data['media'])
                station['room'] = next((item['url'].rsplit('/', 1)[-1] for item in station['media'] if item['type'] == 'live'), '')
            if 'freeze_after' in data:
                value = float(data['freeze_after'])
                if not math.isfinite(value) or not 0 <= value <= 24:
                    raise ValueError('封榜时间应为开赛后 0–24 小时')
                station['freeze_after'] = value
            if 'reveal' in data:
                if not isinstance(data['reveal'], bool):
                    raise ValueError('解封选项格式错误')
                station['reveal'] = data['reveal']
            path = board_source(station['board_url'])[0]
            key = source_key(path)
            if any(k in data for k in ['notes', 'public_groups', 'default_group']):
                if data.get('source') != key:
                    raise ValueError('榜单已切换，请刷新后编辑分组和备注')
                profile = station['profiles'].setdefault(key, {})
                if 'notes' in data:
                    if not isinstance(data['notes'], dict) or len(data['notes']) > 5000:
                        raise ValueError('备注格式错误')
                    profile['notes'] = {str(k)[:100]: str(v)[:100] for k, v in data['notes'].items() if str(v).strip()}
                if 'public_groups' in data:
                    groups = data['public_groups']
                    if not isinstance(groups, list) or len(groups) > 50:
                        raise ValueError('最多支持 50 个公共分组')
                    clean = []
                    for g in groups:
                        gid = str(g['id'])
                        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,60}', gid) or gid in [x['id'] for x in clean] or not isinstance(g['teams'], list):
                            raise ValueError('分组格式错误')
                        clean.append(dict(id=gid, name=str(g['name'])[:60], teams=list(dict.fromkeys(str(t)[:100] for t in g['teams']))[:5000]))
                    profile['public_groups'] = clean
                if 'default_group' in data:
                    value = str(data['default_group'])
                    if value != 'all' and value not in [g['id'] for g in profile.get('public_groups', [])]:
                        raise ValueError('公共默认分组不存在')
                    profile['default_group'] = value
                if profile.get('default_group', 'all') not in ['all'] + [g['id'] for g in profile.get('public_groups', [])]:
                    profile['default_group'] = 'all'
            atomic_json(self.config_path, config)
            self.config = config
            self.load(path) if path else None
            if warmup_path(path):
                self.load(warmup_path(path))
            self.inherit_preferences(path)
        self.wakeup.set()
        return self.public_station(self.station(sid))

    def refresh(self, station):
        path = board_source(station['board_url'])[0]
        if not path:
            return
        key = source_key(path)
        with self.lock:
            guard = self.refresh_locks.setdefault(key, threading.Lock())
        if not guard.acquire(blocking=False):
            return
        try:
            with self.lock:
                self.health.setdefault(key, {})['checking'] = True
            if path.startswith('iccpc:'):
                raw = iccpc_raw(self.fetcher.get('https://rank.iccpc.cn/upload/outrank_attach/' + path.split(':')[1] + '/rank.json'), path)
            else:
                base = f'https://board.xcpcio.com/data/{path}/'
                c = self.fetcher.get(base + 'config.json', ttl=60)
                with ThreadPoolExecutor(max_workers=3) as pool:
                    ft = pool.submit(self.fetcher.get, base + 'team.json', 60)
                    fr = pool.submit(self.fetcher.get, base + 'run.json')
                    org = c.get('organizations', [])
                    if isinstance(org, dict) and 'url' in org:
                        # DataItem URLs must remain local to this contest directory.
                        if not re.fullmatch(r'[A-Za-z0-9_-]+\.json', org['url']):
                            raise ValueError('不支持的学校数据路径')
                        org = self.fetcher.get(base + org['url'], ttl=300)
                    raw = dict(path=path, config=c, teams=ft.result(), runs=fr.result(), organizations=org, fetched_at=time.time())
            with self.lock:
                previous = self.raw.get(key)
            if previous and ((entries(previous['teams']) and not entries(raw['teams'])) or (entries(previous['runs']) and not entries(raw['runs']))):
                raise ValueError('上游返回了空榜，保留最近一次完整快照')
            build_board(raw, station)  # Validate before replacing a known-good snapshot.
            target = self.directory / (key + '.json.gz')
            changed = not previous or {k:v for k,v in raw.items() if k != 'fetched_at'} != {k:v for k,v in previous.items() if k != 'fetched_at'}
            if changed or time.time() - (target.stat().st_mtime if target.exists() else 0) >= 60:
                if target.exists() and changed:
                    atomic_json(self.directory / (key + '.previous.json.gz'), previous, compressed=True)
                atomic_json(target, raw, compressed=True)
            with self.lock:
                self.raw[key] = raw
                self.health[key] = dict(last_success=raw['fetched_at'], last_attempt=time.time(), error='', checking=False)
                self.inherit_preferences(path)
        except Exception as exc:
            LOG.warning('Refresh %s failed: %s', path, exc)
            with self.lock:
                self.health.setdefault(key, {}).update(error=str(exc)[:240], last_attempt=time.time(), checking=False)
        finally:
            guard.release()

    def board(self, sid):
        station = self.station(sid)
        path = board_source(station['board_url'])[0]
        key = source_key(path)
        with self.lock:
            raw = self.raw.get(key)
            health = copy.deepcopy(self.health.get(key, {}))
        age = max(0, time.time() - health.get('last_success', 0)) if raw else None
        return dict(station=self.public_station(station), board=build_board(raw, station) if raw else None,
                    health={**health, 'age': age, 'stale': bool(raw and (health.get('error') or age > POLL * 3)),
                            'configured': bool(path), 'poll_seconds': POLL}, server_time=time.time())

    def worker(self):
        while True:
            self.wakeup.clear()
            stations = self.stations()
            unique = {s['source']: s for s in stations if s['board_url']}
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(self.refresh, unique.values()))
            self.wakeup.wait(POLL)

    def payload(self, sid, compressed=False):
        # All visitors share scoring, serialization and gzip work. The one-second
        # window also advances start/freeze boundaries when upstream stops pushing.
        with self.lock:
            s = self.station(sid)
            key = source_key(board_source(s['board_url'])[0])
            signature = (id(self.config), id(self.raw.get(key)), int(time.time()))
            cached = self.payload_cache.get(sid)
            if not cached or cached[0] != signature:
                plain = json.dumps(self.board(sid), ensure_ascii=False, separators=(',', ':')).encode()
                cached = (signature, plain, gzip.compress(plain, compresslevel=5))
                self.payload_cache[sid] = cached
            return cached[2 if compressed else 1]


class Handler(BaseHTTPRequestHandler):
    server_version = 'XCPCBoard/2'

    def log_message(self, fmt, *args):
        if len(args) > 1 and str(args[1]).startswith(('4', '5')):
            LOG.info('%s %s', self.address_string(), fmt % args)

    def send(self, status, data, mime='application/json; charset=utf-8', encoded_gzip=False):
        body = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode() if not isinstance(data, bytes) else data
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Cache-Control', 'no-store' if self.path.startswith('/api/') else 'no-cache')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')
        self.send_header('X-Frame-Options', 'SAMEORIGIN')
        scripts = "'self' 'unsafe-inline'" if urlsplit(self.path).path in ['/legacy', '/legacy/'] else "'self'"
        self.send_header('Content-Security-Policy', f"default-src 'self'; script-src {scripts}; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-src https://www.bilibili.com https://live.bilibili.com https://player.bilibili.com; connect-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'")
        if encoded_gzip or (len(body) > 2048 and 'gzip' in self.headers.get('Accept-Encoding', '')):
            if not encoded_gzip:
                body = gzip.compress(body, compresslevel=5)
            self.send_header('Content-Encoding', 'gzip')
            self.send_header('Vary', 'Accept-Encoding')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def authorized(self):
        token = self.server.token
        supplied = self.headers.get('Authorization', '').removeprefix('Bearer ')
        if not token or not hmac.compare_digest(supplied.encode(), token.encode()):
            self.send(401, {'error': '管理密钥不正确'})
            return False
        return True

    def do_GET(self):
        path = urlsplit(self.path).path
        store = self.server.store
        if path == '/api/contests':
            self.send(200, {'contests': store.stations(), 'poll_seconds': POLL})
        elif re.fullmatch(r'/api/contests/[a-z0-9-]+', path):
            try:
                compressed = 'gzip' in self.headers.get('Accept-Encoding', '')
                self.send(200, store.payload(path.split('/')[-1], compressed), encoded_gzip=compressed)
            except StopIteration:
                self.send(404, {'error': '赛站不存在'})
        elif path == '/api/admin/session':
            if self.authorized():
                self.send(200, {'ok': True})
        elif path == '/api/health':
            self.send(200, {'ok': True, 'contests': len(store.stations()), 'time': time.time()})
        elif path == '/' or path == '/index.html':
            self.send(200, (ROOT / 'index.html').read_bytes(), 'text/html; charset=utf-8')
        elif path in ['/static/app.js', '/static/preferences.js', '/static/style.css']:
            self.send(200, (ROOT / path.lstrip('/')).read_bytes(), 'application/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')
        elif path in ['/legacy', '/legacy/']:
            self.send(200, (ROOT / 'legacy/index.html').read_bytes(), 'text/html; charset=utf-8')
        elif path == '/favicon.svg':
            self.send(200, b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40"><rect width="40" height="40" rx="12" fill="#27785f"/><path d="M10 27V18h5v9zm8 0V10h5v17zm8 0V14h5v13z" fill="white"/></svg>', 'image/svg+xml')
        elif path == '/api/hbcpc/rank':
            try:
                self.send(200, store.fetcher.get('https://cpc.csgrandeur.cn/upload/outrank_attach/88998553-ce66-46ec-a062-1beb357d774c/rank.json', 300))
            except Exception:
                self.send(502, {'error': '历史榜单上游暂不可用'})
        elif path in ['/api/dongbei/config', '/api/dongbei/team', '/api/dongbei/orgs', '/api/dongbei/run', '/api/nanchang/config', '/api/nanchang/team', '/api/nanchang/orgs', '/api/nanchang/run']:
            parts = path.split('/')
            prefix = 'provincial-contest/2026/northeastern' if parts[2] == 'dongbei' else 'ccpc/12th/nanchang-invitational'
            name = 'organizations' if parts[3] == 'orgs' else parts[3]
            try:
                self.send(200, store.fetcher.get(f'https://board.xcpcio.com/data/{prefix}/{name}.json', 300))
            except Exception:
                self.send(502, {'error': '历史榜单上游暂不可用'})
        else:
            self.send(404, {'error': 'Not found'})

    def do_PUT(self):
        path = urlsplit(self.path).path
        if not re.fullmatch(r'/api/admin/contests/[a-z0-9-]+', path):
            self.send(404, {'error': 'Not found'})
            return
        if not self.authorized():
            return
        # A bearer token plus same-origin check prevents cross-site config mutations.
        origin = self.headers.get('Origin')
        if origin and urlsplit(origin).netloc != self.headers.get('Host'):
            self.send(403, {'error': '不允许跨站修改配置'})
            return
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 2 * 1024 * 1024:
                raise ValueError('配置内容大小不正确')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('配置内容格式不正确')
            self.send(200, self.server.store.update(path.split('/')[-1], data))
        except (ValueError, KeyError, TypeError) as exc:
            self.send(400, {'error': str(exc)[:240]})
        except OSError:
            LOG.exception('Config persistence failed')
            self.send(500, {'error': '配置写入失败，请检查服务器磁盘'})


class BoardHTTPServer(ThreadingHTTPServer):
    request_queue_size = 128


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    store = Store(os.getenv('DATA_DIR', str(ROOT / 'data')))
    token = os.getenv('ADMIN_TOKEN', '')
    if len(token) < 20:
        raise SystemExit('Set ADMIN_TOKEN to a secret of at least 20 characters')
    server = BoardHTTPServer((os.getenv('BIND', '127.0.0.1'), int(os.getenv('PORT', '8766'))), Handler)
    server.store, server.token = store, token
    threading.Thread(target=store.worker, daemon=True).start()
    LOG.info('Listening on %s:%s; cache %s', *server.server_address, store.directory)
    server.serve_forever()


if __name__ == '__main__':
    main()
