"""Synthetic feed incidents and owner-only responses; no external messages."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch
from fastapi import HTTPException
from starlette.responses import JSONResponse
from gbop_voice_web.feed_operations import ASSETS, assess_feeds, log_transition
from test_owner_maintenance import load_function

NOW = 2000000000


def snapshots(capture=20, tick=20, receipt=20):
    return [dict(asset=a,captured_at=NOW-capture,received_at=NOW-receipt,tick_time=str(NOW-tick)) for a in ASSETS]


class FeedOperationsTests(unittest.TestCase):
    def test_all_assets_required(self):
        self.assertEqual(assess_feeds(snapshots(),NOW)['status'],'healthy')
        result=assess_feeds(snapshots()[:-1],NOW)
        self.assertEqual(result['status'],'collector_attention')
        self.assertEqual(sum(f['attention']=='no_snapshot' for f in result['feeds']),1)

    def test_new_upload_does_not_make_closed_market_quote_fresh(self):
        result=assess_feeds(snapshots(tick=86400),NOW)
        self.assertEqual(result['status'],'quotes_unavailable')
        self.assertEqual(result['broker_session'],'unknown')
        self.assertIn('closure or a broker issue is unverified',result['notice'])
        self.assertFalse(result['external_notification_sent'])

    def test_capture_cadence_not_upload_receipt_controls_incident(self):
        result=assess_feeds(snapshots(capture=301,receipt=1),NOW)
        self.assertEqual(result['status'],'collector_attention')
        self.assertTrue(all(f['attention']=='collector_upload_overdue' for f in result['feeds']))

    def test_delay_and_outage_thresholds(self):
        for age,status in ((120,'healthy'),(121,'delayed'),(300,'delayed'),(301,'collector_attention')):
            with self.subTest(age=age):
                self.assertEqual(assess_feeds(snapshots(capture=age),NOW)['status'],status)

    def test_future_missing_and_malformed_timestamps_are_never_healthy(self):
        for field,value in (('tick_time',str(NOW+1)),('captured_at',True),('received_at',None),('tick_time','bad')):
            rows=snapshots();rows[0][field]=value
            self.assertEqual(assess_feeds(rows,NOW)['status'],'unverified')

    def test_logs_once_on_change_then_records_recovery_without_sending(self):
        logger=Mock();bad=assess_feeds(snapshots(capture=301),NOW)
        state=log_transition(None,bad,logger)
        log_transition(state,bad,logger)
        self.assertEqual(logger.warning.call_count,1)
        log_transition(state,assess_feeds(snapshots(),NOW),logger)
        self.assertEqual(logger.info.call_count,1)

    def endpoint(self,auth):
        return load_function('gbop_voice_web/server.py','owner_feed_health',{
            'require_authenticated_user':auth,'OWNER_USER_ID':99,'HTTPException':HTTPException,
            'JSONResponse':JSONResponse,'asyncio':asyncio,'db':Mock()})

    def test_forged_owner_boolean_cannot_admit_another_member(self):
        auth=AsyncMock(return_value={'user_id':20,'is_owner':True})
        with patch('gbop_voice_web.feed_operations.operations_health') as read:
            with self.assertRaises(HTTPException) as error:
                asyncio.run(self.endpoint(auth)(object()))
            self.assertEqual(error.exception.status_code,403);read.assert_not_called()

    def test_unauthenticated_denied_before_read(self):
        auth=AsyncMock(side_effect=HTTPException(status_code=401))
        with patch('gbop_voice_web.feed_operations.operations_health') as read:
            with self.assertRaises(HTTPException):asyncio.run(self.endpoint(auth)(object()))
            read.assert_not_called()

    def test_owner_response_no_cache_and_sanitized_failure(self):
        auth=AsyncMock(return_value={'user_id':99})
        with patch('gbop_voice_web.feed_operations.operations_health',return_value=assess_feeds(snapshots(),NOW)):
            result=asyncio.run(self.endpoint(auth)(object()))
        self.assertEqual(result.headers['cache-control'],'no-store')
        self.assertEqual(json.loads(result.body)['status'],'healthy')
        with patch('gbop_voice_web.feed_operations.operations_health',side_effect=RuntimeError('secret connection string')):
            failed=asyncio.run(self.endpoint(auth)(object()))
        self.assertEqual(failed.status_code,503)
        self.assertNotIn('secret',failed.body.decode())
        self.assertEqual(json.loads(failed.body)['status'],'unverified')


if __name__=='__main__':unittest.main()
