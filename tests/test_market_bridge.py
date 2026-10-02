import contextlib
import copy
from datetime import datetime
import os
import sqlite3
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from gbop_voice_web import market_data as market
from gbop_voice_web.market_routes import market_router
from market_bridge.bridge import collect


class MarketTests(unittest.TestCase):
    def setUp(self):
        self.now = int(datetime(2026, 10, 2, 10, tzinfo=market.NY).timestamp())
        self.conn = sqlite3.connect(':memory:', check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(market.CREATE_SQL)
        @contextlib.contextmanager
        def db():
            with self.conn:
                yield self.conn
        self.db = db
        self.payload = {'captured_at': self.now, 'instruments': [dict(asset='NAS100', symbol='USTECm', bid=101, ask=102, tick_time=self.now, bars=[])]}

    def tearDown(self):
        self.conn.close()

    def test_freshness_uses_tick_not_upload_time(self):
        market.ingest(self.db, self.payload, self.now)
        self.assertTrue(market.read_feed(self.db, 'NAS', self.now)['is_live'])
        self.assertFalse(market.read_feed(self.db, 'NAS100', self.now + 121)['is_live'])
        self.payload['captured_at'] += 200
        market.ingest(self.db, self.payload, self.now + 200)
        self.assertEqual(market.read_feed(self.db, 'NAS', self.now + 200)['status'], 'stale')

    def test_out_of_order_and_duplicate_do_not_rewrite(self):
        market.ingest(self.db, self.payload, self.now)
        self.payload['captured_at'] -= 1
        self.payload['instruments'][0]['bid'] = 99
        market.ingest(self.db, self.payload, self.now)
        self.assertEqual(market.read_feed(self.db, 'NAS', self.now)['bid'], 101)
        self.payload['captured_at'] += 1
        market.ingest(self.db, self.payload, self.now)
        self.assertEqual(market.read_feed(self.db, 'NAS', self.now)['bid'], 101)

    def test_validation_rejects_corrupt_data_atomically(self):
        changes = [dict(bid=float('nan')), dict(ask=99), dict(tick_time=self.now+100), dict(symbol='bad token'), dict(asset='UNKNOWN')]
        for change in changes:
            data = copy.deepcopy(self.payload)
            data['instruments'][0].update(change)
            with self.assertRaises(ValueError):
                market.ingest(self.db, data, self.now)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM gbop_market_feed').fetchone()[0], 0)
        data = copy.deepcopy(self.payload)
        data['instruments'].append(dict(data['instruments'][0]))
        with self.assertRaises(ValueError): market.validate_payload(data, self.now)

    def bars(self, day='2026-10-02', shift='day'):
        date = datetime.fromisoformat(day)
        offset = 12 if shift == 'night' else 0
        bars=[]
        for h in (7,8,9):
            ts = int(date.replace(hour=h+offset, tzinfo=market.NY).timestamp())
            for n in range(12):
                bars.append(dict(time=ts+n*300, open=100, high=110 if h != 9 else 112, low=90 if h != 9 else 95, close=100))
        return bars

    def test_complete_hour_sweep_candidate_with_primary_target(self):
        result = market.session_review(self.bars(), '2026-10-02', 'day')['observations']
        self.assertEqual([r['status'] for r in result], ['range_sweep_candidate']*2)
        self.assertEqual(result[0]['direction'], 'bearish')
        self.assertEqual(result[0]['primary_target'], 90)
        self.assertFalse(result[0]['entry_confirmed'])

    def test_incomplete_hour_never_becomes_no_setup(self):
        bars = self.bars(); bars.pop(15)
        result = market.session_review(bars, '2026-10-02', 'day')['observations']
        self.assertTrue(all(r['status']=='insufficient_closed_candles' for r in result))

    def test_outside_close_invalidates(self):
        bars=self.bars(); bars[-1]['close']=111
        result=market.session_review(bars,'2026-10-02','day')['observations']
        self.assertTrue(all(r['status']=='invalidated_by_hourly_close' for r in result))

    def test_young_lefty_intervening_close_invalidates(self):
        bars=self.bars(); bars[23]['high']=112; bars[23]['close']=111
        result=market.session_review(bars,'2026-10-02','day')['observations']
        self.assertEqual(result[1]['status'],'invalidated_by_hourly_close')

    def test_both_sides_has_no_invented_direction(self):
        bars=self.bars(); bars[-1]['low']=89
        result=market.session_review(bars,'2026-10-02','day')['observations'][0]
        self.assertEqual(result['status'],'both_sides_swept_order_unknown')
        self.assertIsNone(result['direction'])

    def test_dst_and_night(self):
        for day in ['2026-10-02','2026-12-02']:
            for shift in ['day','night']:
                result=market.session_review(self.bars(day,shift),day,shift)['observations'][0]
                self.assertEqual(result['status'],'range_sweep_candidate')
                self.assertEqual(result['anchor_hour_ny'],8 if shift=='day' else 20)

    def test_candle_validation_rejects_open_duplicate_and_bad_ohlc(self):
        for bars in [[dict(time=self.now,open=100,high=110,low=90,close=100)],
                     [dict(time=self.now-300,open=100,high=99,low=90,close=100)]]:
            self.payload['instruments'][0]['bars']=bars
            with self.assertRaises(ValueError): market.validate_payload(self.payload,self.now)
        bars=self.bars(); self.payload['instruments'][0]['bars']=bars+[bars[-1]]
        with self.assertRaises(ValueError): market.validate_payload(self.payload,self.now)

    def test_absent_price_honestly_unavailable(self):
        result=market.market_tool(self.db,'get_market_price',{'asset':'gold'})
        self.assertEqual(result['status'],'not_connected')

    def test_http_auth_limits_and_roundtrip(self):
        async def auth(request):
            if request.headers.get('x-test-member')!='yes': raise HTTPException(401)
        app=FastAPI(); app.include_router(market_router(self.db,auth))
        with TestClient(app) as client, patch.dict(os.environ, {'GBOP_MARKET_BRIDGE_TOKEN':'x'*40}):
            self.assertEqual(client.post('/api/market/ingest',json=self.payload).status_code,401)
            headers={'Authorization':'Bearer '+'x'*40}
            with patch.object(market.time,'time',return_value=self.now):
                self.assertEqual(client.post('/api/market/ingest',json=self.payload,headers=headers).status_code,200)
                self.assertEqual(client.get('/api/market/NAS').status_code,401)
                response=client.get('/api/market/NAS',headers={'x-test-member':'yes'})
                self.assertTrue(response.json()['is_live'])
                self.assertNotIn('bars',response.json())
            self.assertEqual(client.post('/api/market/ingest',content=b'x'*(market.MAX_BYTES+1),headers=headers).status_code,413)
            self.assertEqual(client.post('/api/market/ingest',json={'bad':1},headers=headers).status_code,422)
        with TestClient(app) as client, patch.dict(os.environ, {'GBOP_MARKET_BRIDGE_TOKEN':''}):
            self.assertEqual(client.post('/api/market/ingest',json=self.payload).status_code,503)

    def test_collector_closed_bars_only_no_trading_methods(self):
        class FakeMT5:
            TIMEFRAME_M5=5
            def terminal_info(s): return SimpleNamespace(connected=True)
            def symbol_select(s,symbol,enable): return True
            def symbol_info_tick(s,symbol): return SimpleNamespace(bid=101,ask=102,time=self.now)
            def copy_rates_from_pos(s,symbol,tf,pos,count):
                self.assertEqual(pos,1)
                return [dict(time=self.now-300,open=100,high=105,low=99,close=102),dict(time=self.now,open=102,high=105,low=101,close=103)]
        payload=collect(FakeMT5(),{'NAS100':'USTECm'},self.now)
        market.validate_payload(payload,self.now)
        self.assertEqual(len(payload['instruments'][0]['bars']),1)


if __name__=='__main__': unittest.main()
