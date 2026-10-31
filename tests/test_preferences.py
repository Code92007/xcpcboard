from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import server


PATH = 'icpc/51st/xian'
WARMUP = PATH + '-warmup'
URL = 'https://board.xcpcio.com/' + PATH


class PreferenceTests(unittest.TestCase):
    def test_girls_uuid_switch_verifies_title_year_school_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = server.Store(directory)
            warmup = server.board_source(server.GIRLS_STATION['warmup_board_url'])[0]
            store.update('girls', dict(board_url=server.GIRLS_STATION['warmup_board_url']))
            path = 'iccpc:11111111-1111-4111-8111-111111111111'
            old_key, key = server.source_key(warmup), server.source_key(path)
            title = '第十二届中国大学生程序设计竞赛（成都女生专场）'
            old = dict(provider='iccpc', path=warmup, fetched_at=1,
                       config=dict(title=title+'-热身赛', start_time='2026-10-31 15:00:00'),
                       teams=[dict(team_id='#cpc1002_A01', name='一队', school='甲校'),
                              dict(team_id='B02', name='二队', school='乙校')])
            new = dict(provider='iccpc', path=path, fetched_at=2,
                       config=dict(title=title+' - 正式赛', start_time='2026-11-01 09:00:00'),
                       teams=[dict(team_id='#cpc1003_C04', name='一队', school='甲校'),
                              dict(team_id='B02', name='二队', school='丙校')])
            server.atomic_json(Path(directory)/(old_key+'.json.gz'), old, True)
            store.load(warmup)
            store.update('girls', dict(source=old_key, notes={'A01':'群友', 'B02':'其他校'},
                                      public_groups=[dict(id='friends', name='群友', teams=['A01','B02'])], default_group='friends'))
            store.update('girls', dict(board_url='https://rank.iccpc.cn/r/'+path.split(':')[1]))
            before = store.station('girls')['profiles'][old_key]
            with patch.object(store.fetcher, 'get', side_effect=HTTPError('https://rank.iccpc.cn', 404, 'Not Found', {}, None)):
                store.refresh(store.station('girls'))
            self.assertEqual(store.station('girls')['profiles'][old_key], before)
            self.assertEqual(store.board('girls')['station']['preference_sources'], [])
            self.assertIsNone(store.board('girls')['board'])
            server.atomic_json(Path(directory)/(key+'.json.gz'), new, True)
            restored = server.Store(directory)
            public = restored.public_station(restored.station('girls'))
            self.assertEqual(public['notes'], {'C04':'群友'})
            self.assertEqual(public['public_groups'][0]['teams'], ['C04'])
            self.assertEqual(public['preference_sources'][0]['team_map'], {'A01':'C04'})
            self.assertEqual(public['default_group'], 'friends')
            self.assertEqual(restored.station('girls')['board_url'], 'https://rank.iccpc.cn/r/'+path.split(':')[1])
            restored.update('girls', dict(source=key, notes={}, public_groups=[], default_group='all'))
            again = server.Store(directory).public_station(restored.station('girls'))
            self.assertEqual((again['notes'], again['public_groups']), ({}, []))
            new['config']['start_time'] = '2027-11-01 09:00:00'
            self.assertFalse(server.same_iccpc_contest(old, new))
            new['config'].update(start_time='2026-11-01 09:00:00', title='另一场女生赛')
            self.assertFalse(server.same_iccpc_contest(old, new))

    def test_team_identity_checks_school_reused_ids_and_ambiguous_names(self):
        old = dict(teams=[dict(id='a', name='一队', organization='甲校'), dict(id='b', name='二队', organization='乙校'),
                          dict(id='c', name='同名', organization='甲校'), dict(id='d', name='同名', organization='甲校')])
        new = dict(teams=[dict(id='a', name='另一队', organization='甲校'), dict(id='new-b', name='二队', organization='乙校'),
                          dict(id='new-c', name='同名', organization='甲校'), dict(id='new-d', name='同名', organization='甲校'),
                          dict(id='wrong-school', name='一队', organization='丙校')])
        self.assertEqual(server.matching_teams(old, new), {'b':'new-b'})
        self.assertEqual(server.warmup_path('iccpc:any-id'), '')
        self.assertEqual(server.warmup_path(WARMUP), '')

    def test_existing_formal_group_is_restored_and_user_edits_stay_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = server.Store(directory)
            old_key, key = server.source_key(WARMUP), server.source_key(PATH)
            original = dict(notes={'a':'群友甲', 'b':'旧备注'}, public_groups=[dict(id='old-friends', name='群友', teams=['a','b'])], default_group='old-friends')
            store.update('xian', dict(source=old_key, **original))
            store.update('xian', dict(board_url=URL))
            store.update('xian', dict(source=key, notes={'b':'新备注'}, public_groups=[dict(id='new-friends', name='群友', teams=[])], default_group='new-friends'))
            # A different contest in the same station must remain independent.
            station = store.station('xian')
            station['profiles'][server.source_key('icpc/50th/xian-warmup')] = dict(notes={'a':'别的年份'})
            store.config['contests'][0] = station
            server.atomic_json(Path(directory) / 'config.json', store.config)
            for path in [WARMUP, PATH]:
                raw = dict(path=path, fetched_at=1, teams=[dict(id='a',name='一队'),dict(id='b',name='二队')])
                server.atomic_json(Path(directory) / (server.source_key(path)+'.json.gz'), raw, True)
            restored = server.Store(directory)
            public = restored.public_station(restored.station('xian'))
            self.assertEqual(public['notes'], {'a':'群友甲','b':'新备注'})
            self.assertEqual(public['public_groups'], [dict(id='new-friends',name='群友',teams=['a','b'])])
            self.assertEqual(public['default_group'], 'new-friends')
            self.assertEqual(public['preference_sources'], [dict(source=old_key,team_map={'a':'a','b':'b'},public_group_map={'old-friends':'new-friends'})])
            self.assertEqual(restored.station('xian')['profiles'][old_key], original)
            restored.update('xian', dict(source=key,notes={},public_groups=[],default_group='all'))
            again_store = server.Store(directory)
            again = again_store.public_station(again_store.station('xian'))
            self.assertEqual((again['notes'],again['public_groups'],again['default_group']), ({},[],'all'))

    def test_inheritance_waits_for_formal_snapshot_then_handles_new_team_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            store = server.Store(directory)
            old_key, key = server.source_key(WARMUP), server.source_key(PATH)
            store.raw[old_key] = dict(teams=[dict(id='old',name='一队',organization='大学')])
            store.update('xian',dict(source=old_key,notes={'old':'群友'},public_groups=[dict(id='friends',name='群友',teams=['old'])],default_group='friends'))
            store.update('xian',dict(board_url=URL))
            self.assertEqual(store.public_station(store.station('xian'))['notes'], {})
            store.raw[key] = dict(teams=[dict(id='new',name='一队',organization='大学')])
            store.inherit_preferences(PATH)
            public = store.public_station(store.station('xian'))
            self.assertEqual(public['notes'], {'new':'群友'})
            self.assertEqual(public['public_groups'][0]['teams'], ['new'])
            self.assertEqual(public['default_group'], 'friends')

    def test_prestart_refresh_restores_warmup_groups_before_problems_are_published(self):
        with tempfile.TemporaryDirectory() as directory:
            store = server.Store(directory)
            path = 'icpc/51st/chengdu'
            warmup = path + '-warmup'
            old_key = server.source_key(warmup)
            store.update('chengdu', dict(board_url='https://board.xcpcio.com/' + warmup))
            store.update('chengdu', dict(source=old_key, public_groups=[
                dict(id='friends', name='群友', teams=['F32'])], default_group='friends'))
            store.raw[old_key] = dict(teams=[dict(id='F32', name='同队', organization='甲校')])
            store.update('chengdu', dict(board_url='https://board.xcpcio.com/' + path))
            upstream = {'config.json': dict(contest_name='成都', start_time=1800000000000,
                                            end_time=1800018000000, organizations=[], group={'official': '正式队伍'}),
                        'team.json': [dict(id='F32', name='同队', organization='甲校', group=['official'])],
                        'run.json': []}
            with patch.object(store.fetcher, 'get', side_effect=lambda url, ttl=0: upstream[url.rsplit('/', 1)[-1]]):
                store.refresh(store.station('chengdu'))
            board = store.board('chengdu')
            self.assertEqual((len(board['board']['teams']), board['board']['problems']), (1, []))
            self.assertEqual(board['station']['public_groups'][0]['teams'], ['F32'])
            self.assertEqual(board['station']['default_group'], 'friends')
            self.assertEqual(board['station']['preference_sources'][0]['team_map'], {'F32': 'F32'})

    def test_wuhan_switch_retains_warmup_snapshot_and_remaps_follows_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = server.Store(directory)
            path = 'icpc/51st/wuhan'
            warmup = path + '-warmup'
            old_key, key = server.source_key(warmup), server.source_key(path)
            raw = dict(path=warmup, fetched_at=1, config={},
                       teams=[dict(id='G31', name='群友队', organization='甲校')])
            server.atomic_json(Path(directory) / (old_key + '.json.gz'), raw, True)
            store.load(warmup)
            store.update('wuhan', dict(board_url='https://board.xcpcio.com/' + warmup))
            store.update('wuhan', dict(source=old_key, notes={'G31': '群友'}, public_groups=[
                dict(id='friends', name='群友', teams=['G31'])], default_group='friends'))
            store.update('wuhan', dict(board_url='https://board.xcpcio.com/' + path))
            upstream = {'config.json': dict(contest_name='武汉', start_time=1800000000000,
                                            end_time=1800018000000, group={'official': '正式队伍'}),
                        'team.json': [dict(id='G31', name='另一队', organization='乙校', group=['official']),
                                      dict(id='new-G31', name='群友队', organization='甲校', group=['official'])],
                        'run.json': []}
            with patch.object(store.fetcher, 'get', side_effect=lambda url, ttl=0: upstream[url.rsplit('/', 1)[-1]]):
                store.refresh(store.station('wuhan'))
            restored = server.Store(directory)
            station = restored.board('wuhan')['station']
            self.assertEqual(station['notes'], {'new-G31': '群友'})
            self.assertEqual(station['public_groups'][0]['teams'], ['new-G31'])
            self.assertEqual(station['default_group'], 'friends')
            self.assertEqual(station['preference_sources'][0]['team_map'], {'G31': 'new-G31'})
            self.assertEqual(restored.raw[old_key], raw)
            restored.update('wuhan', dict(source=key, notes={}, public_groups=[], default_group='all'))
            again = server.Store(directory).board('wuhan')['station']
            self.assertEqual((again['notes'], again['public_groups']), ({}, []))


if __name__ == '__main__':
    unittest.main()
