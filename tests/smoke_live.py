"""Explicit integration smoke against the public warmup data (requires network)."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import tempfile
import server

with tempfile.TemporaryDirectory() as directory:
    store=server.Store(directory)
    store.refresh(store.station('xian'))
    result=store.board('xian')
    assert result['board'],result['health']
    board=result['board']
    assert len(board['teams'])>300
    assert len(board['problems'])==3
    assert board['frozen']
    assert all(e['status']=='FROZEN' for e in board['events'] if e['second']>=5400)
    print({k:board[k] for k in ['title','state','frozen','total_runs']})
    print('Teams:',len(board['teams']))
    print('Top 3:',[(t['name'],t['solved'],t['penalty']) for t in board['teams'][:3]])
    restarted=server.Store(directory)
    assert restarted.board('xian')['board']['teams']==board['teams']
    print('Restart cache recovery: OK')
