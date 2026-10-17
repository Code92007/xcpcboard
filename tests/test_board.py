import copy
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import server


START = 1800000000
URL = 'https://board.xcpcio.com/icpc/51st/xian?group=official'


def fixture():
    return dict(config=dict(contest_name='测试', start_time=START * 1000, end_time=(START + 18000) * 1000,
                            frozen_time=3600, penalty=1200, problems=[dict(id='0', label='A'), dict(id='1', label='B')],
                            options=dict(submission_timestamp_unit='millisecond')),
                teams=[dict(id='t1', name='一队', group=['official']), dict(id='t2', name='二队', group=['official']),
                       dict(id='star', name='打星', group=['unofficial'])], runs=[], organizations=[])


def run(rid, tid, pid, second, status):
    return dict(id=rid, team_id=tid, problem_id=pid, timestamp=second * 1000, status=status)


class ScoringTests(unittest.TestCase):
    def test_modern_and_legacy_schema_units_and_ignored_verdicts(self):
        raw = fixture()
        raw['runs'] = [run('1', 't1', '0', 1, 'COMPILATION_ERROR'), run('2', 't1', '0', 2, 'PRESENTATION_ERROR'),
                       run('3', 't1', '0', 3, 'WRONG_ANSWER'), run('4', 't1', '0', 119.999, 'ACCEPTED'),
                       run('5', 't1', '0', 130, 'ACCEPTED'), run('6', 't1', '1', 140, 'JUDGING')]
        station = dict(board_url=URL)
        b = server.build_board(raw, station, START + 200)
        t = next(t for t in b['teams'] if t['id'] == 't1')
        self.assertEqual((t['solved'], t['penalty'], t['problems'][0]['wrong']), (1, 21, 1))
        self.assertEqual(t['problems'][1]['state'], 'pending')
        legacy = copy.deepcopy(raw)
        legacy['config']['problem_id'] = ['A', 'B']
        del legacy['config']['problems']
        legacy['config']['options']['submission_timestamp_unit'] = 'second'
        for r in legacy['runs']:
            r['timestamp'] /= 1000
        self.assertEqual(server.build_board(legacy, station, START + 200)['teams'], b['teams'])

    def test_freeze_boundary_does_not_leak_in_any_payload_and_survives_end(self):
        raw = fixture()
        raw['runs'] = [run('1', 't1', '0', 14399.999, 'ACCEPTED'), run('2', 't1', '1', 14400, 'ACCEPTED'),
                       run('3', 't2', '0', 14500, 'WRONG_ANSWER')]
        for now in [START + 15000, START + 19000]:
            b = server.build_board(raw, dict(board_url=URL), now)
            self.assertTrue(b['frozen'])
            t = next(t for t in b['teams'] if t['id'] == 't1')
            self.assertEqual(t['solved'], 1)
            self.assertEqual(t['problems'][1]['state'], 'frozen')
            self.assertEqual([e['status'] for e in b['events']], ['AC', 'FROZEN', 'FROZEN'])
        self.assertEqual(next(t for t in server.build_board(raw, dict(board_url=URL, reveal=True), START+19000)['teams'] if t['id']=='t1')['solved'], 2)

    def test_ties_original_group_ranks_and_last_solve(self):
        raw = fixture()
        raw['runs'] = [run('1', 'star', '0', 5, 'AC'), run('2', 't1', '0', 65, 'AC'), run('3', 't2', '0', 100, 'AC')]
        b = server.build_board(raw, dict(board_url=URL), START+200)
        teams = {t['id']: t for t in b['teams']}
        self.assertEqual(teams['star']['ranks']['all'], 1)
        self.assertNotIn('official', teams['star']['ranks'])
        self.assertEqual(teams['t1']['ranks']['official'], 1)
        self.assertEqual(teams['t2']['ranks']['official'], 1)

    def test_seconds_penalty_and_out_of_contest_runs(self):
        raw=fixture()
        raw['config']['options']['calculation_of_penalty']='in_seconds'
        raw['runs']=[run('1','t1','0',90,'AC'),run('2','t1','1',18000,'AC'),run('3','t1','1',-1,'AC')]
        b=server.build_board(raw,dict(board_url=URL,reveal=True),START+19000)
        self.assertEqual(b['teams'][0]['penalty'],1.5)
        self.assertEqual(len(b['events']),1)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=server.Store(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_public_notes_groups_and_source_isolation_across_restart(self):
        s=self.store.stations()[0]
        self.store.update('xian',dict(source=s['source'],notes={'Team001':'群友'},public_groups=[dict(id='friends',name='群友',teams=['Team001'])],default_group='friends'))
        self.store.update('xian',dict(board_url=URL))
        self.assertEqual(self.store.stations()[0]['notes'],{})
        self.store.update('xian',dict(board_url=s['board_url']))
        self.assertEqual(server.Store(self.temp.name).stations()[0]['notes'],{'Team001':'群友'})
        with self.assertRaises(ValueError):
            self.store.update('xian',dict(source='stale',notes={}))

    def test_known_good_snapshot_survives_outage_empty_upstream_and_restart(self):
        s=self.store.station('xian')
        raw=fixture()
        raw['config']['start_time']=1000
        raw['config']['end_time']=20000000
        raw['runs']=[run('1','t1','0',1,'AC')]
        def fetch(url, ttl=0):
            return raw[{'config.json':'config','team.json':'teams','run.json':'runs'}[url.rsplit('/',1)[-1]]]
        with patch.object(self.store.fetcher,'get',side_effect=fetch):
            self.store.refresh(s)
        key=server.source_key(server.board_source(s['board_url'])[0])
        original=copy.deepcopy(self.store.raw[key])
        with patch.object(self.store.fetcher,'get',side_effect=OSError('outage')):
            self.store.refresh(s)
        self.assertEqual(self.store.raw[key],original)
        self.assertTrue(self.store.board('xian')['health']['stale'])
        raw['runs']=[]
        with patch.object(self.store.fetcher,'get',side_effect=fetch):
            self.store.refresh(s)
        self.assertEqual(self.store.raw[key],original)
        restarted=server.Store(self.temp.name)
        self.assertEqual(restarted.raw[key],original)
        self.assertTrue(restarted.board('xian')['board']['frozen'])

    def test_cache_corruption_falls_back_to_previous(self):
        s=self.store.station('xian')
        path=server.board_source(s['board_url'])[0];key=server.source_key(path)
        raw={**fixture(), 'path':path,'fetched_at':1}
        server.atomic_json(Path(self.temp.name)/(key+'.previous.json.gz'),raw,True)
        (Path(self.temp.name)/(key+'.json.gz')).write_bytes(b'broken')
        self.assertEqual(server.Store(self.temp.name).raw[key],raw)

    def test_url_and_config_validation_does_not_mutate_config(self):
        before=copy.deepcopy(self.store.config)
        for url in ['http://127.0.0.1/x/y','https://board.xcpcio.com.evil.test/a/b','https://board.xcpcio.com/a/../b','https://board.xcpcio.com/a/%2e%2e/b','https://board.xcpcio.com:443/a/b']:
            with self.assertRaises(ValueError):
                self.store.update('xian',dict(board_url=url))
        with self.assertRaises(ValueError):
            self.store.update('xian',dict(freeze_after=float('nan')))
        self.assertEqual(self.store.config,before)
        self.assertEqual(server.board_source(URL)[0],'icpc/51st/xian')


if __name__=='__main__':
    unittest.main()
