import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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
            with patch.object(ratings, 'lookup', return_value=dict(handle='Some_User', rating=2105, rank='master', error='')) as lookup:
                self.assertTrue(ratings.refresh(['Some_User'], now=10000))
                self.assertFalse(ratings.refresh(['Some_User', 'other'], now=10001))
                self.assertFalse(ratings.refresh(['Some_User'], now=10010))
                lookup.assert_called_once_with('Some_User')
            restored = Ratings(directory)
            self.assertEqual(restored.snapshot(['some_user'], now=10010)['some_user']['rating'], 2105)
            with patch.object(restored, 'lookup', side_effect=OSError('offline')):
                self.assertTrue(restored.refresh(['Some_User'], now=14000))
                cached = restored.snapshot(['SOME_USER'], now=14001)['some_user']
                self.assertEqual((cached['rating'], cached['updated_at']), (2105, 10000))
                self.assertTrue(cached['stale'])
                self.assertEqual(cached['error'], 'CF 暂不可用')
                self.assertFalse(restored.refresh(['Some_User'], now=14010))

    def test_invalid_handle_does_not_prevent_other_users_refreshing(self):
        with tempfile.TemporaryDirectory() as directory:
            ratings = Ratings(directory)
            with patch.object(ratings, 'lookup', side_effect=[dict(error='账号不存在'), dict(handle='valid', rating=None, rank='', error='')]):
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

    def test_lookup_uses_current_rating_and_historic_handle_response(self):
        with tempfile.TemporaryDirectory() as directory:
            ratings = Ratings(directory)
            response = unittest.mock.MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(dict(status='OK', result=[dict(handle='New_Name', rating=1500, maxRating=2900)])).encode()
            with patch.object(ratings.opener, 'open', return_value=response) as request:
                self.assertEqual(ratings.lookup('Old_Name')['rating'], 1500)
                self.assertEqual(ratings.lookup('Old_Name')['handle'], 'New_Name')
                self.assertIn('https://codeforces.com/api/user.info?', request.call_args.args[0].full_url)
