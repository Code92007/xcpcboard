from pathlib import Path
import tempfile
import unittest
import server


PATH = 'icpc/51st/xian'
WARMUP = PATH + '-warmup'
URL = 'https://board.xcpcio.com/' + PATH


class PreferenceTests(unittest.TestCase):
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
            public = restored.stations()[0]
            self.assertEqual(public['notes'], {'a':'群友甲','b':'新备注'})
            self.assertEqual(public['public_groups'], [dict(id='new-friends',name='群友',teams=['a','b'])])
            self.assertEqual(public['default_group'], 'new-friends')
            self.assertEqual(public['preference_sources'], [dict(source=old_key,team_map={'a':'a','b':'b'},public_group_map={'old-friends':'new-friends'})])
            self.assertEqual(restored.station('xian')['profiles'][old_key], original)
            restored.update('xian', dict(source=key,notes={},public_groups=[],default_group='all'))
            again = server.Store(directory).stations()[0]
            self.assertEqual((again['notes'],again['public_groups'],again['default_group']), ({},[],'all'))

    def test_inheritance_waits_for_formal_snapshot_then_handles_new_team_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            store = server.Store(directory)
            old_key, key = server.source_key(WARMUP), server.source_key(PATH)
            store.raw[old_key] = dict(teams=[dict(id='old',name='一队',organization='大学')])
            store.update('xian',dict(source=old_key,notes={'old':'群友'},public_groups=[dict(id='friends',name='群友',teams=['old'])],default_group='friends'))
            store.update('xian',dict(board_url=URL))
            self.assertEqual(store.stations()[0]['notes'], {})
            store.raw[key] = dict(teams=[dict(id='new',name='一队',organization='大学')])
            store.inherit_preferences(PATH)
            public = store.stations()[0]
            self.assertEqual(public['notes'], {'new':'群友'})
            self.assertEqual(public['public_groups'][0]['teams'], ['new'])
            self.assertEqual(public['default_group'], 'friends')


if __name__ == '__main__':
    unittest.main()
