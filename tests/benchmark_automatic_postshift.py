"""Offline synthetic automatic-appendix read/transfer/analysis measurements.

Run: PYTHONPATH=.:tests python tests/benchmark_automatic_postshift.py
SQLite row payload bytes are measured, not production network, tokens or cost.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gbop_voice_web import automatic_postshift as auto, market_data as market, market_payload_cache as payload_cache
from gbop_voice_web.market_watch_runtime import prepare_next_shift
from gbop_voice_web.voice_payload import voice_tool_payload, _encoded_size
from test_automatic_postshift import AutomaticPostShiftTests


def measurements():
    f=AutomaticPostShiftTests(); f.setUp()
    try:
        f.seed(f.cutoff,rows=[]); prepare_next_shift(f.db,{},f.cutoff)
        now=f.cutoff+180; f.seed(now)
        bars=f.base+f.later()
        f.conn.execute('INSERT INTO gbop_market_history VALUES(?,?,?,?,?)',
            (f.asset,f.asset+'m',60,f.cutoff//86400*86400,json.dumps(bars)))
        f.conn.create_function('md5',1,lambda value:hashlib.md5(value.encode()).hexdigest())
        stats={}; namespace=object()
        class Adapter:
            def execute(self,sql,params=()):
                stats['database_queries']+=1
                cursor=f.conn.execute(sql,params)
                class Cursor:
                    def measure(self,row):
                        if row is not None and 'payload' in row.keys():
                            size=len((row['payload'] or '').encode())
                            stats['all_payload_bytes_returned']+=size
                            if 'gbop_market_history' in sql or 'gbop_market_feed' in sql:
                                stats['market_payload_bytes_returned']+=size
                        return row
                    def fetchone(self): return self.measure(cursor.fetchone())
                    def fetchall(self): return [self.measure(row) for row in cursor.fetchall()]
                return Cursor()
            def market_history_row(self,identity):return payload_cache.read_history(self,namespace,identity)
            def market_feed_row(self,asset):return payload_cache.read_feed(self,namespace,asset)
        @contextmanager
        def db():yield Adapter()
        f.db=db
        rows=[]
        for name in ('frozen_recap_without_appendix','automatic_first','automatic_repeat'):
            if name!='automatic_repeat':
                with auto._cache_lock:auto._cache.clear()
                with payload_cache._lock:payload_cache._cache.clear();payload_cache._bytes=0
            stats.clear();stats.update(database_queries=0,all_payload_bytes_returned=0,market_payload_bytes_returned=0)
            start=perf_counter()
            with patch.object(market,'session_review',wraps=market.session_review) as analyzed:
                if name=='frozen_recap_without_appendix':
                    with patch.object(auto,'append_fresh_outcomes'): result=f.saved(now)
                else: result=f.saved(now)
            elapsed=(perf_counter()-start)*1000
            wire=voice_tool_payload('get_prepared_market_brief',result)
            assert wire['ok'],wire
            rows.append(dict(case=name,**stats,session_analyses=analyzed.call_count,
                local_elapsed_ms=round(elapsed,3),voice_characters=_encoded_size(wire),
                voice_budget=wire['voice_view']['character_budget'],
                later_ranges=len(result['review'].get('post_shift_outcomes',{}).get('ranges',[]))))
        assert rows[1]['session_analyses']==1 and rows[2]['session_analyses']==0
        # Existing prepared-feed validation may still transfer bytes.
        return {'synthetic_only':True,'paid_api_calls':0,'tokens_or_cost_measured':False,
            'method':'SQLite query count and returned payload UTF-8 bytes; local wall time, one sample. Same prepared cutoff facts and later candles.',
            'measurements':rows}
    finally:f.tearDown()


if __name__=='__main__':print(json.dumps(measurements(),indent=2))
