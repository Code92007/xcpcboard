import copy
from pathlib import Path
import tempfile
import unittest

import server
from public_import import match_teams, plan_import


def team(tid, name='队伍', org='甲校', members=None):
    return dict(id=tid, name=name, organization=org, members=members or [])


class PublicImportTests(unittest.TestCase):
    def test_changed_roster_same_name_and_ambiguous_teams_do_not_match(self):
        before = [team('old', members=['甲','乙','丙']), team('single', '单人', members=['同名']),
                  team('dup1', '重名'), team('dup2', '重名')]
        after = [team('new', members=['甲','乙','丁']), team('other', '另一队', members=['同名','另一人']), team('dup-new', '重名')]
        matches, ambiguous = match_teams(before, after)
        self.assertEqual(matches, {})
        self.assertEqual(ambiguous, {'dup1','dup2'})

    def test_full_roster_can_match_renamed_team_with_different_organization(self):
        before = [team('old', members=['甲','乙','丙'])]
        after = [team('new', '新队名', '打星参赛', ['丙','乙','甲'])]
        self.assertEqual(match_teams(before, after)[0]['old']['id'], 'new')
        self.assertEqual(match_teams(before, after + [team('duplicate', '又一队', members=['甲','乙','丙'])])[0], {})

    def test_merge_keeps_notes_groups_and_existing_member_bindings(self):
        target = dict(station=dict(notes={'new':'现有备注'}, public_groups=[dict(id='keep', name='群友', teams=['existing'])],
                                  cf_bindings={'new':[dict(member='甲', handle='current')]}),
                      board=dict(teams=[team('new', members=['甲','乙'])]))
        source = dict(station=dict(title='往期', notes={'old':'旧备注'}, public_groups=[dict(id='old-group', name='群友', teams=['old']),
                                 dict(id='cyber', name='赛博大战', teams=['old'])],
                                 cf_bindings={'old':[dict(member='甲', handle='previous'), dict(member='乙', handle='second'), dict(member='甲', handle='previous_alt')]}),
                      board=dict(teams=[team('old', members=['甲','乙'])]))
        before = copy.deepcopy(target)
        plan = plan_import(target, [source], ['群友','赛博大战'])
        self.assertEqual(target, before)
        self.assertEqual(plan['notes'], {'new':'现有备注'})
        self.assertEqual(plan['public_groups'][0], dict(id='keep', name='群友', teams=['existing','new']))
        self.assertEqual(plan['public_groups'][1]['teams'], ['new'])
        self.assertEqual(plan['cf_bindings']['new'], [dict(member='甲', handle='current'), dict(member='甲', handle='previous'), dict(member='乙', handle='second')])
        self.assertEqual(plan['report']['bindings_added'], 2)
        self.assertEqual(len(plan['report']['conflicts']), 1)

    def test_preview_is_readonly_apply_merges_and_rejects_stale_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            store = server.Store(directory)
            for sid, default, tid in [('wuhan', server.WUHAN_STATION, 'old'), ('nanjing', server.NANJING_STATION, 'new')]:
                path = server.board_source(default['board_url'])[0]
                key = server.source_key(path)
                raw = dict(path=path, fetched_at=1, config=dict(contest_name='测试', start_time=1800000000000,
                           end_time=1800018000000, problems=[]), teams=[dict(id=tid, name='同队', organization='甲校', members=['甲','乙'])], organizations=[], runs=[])
                server.atomic_json(Path(directory)/(key+'.json.gz'), raw, True)
                store.load(path)
                if sid == 'wuhan':
                    store.update(sid, dict(source=key, notes={tid:'旧群友'}, public_groups=[dict(id='friends', name='群友', teams=[tid])]))
            source = store.public_station(store.station('wuhan'))
            target = store.public_station(store.station('nanjing'))
            request = dict(source=target['source'], sources=[dict(id='wuhan', source=source['source'])], groups=['群友'])
            before = store.config_path.read_bytes()
            plan = store.import_public('nanjing', request)
            self.assertEqual(store.config_path.read_bytes(), before)
            self.assertEqual(plan['plan']['notes'], {'new':'旧群友'})
            result = store.import_public('nanjing', {**request, 'apply':True})
            self.assertEqual(result['station']['notes'], {'new':'旧群友'})
            self.assertEqual(server.Store(directory).public_station(store.station('nanjing'))['public_groups'][0]['teams'], ['new'])
            for bad in [{**request, 'source':'stale'}, {**request, 'sources':[dict(id='wuhan', source='stale')]}]:
                with self.assertRaises(ValueError):
                    store.import_public('nanjing', bad)
