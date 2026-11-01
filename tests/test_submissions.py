import copy
from http.client import HTTPConnection
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

import server
from test_board import START, URL, fixture, run
from test_iccpc_media import PATH as ICCPC_PATH, START as ICCPC_START, fixture as iccpc_fixture, run as iccpc_run


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = server.Store(self.temp.name)
        self.store.update('xian', dict(board_url=URL))
        self.key = server.source_key(server.board_source(URL)[0])

    def history(self, team='t1', pid='0', now=START+15000):
        with patch.object(server.time, 'time', return_value=now):
            return self.store.submissions('xian', self.key, team, server.submission_key(team, pid))

    def test_full_history_survives_feed_limit_and_uses_same_filtering(self):
        raw = fixture()
        raw['runs'] = [run('early', 't1', '0', 1, 'WRONG_ANSWER')]
        raw['runs'] += [run(str(i), 't2', '1', i+2, 'WA') for i in range(900)]
        raw['runs'] += [run('late', 't1', '0', 14500.123, 'ACCEPTED'),
                        run('late', 't1', '0', 14500.123, 'ACCEPTED'),
                        run('future', 't1', '0', 16000, 'AC'),
                        run('negative', 't1', '0', -1, 'AC'),
                        run('after', 't1', '0', 18000, 'AC')]
        raw['runs'][0]['language'] = 'C++'
        self.store.raw[self.key] = raw
        before = copy.deepcopy(raw)
        b = server.build_board(raw, dict(board_url=URL), START+15000)
        self.assertEqual(len(b['events']), 800)
        self.assertNotIn('early', [e['id'] for e in b['events']])
        h = self.history()
        self.assertEqual([(e['id'], e['status']) for e in h['submissions']], [('late', 'FROZEN'), ('early', 'WA')])
        self.assertEqual(h['submissions'][1]['language'], 'C++')
        self.assertEqual(h['total'], 2)
        self.assertEqual(h['problem']['label'], 'A')
        self.assertEqual(next(t for t in b['teams'] if t['id']=='t1')['problems'][0]['last_submit_second'], 14500.123)
        self.assertEqual(raw, before)
        self.assertEqual(self.history(now=START+19000)['submissions'][0]['status'], 'FROZEN')
        self.store.update('xian', dict(reveal=True))
        self.assertEqual(self.history()['submissions'][0]['status'], 'AC')

    def test_last_submit_includes_non_penalized_and_post_acceptance_runs(self):
        raw = fixture()
        raw['runs'] = [run('1', 't1', '0', 60, 'AC'), run('2', 't1', '0', 100, 'CE'),
                       run('3', 't1', '1', 120, 'JUDGING')]
        self.store.raw[self.key] = raw
        b = server.build_board(raw, dict(board_url=URL), START+200)
        team = next(t for t in b['teams'] if t['id']=='t1')
        self.assertEqual((team['solved'], team['penalty']), (1, 1))
        self.assertEqual(team['problems'][0]['last_submit_second'], 100)
        self.assertEqual(team['problems'][1]['last_submit_second'], 120)
        self.assertEqual([e['status'] for e in self.history()['submissions']], ['CE', 'AC'])
        empty = self.history('t2', '0')
        self.assertEqual((empty['total'], empty['submissions']), (0, []))

    def test_iccpc_hidden_history_is_team_scoped_and_never_reveals_provider_ids(self):
        d = iccpc_fixture()
        d['solution'] = [iccpc_run(1, 0, 'hidden-provider-id', 60, 6),
                         iccpc_run(2, 0, 'hidden-provider-id', 14400, 4),
                         iccpc_run(3, 1, 'hidden-provider-id', 70, 11)]
        d['solution'][1]['language'] = 'C++'
        raw = server.iccpc_raw(d, ICCPC_PATH)
        station = self.store.station('changchun')
        key = server.source_key(server.board_source(station['board_url'])[0])
        raw['path'] = server.board_source(station['board_url'])[0]
        self.store.raw[key] = raw
        with patch.object(server.time, 'time', return_value=ICCPC_START+15000):
            b = self.store.board('changchun')['board']
            cell = next(t for t in b['teams'] if t['id']=='0')['hidden_problems'][0]
            h = self.store.submissions('changchun', key, '0', cell['history_key'])
            self.assertEqual(h['problem']['label'], '?')
            self.assertEqual([e['status'] for e in h['submissions']], ['FROZEN', 'WA'])
            self.assertEqual(h['submissions'][0]['language'], 'C++')
            self.assertEqual(cell['last_submit_second'], 14400)
            self.assertNotIn('hidden-provider-id', json.dumps(h))
            self.assertNotIn('_pid', json.dumps(h))
            with self.assertRaises(LookupError):
                self.store.submissions('changchun', key, '1', cell['history_key'])
            # Revealing a problem changes its label, not its history identity.
            raw['ccpc_meta']['frozen_show_all'] = True
            raw['problems'] = [dict(problem_id='hidden-provider-id', num=2)]
            h = self.store.submissions('changchun', key, '0', cell['history_key'])
            self.assertEqual(h['problem']['label'], 'C')
            self.assertEqual(h['submissions'][0]['status'], 'FROZEN')

    def test_http_route_validates_source_and_returns_sanitized_history(self):
        raw = fixture()
        raw['runs'] = [run('1', 't1', '0', 14400, 'AC')]
        self.store.raw[self.key] = raw
        httpd = server.BoardHTTPServer(('127.0.0.1', 0), server.Handler)
        httpd.store = self.store
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        def get(query, sid='xian'):
            connection = HTTPConnection(*httpd.server_address, timeout=5)
            connection.request('GET', '/api/contests/'+sid+'/submissions?'+query)
            response = connection.getresponse()
            body = json.loads(response.read())
            connection.close()
            return response.status, body
        with patch.object(server.time, 'time', return_value=START+15000):
            query = 'source='+self.key+'&team=t1&problem='+server.submission_key('t1', '0')
            status, body = get(query)
            self.assertEqual(status, 200)
            self.assertEqual(body['submissions'][0]['status'], 'FROZEN')
            self.assertNotIn('AC', json.dumps(body))
            self.assertEqual(get(query.replace(self.key, 'stale'))[0], 400)
            self.assertEqual(get(query.replace('team=t1', 'team=missing'))[0], 404)
            self.assertEqual(get(query.replace(server.submission_key('t1', '0'), 'missing'))[0], 404)
            self.assertEqual(get(query, 'missing')[0], 404)
            self.assertEqual(get('')[0], 400)


if __name__ == '__main__':
    unittest.main()
