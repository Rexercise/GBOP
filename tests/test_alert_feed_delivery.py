"""Queue-to-send freshness regressions using an isolated SQLite watch database."""
import asyncio
import unittest
from unittest.mock import patch
import test_tab_release as fixture
from test_tab_release import T
from gbop_voice_web.market_watch_runtime import deliver_alerts


class DeliveryFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture.WatchTests();self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.start();self.fixture.poll()
        self.sent=[]
    async def send(self,user,text):
        self.sent.append(user);return 'synthetic-message'
    async def role(self,user):return True
    def deliver(self,now=T+340):
        return asyncio.run(deliver_alerts(self.fixture.db,1,42,self.send,self.role,now))
    def test_fresh_at_queue_stale_at_send_is_held_then_sent_once_after_recovery(self):
        with patch('gbop_voice_web.market_data.read_feed',return_value={'ok':True,'is_live':False}):
            self.assertEqual(self.deliver(),0)
        self.assertEqual(self.sent,[])
        self.assertEqual(self.fixture.conn.execute('SELECT state FROM gbop_market_alerts').fetchone()[0],'pending')
        self.assertEqual(self.deliver(T+350),1)
        self.assertEqual(self.deliver(T+360),0)
        self.assertEqual(self.sent,[7])
    def test_unknown_feed_check_does_not_send_or_claim_delivery(self):
        with patch('gbop_voice_web.market_data.read_feed',side_effect=TimeoutError('fixture')):
            self.assertEqual(self.deliver(),0)
        self.assertEqual(self.sent,[])
        self.assertEqual(self.fixture.conn.execute('SELECT last_status FROM gbop_market_watches').fetchone()[0],'paused_feed_check_unavailable')
    def test_outage_does_not_replay_expired_event_when_feed_returns(self):
        with patch('gbop_voice_web.market_data.read_feed',return_value={'ok':False}):
            self.assertEqual(self.deliver(),0)
        self.assertEqual(self.deliver(T+1000),0)
        self.assertEqual(self.sent,[])
        self.assertEqual(self.fixture.conn.execute('SELECT state FROM gbop_market_alerts').fetchone()[0],'cancelled')


if __name__=='__main__':unittest.main()
