import json
from io import BytesIO
from pathlib import Path
import tempfile
import threading
from datetime import datetime
from http.client import HTTPConnection
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from codeforces import Ratings, clean_bindings
import server


class CodeforcesTests(unittest.TestCase):
    def test_warmup_binding_inheritance_checks_formal_team_members(self):
        target = {}
        old = dict(cf_bindings={'old':[dict(member='甲', handle='first'), dict(member='乙', handle='second')]})
        server.merge_public_profile(target, old, {'old':'new'}, {'new':['甲','丙']})
        self.assertEqual(target['cf_bindings'], {'new':[dict(member='甲', handle='first')]})

    def test_member_validation_limits_duplicates_and_nonexistent_teams(self):
        teams = [dict(id='t', members=['甲', '乙', '丙'])]
        valid = {'t':[dict(member='甲', handle='Some_User')]}
        self.assertEqual(clean_bindings(valid, teams), valid)
        self.assertEqual(clean_bindings({'t':[]}, teams), {})
        invalid = [
            {'missing':valid['t']}, {'t':valid['t'] * 4},
            {'t':[dict(member='甲', handle='bad/account')]},
            {'t':[dict(member='丁', handle='valid')]},
            {'t':[dict(member='甲', handle='valid'), dict(member='乙', handle='VALID')]},
            {'t':[dict(member='甲', handle='first'), dict(member='甲', handle='second')]},
            {'t':[dict(member='', handle='valid')]},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                clean_bindings(value, teams)

    def test_cache_persists_current_rating_and_keeps_it_during_outages(self):
        with tempfile.TemporaryDirectory() as directory:
            ratings = Ratings(directory)
            with patch.object(ratings, 'lookup_many', return_value={'some_user':dict(handle='Some_User', rating=2105, rank='master', error='')}) as lookup:
                self.assertTrue(ratings.refresh(['Some_User'], now=10000))
                self.assertFalse(ratings.refresh(['Some_User', 'other'], now=10001))
                self.assertFalse(ratings.refresh(['Some_User'], now=10010))
                lookup.assert_called_once_with(['Some_User'])
            restored = Ratings(directory)
            self.assertEqual(restored.snapshot(['some_user'], now=10010)['some_user']['rating'], 2105)
            restored.request_sync(['Some_User'], now=14000)
            with patch.object(restored, 'lookup_many', side_effect=OSError('offline')):
                self.assertTrue(restored.refresh(['Some_User'], now=14000))
                cached = restored.snapshot(['SOME_USER'], now=14001)['some_user']
                self.assertEqual((cached['rating'], cached['updated_at']), (2105, 10000))
                self.assertTrue(cached['stale'])
                self.assertEqual(cached['error'], 'CF 暂不可用')
                self.assertFalse(restored.refresh(['Some_User'], now=14010))

    def test_invalid_handle_does_not_prevent_other_users_refreshing(self):
        with tempfile.TemporaryDirectory() as directory:
            ratings = Ratings(directory)
            with patch.object(ratings, 'lookup_many', side_effect=[{'missing':dict(error='账号不存在')}, {'valid':dict(handle='valid', rating=None, rank='', error='')}]):
                ratings.refresh(['missing', 'valid'], now=10000)
                ratings.refresh(['missing', 'valid'], now=10003)
            users = ratings.snapshot(['missing', 'valid'], now=10004)
            self.assertEqual(users['missing']['error'], '账号不存在')
            self.assertIsNone(users['valid']['rating'])
            self.assertFalse(users['valid']['stale'])

    def test_binding_save_is_source_scoped_atomic_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = server.Store(directory)
            path = server.board_source(server.NANJING_STATION['board_url'])[0]
            key = server.source_key(path)
            raw = dict(path=path, fetched_at=1, config=dict(contest_name='测试', start_time=1800000000000,
                       end_time=1800018000000, problems=[]), teams=[dict(id='t', name='队伍', members=['甲','乙'])], organizations=[], runs=[])
            server.atomic_json(Path(directory)/(key+'.json.gz'), raw, True)
            store.load(path)
            store.update('nanjing', dict(source=key, cf_binding=dict(team='t', members=[dict(member='甲', handle='Some_User')]), notes={'t':'群友'}))
            store.update('nanjing', dict(source=key, cf_binding=dict(team='t', members=[dict(member='乙', handle='another')])) )
            before = store.config_path.read_bytes()
            for source, binding in [('wrong', dict(team='t', members=[])), (key, dict(team='t', members=[dict(member='外人', handle='valid')]))]:
                with self.assertRaises(ValueError):
                    store.update('nanjing', dict(source=source, cf_binding=binding))
                self.assertEqual(store.config_path.read_bytes(), before)
            restored = server.Store(directory).public_station(store.station('nanjing'))
            self.assertEqual(restored['cf_bindings'], {'t':[dict(member='乙', handle='another')]})
            self.assertEqual(restored['notes'], {'t':'群友'})
            self.assertEqual(store.cf_handles(), ['another'])
            self.assertTrue(restored['cf_users']['another']['syncing'])
            self.assertTrue(store.ratings.wakeup.is_set())

    def test_lookup_uses_current_rating_and_historic_handle_response(self):
        with tempfile.TemporaryDirectory() as directory:
            ratings = Ratings(directory)
            response = unittest.mock.MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(dict(status='OK', result=[dict(handle='New_Name', rating=1500, maxRating=2900)])).encode()
            with patch.object(ratings.opener, 'open', return_value=response) as request:
                self.assertEqual(ratings.lookup('Old_Name')['rating'], 1500)
                self.assertEqual(ratings.lookup('Old_Name')['handle'], 'New_Name')
                self.assertIn('https://codeforces.com/api/user.info?', request.call_args.args[0].full_url)

    def test_binding_forces_fresh_cached_account_but_visitors_share_work(self):
        with tempfile.TemporaryDirectory() as directory:
            ratings = Ratings(directory)
            update = {'first':dict(handle='first', rating=2500, rank='grandmaster', error='')}
            with patch.object(ratings, 'lookup_many', return_value=update) as lookup:
                ratings.refresh(['first'], now=10000)
                self.assertEqual(ratings.request_sync(['FIRST'], reason='visit', now=10005), 0)
                self.assertEqual(ratings.request_sync(['first'], now=10005), 1)
                self.assertEqual(ratings.request_sync(['FIRST'], reason='visit', now=10005), 0)
                ratings.refresh(['first'], now=10005)
                self.assertEqual(lookup.call_count, 2)
                self.assertEqual(ratings.request_sync(['first'], reason='visit', now=10070), 1)
                self.assertEqual(ratings.request_sync(['FIRST'], reason='visit', now=10070), 0)

    def test_one_batch_fetches_three_accounts_and_snapshot_does_not_block(self):
        with tempfile.TemporaryDirectory() as directory:
            ratings = Ratings(directory)
            ratings.request_sync(['first','second','third','FIRST'], now=10000)
            def lookup(handles):
                # Public reads remain available while a CF request is running.
                observed = []
                reader = threading.Thread(target=lambda:observed.append(ratings.snapshot(handles, now=10000)), daemon=True)
                reader.start();reader.join(1)
                self.assertFalse(reader.is_alive(), 'Snapshot blocked on CF network')
                self.assertTrue(all(u['syncing'] for u in observed[0].values()))
                return {h.casefold():dict(handle=h, rating=2000, rank='', error='') for h in handles}
            with patch.object(ratings, 'lookup_many', side_effect=lookup) as fetch:
                ratings.refresh(['first','second','third'], now=10000)
                self.assertEqual(len(fetch.call_args.args[0]), 3)
                self.assertEqual(fetch.call_count, 1)
            self.assertEqual(ratings.pending, {})
            self.assertTrue(all(not u['syncing'] and u['rating']==2000 for u in ratings.snapshot(['first','second','third'], now=10000).values()))

    def test_daily_trigger_uses_shanghai_midnight_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            ratings = Ratings(directory)
            before = datetime.fromisoformat('2026-11-07T15:59:59+00:00').timestamp()
            ratings.schedule(['first'], [], now=before)
            self.assertEqual(ratings.daily_date, '2026-11-07')
            with patch.object(ratings, 'lookup_many', return_value={'first':dict(handle='first', rating=2200, rank='', error='')}):
                ratings.refresh(['first'], now=before)
                ratings.schedule(['first'], [], now=before+1)
                self.assertEqual(ratings.daily_date, '2026-11-08')
                self.assertIn('first', ratings.pending)
                self.assertFalse(ratings.refresh(['first'], now=before+1))
                ratings.refresh(['first'], now=before+3)
            restored = Ratings(directory)
            restored.schedule(['first'], [], now=before+100)
            self.assertEqual(restored.pending, {})

    def test_kickoff_forces_only_that_contest_once_even_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            ratings = Ratings(directory)
            start = datetime.fromisoformat('2026-11-08T09:30:00+08:00').timestamp()
            contest = dict(source='formal-source', start_time=start, end_time=start+18000, handles=['first'])
            old = dict(source='old-source', start_time=start-90000, end_time=start-72000, handles=['second'])
            ratings.schedule(['first','second'], [contest,old], now=start-10)
            with patch.object(ratings, 'lookup_many', return_value={h:dict(handle=h, rating=2200, rank='', error='') for h in ['first','second']}):
                ratings.refresh(['first','second'], now=start-10)
            ratings.schedule(['first','second'], [contest,old], now=start-1)
            self.assertEqual(ratings.pending, {})
            ratings.schedule(['first','second'], [contest,old], now=start)
            self.assertEqual(list(ratings.pending), ['first'])
            with patch.object(ratings, 'lookup_many', return_value={'first':dict(handle='first', rating=2201, rank='', error='')}):
                ratings.refresh(['first','second'], now=start)
            restored = Ratings(directory)
            restored.schedule(['first','second'], [contest,old], now=start+10)
            self.assertEqual(restored.pending, {})
            self.assertEqual(len(restored.contest_starts), 1)

    def test_failed_batch_isolates_unknown_handle_and_keeps_others_queued(self):
        with tempfile.TemporaryDirectory() as directory:
            ratings = Ratings(directory)
            body = json.dumps(dict(status='FAILED', comment='handles: User with handle Missing not found')).encode()
            failure = HTTPError('https://codeforces.com/api/user.info', 400, 'Bad Request', {}, BytesIO(body))
            with patch.object(ratings.opener, 'open', side_effect=failure):
                ratings.refresh(['valid','Missing'], now=10000)
            self.assertEqual(ratings.users['missing']['error'], '账号不存在')
            self.assertEqual(list(ratings.pending), ['valid'])
            self.assertEqual(list(Ratings(directory).pending), ['valid'])

    def test_outage_backoff_and_pending_work_persist_across_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            ratings = Ratings(directory)
            with patch.object(ratings, 'lookup_many', side_effect=OSError('offline')):
                ratings.refresh(['first'], now=10000)
            restored = Ratings(directory)
            self.assertEqual(restored.pending['first']['ready_at'], 10300)
            self.assertFalse(restored.snapshot(['first'], now=10010)['first']['syncing'])
            self.assertEqual(restored.request_sync(['first'], reason='visit', now=10010), 0)
            with patch.object(restored, 'lookup_many', return_value={'first':dict(handle='first', rating=2400, rank='', error='')}) as fetch:
                self.assertFalse(restored.refresh(['first'], now=10299))
                self.assertTrue(restored.refresh(['first'], now=10300))
                self.assertEqual(fetch.call_count, 1)

    def test_new_binding_accelerates_retry_and_restart_keeps_api_spacing(self):
        with tempfile.TemporaryDirectory() as directory:
            ratings = Ratings(directory)
            with patch.object(ratings, 'lookup_many', side_effect=OSError('offline')):
                ratings.refresh(['first'], now=10000)
            restored = Ratings(directory)
            self.assertEqual(restored.request_sync(['first'], now=10001), 1)
            with patch.object(restored, 'lookup_many', return_value={'first':dict(handle='first', rating=2400, rank='', error='')}) as fetch:
                self.assertFalse(restored.refresh(['first'], now=10001))
                self.assertTrue(restored.refresh(['first'], now=10003))
                self.assertEqual(fetch.call_count, 1)

    def test_public_visit_endpoint_cannot_choose_accounts_or_cross_origins(self):
        with tempfile.TemporaryDirectory() as directory:
            store = server.Store(directory)
            httpd = server.BoardHTTPServer(('127.0.0.1', 0), server.Handler)
            httpd.store = store
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            self.addCleanup(httpd.server_close);self.addCleanup(httpd.shutdown)
            def post(body='{}', origin=None):
                connection = HTTPConnection(*httpd.server_address, timeout=5)
                headers = {'Content-Type':'application/json'}
                if origin:headers['Origin']=origin
                connection.request('POST', '/api/codeforces/visit', body=body, headers=headers)
                response = connection.getresponse();result = response.status, json.loads(response.read());connection.close()
                return result
            with patch.object(store, 'cf_handles', return_value=['bound_account']), patch.object(store.ratings, 'request_sync', return_value=1) as enqueue:
                self.assertEqual(post(), (202, dict(ok=True, queued=1)))
                enqueue.assert_called_once_with(['bound_account'], reason='visit')
                self.assertEqual(post(origin='https://another.site')[0], 403)
                self.assertEqual(post('{"handles":["arbitrary"]}')[0], 400)
                self.assertEqual(enqueue.call_count, 1)
