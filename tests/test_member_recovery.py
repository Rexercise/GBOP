import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from gbop_voice_web.voice_runtime import VoiceRateLimitRecovery


class RecoverySafetyTests(unittest.TestCase):
    def setUp(self):
        self.session = SimpleNamespace(
            _voice_turn_count=4, _last_response_options={'max_output_tokens': 2200},
            _voice_tools=[{'type': 'function', 'name': n} for n in
                ('get_journal_history', 'review_market_smt', 'open_trade', 'delete_journal', 'send_journal_history')],
            closed=False, websocket=object(), send_event=AsyncMock(),
            member=SimpleNamespace(send=AsyncMock()))
        self.recovery = VoiceRateLimitRecovery(self.session)
        self.recovery.recovery_turn = 4

    def test_recovery_keeps_journal_and_market_reads(self):
        options = self.recovery.response_options()
        self.assertEqual({t['name'] for t in options['tools']}, {'get_journal_history', 'review_market_smt'})
        self.assertEqual(options['tool_choice'], 'auto')
        self.assertEqual(options['max_output_tokens'], 2200)

    def test_recovery_blocks_writes_and_delivery_server_side(self):
        for name in ('open_trade', 'delete_journal', 'send_journal_history', 'unknown_tool'):
            self.assertFalse(self.recovery.allows_tool(name))
        self.assertTrue(self.recovery.allows_tool('get_journal_history'))

    def test_completed_retry_does_not_reenable_mutations_in_same_turn(self):
        self.recovery.cancel(reset=True)
        self.assertFalse(self.recovery.allows_tool('delete_journal'))

    def test_new_member_turn_restores_normal_tool_policy(self):
        self.session._voice_turn_count += 1
        self.assertTrue(self.recovery.allows_tool('send_journal_history'))

    def test_absent_tool_schema_fails_closed(self):
        del self.session._voice_tools
        self.assertEqual(self.recovery.response_options()['tools'], [])
        self.assertEqual(self.recovery.response_options()['tool_choice'], 'none')


class RecoveryEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_creates_read_only_response(self):
        session = SimpleNamespace(_voice_turn_count=4, _last_response_options={},
            _voice_tools=[{'type': 'function', 'name': 'get_journal_history'},
                          {'type': 'function', 'name': 'delete_trade'}],
            closed=False, websocket=object(), send_event=AsyncMock(),
            member=SimpleNamespace(send=AsyncMock()))
        recovery = VoiceRateLimitRecovery(session)
        await recovery._recover(0, False)
        event = session.send_event.call_args.args[0]
        self.assertEqual(event['type'], 'response.create')
        self.assertEqual([t['name'] for t in event['response']['tools']], ['get_journal_history'])
        self.assertTrue(recovery.active_for_current_turn())
        self.assertFalse(recovery.allows_tool('delete_trade'))
