"""Persistent public Codeforces ratings, refreshed independently of scoreboards."""
import copy
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


HANDLE = re.compile(r'[A-Za-z0-9_.-]{1,24}')
TTL = 24 * 3600
VISIT_COOLDOWN = 60


def clean_bindings(value, teams):
    if not isinstance(value, dict) or len(value) > 5000:
        raise ValueError('CF 绑定格式错误')
    by_id = {t['id']: t for t in teams}
    clean = {}
    for tid, bindings in value.items():
        if tid not in by_id:
            raise ValueError('CF 绑定的队伍不存在，请刷新名单')
        if not isinstance(bindings, list) or len(bindings) > 3:
            raise ValueError('每队最多绑定 3 个 CF 账号')
        result, members, handles = [], set(), set()
        roster = by_id[tid].get('members', [])
        for binding in bindings:
            if not isinstance(binding, dict):
                raise ValueError('CF 绑定格式错误')
            member, handle = binding.get('member'), binding.get('handle')
            if not isinstance(member, str) or not member.strip() or len(member) > 80:
                raise ValueError('请选择或填写 CF 账号对应的队员')
            if not isinstance(handle, str) or not HANDLE.fullmatch(handle.strip()):
                raise ValueError('CF 账号应为 1–24 位英文字母、数字、下划线、点或短横线')
            member, handle = member.strip(), handle.strip()
            if roster and member not in roster:
                raise ValueError('绑定的队员不在该队参赛名单中')
            if member in members or handle.casefold() in handles:
                raise ValueError('同一队员或 CF 账号不能重复绑定')
            members.add(member)
            handles.add(handle.casefold())
            result.append(dict(member=member, handle=handle))
        if result:
            clean[tid] = result
    return clean


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Ratings:
    def __init__(self, directory):
        self.path = Path(directory) / 'codeforces-cache.json'
        self.lock = threading.Lock()
        self.opener = build_opener(NoRedirect)
        self.last_request = 0
        self.busy = False
        self.inflight = set()
        self.wakeup = threading.Event()
        self.revision = 0
        self.pending = {}
        self.daily_date = ''
        self.contest_starts = {}
        try:
            saved = json.loads(self.path.read_text())
            self.users = saved['users'] if saved.get('version') == 1 and isinstance(saved['users'], dict) else {}
            self.pending = saved.get('pending', {})
            self.daily_date = saved.get('daily_date', '')
            self.contest_starts = saved.get('contest_starts', {})
        except (OSError, ValueError, KeyError, TypeError):
            self.users = {}
        self.last_request = max((u.get('last_attempt', 0) for u in self.users.values()), default=0)

    @staticmethod
    def handles(values):
        return {h.casefold():h for h in values if isinstance(h, str) and HANDLE.fullmatch(h)}

    def save(self):
        """Caller holds the lock; queue and trigger markers persist together."""
        data = dict(version=1, users=self.users, pending=self.pending,
                    daily_date=self.daily_date, contest_starts=self.contest_starts)
        temporary = self.path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False))
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
        self.revision += 1

    def enqueue(self, handles, reason, now):
        """Merge pending/in-flight work; visitors share a short freshness window."""
        added = 0
        for key, handle in self.handles(handles).items():
            if key in self.inflight:
                continue
            if key in self.pending:
                if reason in ('binding', 'contest', 'daily') and self.pending[key]['ready_at'] > now:
                    self.pending[key]['ready_at'] = now
                    added += 1
                continue
            if reason == 'visit' and now - self.users.get(key, {}).get('last_attempt', 0) < VISIT_COOLDOWN:
                continue
            self.pending[key] = dict(handle=handle, ready_at=now)
            added += 1
        return added

    def request_sync(self, handles, reason='binding', now=None):
        now = time.time() if now is None else now
        with self.lock:
            added = self.enqueue(handles, reason, now)
            if added:
                self.save()
        if added:
            self.wakeup.set()
        return added

    def schedule(self, handles, contests, now=None):
        """Daily in Shanghai time; catch up a kickoff only while it is live."""
        now = time.time() if now is None else now
        day = datetime.fromtimestamp(now, ZoneInfo('Asia/Shanghai')).date().isoformat()
        changed = False
        with self.lock:
            if self.daily_date != day:
                self.enqueue(handles, 'daily', now)
                self.daily_date = day
                changed = True
            for contest in contests:
                start, end = contest.get('start_time'), contest.get('end_time')
                if start is None or end is None or not start <= now < end:
                    continue
                event = contest['source'] + ':' + str(start)
                if event in self.contest_starts:
                    continue
                self.enqueue(contest.get('handles', []), 'contest', now)
                self.contest_starts[event] = now
                # Bound markers even if a station is reused for many years.
                self.contest_starts = dict(sorted(self.contest_starts.items(), key=lambda item:item[1])[-200:])
                changed = True
            if changed:
                self.save()

    def snapshot(self, handles, now=None):
        now = time.time() if now is None else now
        with self.lock:
            result = {}
            for handle in handles:
                key = handle.casefold()
                user = copy.deepcopy(self.users.get(key, {}))
                user['stale'] = bool(user.get('error') or now - user.get('updated_at', 0) >= TTL)
                user['syncing'] = key in self.inflight or self.pending.get(key, {}).get('ready_at', float('inf')) <= now
                result[key] = user
            return result

    def lookup_many(self, handles):
        # Fixed HTTPS endpoint; configured handles can never select another host.
        url = 'https://codeforces.com/api/user.info?' + urlencode(dict(handles=';'.join(handles), checkHistoricHandles='true', lang='en'))
        request = Request(url, headers={'User-Agent':'Wannafly-XCPCBoard/2.0', 'Accept':'application/json'})
        try:
            response = self.opener.open(request, timeout=8)
        except HTTPError as error:
            if error.code != 400:
                raise
            # CF sends validation failures, including unknown handles, as 400
            # with the same JSON envelope as successful responses.
            response = error
        with response as opened_response:
            content = opened_response.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise ValueError('CF response too large')
        data = json.loads(content)
        if data.get('status') != 'OK':
            comment = str(data.get('comment', ''))
            if 'not found' in comment.lower():
                missing = re.search(r'user with handle ["(]?([A-Za-z0-9_.-]+)', comment, re.I)
                if missing and missing[1].casefold() in self.handles(handles):
                    # CF rejects the entire batch for one unknown handle. Remove
                    # only that account; the other queued accounts run next.
                    return {missing[1].casefold():dict(error='账号不存在')}
            return {h.casefold():dict(error='CF 暂不可用') for h in handles}
        users = data.get('result')
        if not isinstance(users, list) or len(users) != len(handles):
            raise ValueError('Invalid CF user response')
        result = {}
        for handle, user in zip(handles, users):
            if not isinstance(user, dict) or not HANDLE.fullmatch(str(user.get('handle', ''))):
                raise ValueError('Invalid CF user response')
            rating = user.get('rating')
            if rating is not None and (type(rating) is not int or not 0 <= rating <= 10000):
                raise ValueError('Invalid CF rating')
            result[handle.casefold()] = dict(handle=user['handle'], rating=rating, rank=str(user.get('rank', ''))[:80], error='')
        return result

    def lookup(self, handle):
        return self.lookup_many([handle])[handle.casefold()]

    def refresh(self, handles, now=None):
        """One batch per call, at least three seconds between CF requests."""
        now = time.time() if now is None else now
        configured = self.handles(handles)
        with self.lock:
            if self.busy or now - self.last_request < 3:
                return False
            self.pending = {k:v for k,v in self.pending.items() if k in configured}
            # New uncached accounts also recover if an older server saved them
            # just before upgrading to the event-driven queue.
            self.enqueue([h for k,h in configured.items() if k not in self.users], 'missing', now)
            due = sorted((k for k,v in self.pending.items() if v['ready_at'] <= now), key=lambda k:self.pending[k]['ready_at'])[:100]
            if not due:
                return False
            batch = [self.pending[k]['handle'] for k in due]
            self.busy, self.last_request = True, now
            self.inflight = set(due)
        try:
            try:
                updates = self.lookup_many(batch)
            except (OSError, ValueError, KeyError, TypeError):
                updates = {k:dict(error='CF 暂不可用') for k in due}
            with self.lock:
                for key, updated in updates.items():
                    old = self.users.get(key, {})
                    user = {**old, **updated, 'last_attempt':now}
                    if not updated['error']:
                        user['updated_at'] = now
                    self.users[key] = user
                    if updated['error'] == 'CF 暂不可用':
                        self.pending[key]['ready_at'] = now + 300
                    else:
                        self.pending.pop(key, None)
                self.save()
            return True
        finally:
            with self.lock:
                self.busy = False
                self.inflight.clear()

    def worker(self, handles, contests=lambda:[]):
        while True:
            self.wakeup.clear()
            try:
                self.schedule(handles(), contests())
                self.refresh(handles())
            except Exception:
                logging.getLogger('xcpcboard').exception('CF cache refresh failed')
            self.wakeup.wait(3)
