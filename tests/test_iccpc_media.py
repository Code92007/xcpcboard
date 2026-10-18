import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import server

URL = 'https://rank.iccpc.cn/r/8a3a28e3-f6b3-4a58-b4ac-fa8039fb2c72'
PATH = server.board_source(URL)[0]
START = datetime.fromisoformat('2026-10-18 09:00:00').replace(tzinfo=server.ZoneInfo('Asia/Shanghai')).timestamp()


def fixture(n=2):
    return dict(contest=dict(title='长春', start_time='2026-10-18 09:00:00', end_time='2026-10-18 14:00:00', frozen_minute=60),
                team=[dict(team_id=str(i), name='队伍'+str(i), school='学校', tkind=0) for i in range(n)],
                problem=[], solution=[], ccpc_meta=dict(view='public', threshold=50, n_teams=n, n_problems=13, frozen_show_all=False),
                time_context=dict(wall_clock_timezone='Asia/Shanghai'))


def run(rid, team, pid, sec, result):
    return dict(solution_id=rid, team_id=str(team), problem_id=pid, result=result,
                in_date=(datetime(2026,10,18,9)+timedelta(seconds=sec)).isoformat(' '))


def board(data, sec=20000, **options):
    return server.build_board(server.iccpc_raw(data, PATH), dict(board_url=URL, **options), START+sec)


class IccpcTests(unittest.TestCase):
    def test_medals_use_configured_denominator_ratios_and_official_ranks(self):
        d = fixture(21)
        d['team'][1]['tkind'] = 1
        d['team'][20]['tkind'] = 2
        d['contest'].update(award_ratio=30020010, flg_award_base=1)
        d['solution'] = [run(i, i, 'hidden', 60*(i+1), 4) for i in range(10)]
        d['solution'] += [run(30, 10, 'hidden', 1, 11), run(31, 11, 'hidden', 2, 6), run(32, 20, 'hidden', 3, 4)]
        b = board(d, 1000)
        self.assertEqual(b['medal_cutoffs'], dict(gold=2, silver=4, bronze=8))
        teams = {t['id']:t for t in b['teams']}
        self.assertEqual([teams[str(i)]['medal'] for i in [0,1,2,4,8,20]], ['gold','gold','silver','bronze','',''])
        self.assertEqual(teams['0']['ranks']['all'], 2)
        for mode, limits in [(0, (1,3,6)), (2, (2,6,12))]:
            d['contest']['flg_award_base'] = mode
            self.assertEqual(tuple(board(d,1000)['medal_cutoffs'].values()), limits)
        d['contest'].update(flg_award_base=1, flg_award_qty_mode=1, award_ratio=6004003)
        self.assertEqual(board(d,1000)['medal_cutoffs'], dict(gold=3, silver=7, bronze=12))

    def test_public_prestart_empty_problem_list_is_valid_and_timezone_explicit(self):
        b=board(fixture(), -1)
        self.assertEqual((b['start'], b['state'], b['problem_total'], b['problems']), (START, 'pending', 13, []))
        self.assertEqual(len(b['teams']), 2)
        self.assertEqual(server.board_source(URL)[1], URL)
        self.assertEqual(server.board_source('https://rank.iccpc.cn/upload/outrank_attach/'+URL.rsplit('/',1)[1]+'/rank.json')[0], PATH)
        for bad in [URL.replace('rank.iccpc.cn', 'rank.iccpc.cn.evil.org'), URL+'/../abc', URL.replace('https','http'), URL+'#oops']:
            with self.assertRaises(ValueError):server.board_source(bad)

    def test_hidden_team_local_ids_never_become_public_columns_or_queue_letters(self):
        d=fixture()
        d['solution']=[run(1,0,'hmac-one-team',60,6),run(2,0,'hmac-one-team',122,4),run(3,1,'hmac-other-team',90,4)]
        b=board(d,500)
        self.assertEqual(b['problems'], [])
        self.assertEqual([t['solved'] for t in b['teams']], [1,1])
        self.assertEqual(b['teams'][1]['penalty'],22)
        self.assertEqual(b['teams'][1]['hidden_problems'][0]['wrong'],1)
        self.assertEqual({e['problem'] for e in b['events']},{'?'})
        self.assertNotIn('hmac-',json.dumps(b))
        # The wall clock passing freeze/end cannot expose a team-local identity.
        self.assertEqual(board(d,20000)['problems'],[])
        self.assertTrue(board(d,20000)['problem_reveal']['hidden'])

    def test_threshold_49_to_50_counts_teams_not_submissions_or_current_view(self):
        d=fixture(50)
        d['problem']=[dict(problem_id='internal-secret-id',num=2,color='#eee')]
        d['solution']=[run(i,i,'internal-secret-id',60+i,4) for i in range(49)]
        d['solution'].append(run(100,0,'internal-secret-id',120,4))
        self.assertEqual(board(d,500)['problems'],[])
        d['solution'].append(run(101,49,'internal-secret-id',130,4))
        b=board(d,500)
        self.assertEqual([(p['label'],p['accepted']) for p in b['problems']],[('C',50)])
        self.assertEqual({e['problem'] for e in b['events']},{'C'})
        self.assertNotIn('internal-secret-id',json.dumps(b))
        self.assertTrue(all(not t['hidden_problems'] for t in b['teams']))

    def test_provider_show_all_and_freeze_results_are_independent(self):
        d=fixture()
        d['problem']=[dict(problem_id=1001,num=0),dict(problem_id=1002,num=1)]
        d['ccpc_meta']['frozen_show_all']=True
        d['solution']=[run(1,0,1001,14399,4),run(2,0,1002,14400,4),run(3,1,1002,14401,-4)]
        for now in [15000,20000]:
            b=board(d,now)
            self.assertEqual([p['label'] for p in b['problems']],['A','B'])
            self.assertEqual([e['status'] for e in b['events']],['AC','FROZEN','FROZEN'])
            self.assertEqual(b['teams'][0]['solved'],1)
        b=board(d,reveal=True)
        self.assertEqual([e['status'] for e in b['events']],['AC','AC','FROZEN'])

    def test_seconds_accumulate_then_floor_ce_ignored_pe_penalized(self):
        d=fixture()
        d['team'][1]['tkind']=2
        d['ccpc_meta']['view']='referee'
        d['problem']=[dict(problem_id=1,num=0),dict(problem_id=2,num=1)]
        d['solution']=[run(1,0,1,1,11),run(2,0,1,2,5),run(3,0,1,119,4),run(4,0,2,179,4),run(5,1,1,59,4)]
        b=board(d,500)
        t=next(t for t in b['teams'] if t['id']=='0')
        self.assertEqual((t['penalty'],t['solved'],t['problems'][0]['wrong']),(24,2,1))
        self.assertEqual(t['ranks']['official'],1)
        self.assertNotIn('official',next(t for t in b['teams'] if t['id']=='1')['ranks'])

    def test_snapshot_fallback_and_shared_payload_compression(self):
        with tempfile.TemporaryDirectory() as temp:
            store=server.Store(temp)
            store.update('changchun',dict(board_url=URL))
            d=fixture()
            with patch.object(store.fetcher,'get',return_value=d):store.refresh(store.station('changchun'))
            with patch.object(server.time,'time',return_value=START-5),patch.object(server,'build_board',wraps=server.build_board) as build:
                with ThreadPoolExecutor(max_workers=20) as pool:
                    results=list(pool.map(lambda _:store.payload('changchun',True),range(40)))
                self.assertEqual(build.call_count,1)
                self.assertTrue(all(r is results[0] for r in results))
                self.assertEqual(json.loads(gzip.decompress(results[0]))['board']['problem_total'],13)
                store.update('changchun',dict(title='新标题'))
                self.assertEqual(json.loads(store.payload('changchun'))['station']['title'],'新标题')
            key=server.source_key(PATH)
            old=copy.deepcopy(store.raw[key])
            d['solution']=[run(1,0,'hidden',60,4)]
            with patch.object(store.fetcher,'get',return_value=d):store.refresh(store.station('changchun'))
            # Invalid response never replaces the last good snapshot; restart recovers disk data.
            with patch.object(store.fetcher,'get',return_value={}):store.refresh(store.station('changchun'))
            recovered=server.Store(temp)
            self.assertEqual(len(recovered.raw[key]['runs']),1)
            self.assertEqual(old['problems'],[])


