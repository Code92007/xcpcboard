"""Persistent public Codeforces ratings, refreshed independently of scoreboards."""
import copy
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


HANDLE = re.compile(r'[A-Za-z0-9_.-]{1,24}')
TTL = 3600


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
        try:
            saved = json.loads(self.path.read_text())
            self.users = saved['users'] if saved.get('version') == 1 and isinstance(saved['users'], dict) else {}
        except (OSError, ValueError, KeyError, TypeError):
            self.users = {}

    def snapshot(self, handles, now=None):
        now = time.time() if now is None else now
        with self.lock:
            result = {}
            for handle in handles:
                key = handle.casefold()
                user = copy.deepcopy(self.users.get(key, {}))
                user['stale'] = bool(user.get('error') or now - user.get('updated_at', 0) >= TTL)
                result[key] = user
            return result

    def lookup(self, handle):
        # Fixed HTTPS endpoint; configured handles can never select another host.
        url = 'https://codeforces.com/api/user.info?' + urlencode(dict(handles=handle, checkHistoricHandles='true'))
        request = Request(url, headers={'User-Agent':'Wannafly-XCPCBoard/2.0', 'Accept':'application/json'})
        with self.opener.open(request, timeout=8) as response:
            content = response.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise ValueError('CF response too large')
        data = json.loads(content)
        if data.get('status') != 'OK':
            return dict(error='账号不存在' if 'not found' in str(data.get('comment', '')).lower() else 'CF 暂不可用')
        users = data.get('result')
        if not isinstance(users, list) or len(users) != 1 or not isinstance(users[0], dict) or not HANDLE.fullmatch(str(users[0].get('handle', ''))):
            raise ValueError('Invalid CF user response')
        user = users[0]
        rating = user.get('rating')
        if rating is not None and (type(rating) is not int or not 0 <= rating <= 10000):
            raise ValueError('Invalid CF rating')
        return dict(handle=user['handle'], rating=rating, rank=str(user.get('rank', ''))[:80], error='')

    def refresh(self, handles, now=None):
        """At most one lookup per call, at least three seconds between requests."""
        now = time.time() if now is None else now
        handles = list(dict.fromkeys(h for h in handles if HANDLE.fullmatch(h)))
        with self.lock:
            if self.busy or now - self.last_request < 3:
                return False
            due = [h for h in handles if now - self.users.get(h.casefold(), {}).get('last_attempt', 0)
                   >= (300 if self.users.get(h.casefold(), {}).get('error') == 'CF 暂不可用' else TTL)]
            if not due:
                return False
            handle = min(due, key=lambda h:self.users.get(h.casefold(), {}).get('last_attempt', 0))
            self.busy, self.last_request = True, now
        try:
            try:
                updated = self.lookup(handle)
            except (OSError, ValueError, KeyError, TypeError):
                updated = dict(error='CF 暂不可用')
            with self.lock:
                old = self.users.get(handle.casefold(), {})
                user = {**old, **updated, 'last_attempt':now}
                if not updated['error']:
                    user['updated_at'] = now
                self.users[handle.casefold()] = user
                temporary = self.path.with_suffix('.json.tmp')
                temporary.write_text(json.dumps(dict(version=1, users=self.users), ensure_ascii=False))
                os.chmod(temporary, 0o600)
                temporary.replace(self.path)
            return True
        finally:
            with self.lock:
                self.busy = False

    def worker(self, handles):
        while True:
            try:
                self.refresh(handles())
            except Exception:
                logging.getLogger('xcpcboard').exception('CF cache refresh failed')
            time.sleep(3)