class MediaTests(unittest.TestCase):
    def test_live_video_ids_pages_and_only_official_embeds(self):
        for value,kind in [('12345','live'),('https://live.bilibili.com/12345?from=search','live'),('BV1B7411m7LV','video'),('av170001','video'),('https://www.bilibili.com/video/BV1B7411m7LV?p=2','video')]:
            item=server.media_item(dict(url=value,title='节目'))
            self.assertEqual(item['type'],kind)
            self.assertEqual(item['title'],'节目')
            self.assertIn('autoplay=0',item['embed_url'])
        self.assertIn('p=2',item['embed_url'])
        for bad in ['javascript:alert(1)','https://player.bilibili.com.evil.org/player.html?bvid=BV1B7411m7LV','https://www.bilibili.com/blackboard/foo','https://b23.tv/test','https://www.bilibili.com/video/BV1B7411m7LV?p=0']:
            with self.assertRaises(ValueError):server.media_item(dict(url=bad))

    def test_legacy_migration_playlist_order_restart_clear_and_invalid_atomicity(self):
        with tempfile.TemporaryDirectory() as temp:
            store=server.Store(temp)
            s=store.update('xian',dict(room='12345'))
            self.assertEqual(s['media'][0]['type'],'live')
            s=store.update('xian',dict(media=[dict(url='BV1B7411m7LV',title='回放'),dict(url='12345',title='直播')]))
            self.assertEqual([x['title'] for x in s['media']],['回放','直播'])
            self.assertEqual(server.Store(temp).stations()[0]['media'],s['media'])
            before=copy.deepcopy(store.config)
            for media in [[dict(url='12345'),dict(url='https://live.bilibili.com/12345')],[dict(url='https://evil.org/a')],[dict(url='12345')]*21]:
                with self.assertRaises(ValueError):store.update('xian',dict(title='不应写入',media=media))
            self.assertEqual(store.config,before)
            # A legacy client changing the live room must retain ordinary videos.
            s=store.update('xian',dict(room='98765'))
            self.assertEqual([x['type'] for x in s['media']],['video','live'])
            s=store.update('xian',dict(media=[]))
            self.assertEqual((s['room'],s['media']),('',[]))
