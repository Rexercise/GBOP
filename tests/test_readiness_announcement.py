"""Offline real owner-command integration, file SQLite and single-attempt HTTP.

No bot login, external requests or real members. Discord components/command
decorators, recipient gate and receipt SQL are real; guild/interaction HTTP data
is synthetic. SQLite does not assert PostgreSQL grants/RLS or locking behavior.
"""
import ast
import asyncio
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
import test_owner_broadcast_guided as guided
from gbop_voice_web import readiness_announcement as campaign
from gbop_voice_web.member_access import member_access_error


class ReadinessIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'campaign.sqlite'
        with self.db() as conn:
            conn.execute('''CREATE TABLE gbop_shift_deliveries(event_key TEXT,guild_id INTEGER,
                user_id INTEGER,discord_message_id TEXT,delivered_at TEXT,
                PRIMARY KEY(event_key,guild_id,user_id))''')
            conn.execute('''CREATE TABLE members(guild_id INTEGER,user_id INTEGER,
                activated INTEGER,leadership_ack INTEGER,revoked INTEGER)''')
            conn.executemany('INSERT INTO members VALUES(10,?,1,1,0)', [(1,), (2,), (3,)])
        self.requests = []
        self.message_requests = []
        self.outcome = 200
        self.wrong_channel = False
        self.wrong_identity = False
        self.send_callback = None
        self.channel_callback = None
        self.original_sender = campaign.SingleAttemptDM
        self.original_deliver = campaign.deliver
        self.pause = AsyncMock()

        def factory(token, expected_bot_id, *, authorize):
            self.assertEqual(token, 'synthetic-bot-token')
            return self.original_sender(token, expected_bot_id, authorize=authorize,
                                        transport=httpx.MockTransport(self.http))

        async def immediate(*args, **kwargs):
            return await self.original_deliver(*args, **kwargs, pause=self.pause)
        self.patch_sender = patch.object(campaign, 'SingleAttemptDM', side_effect=factory)
        self.patch_deliver = patch.object(campaign, 'deliver', side_effect=immediate)
        self.patch_sender.start()
        self.patch_deliver.start()
        self.addCleanup(self.patch_sender.stop)
        self.addCleanup(self.patch_deliver.stop)
        await self.new_session()

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    async def new_session(self):
        self.ui = guided.GuidedOwnerBroadcastTests()
        await self.ui.asyncSetUp()
        self.addAsyncCleanup(self.ui.sdk_client.close)
        self.ns = self.ui.ns
        self.ns.update(readiness_announcement=campaign, db=self.db,
                       member_access_error=member_access_error, DISCORD_TOKEN='synthetic-bot-token')
        self.ns['client'].is_ready = Mock(return_value=True)
        self.ns['client'].user = NS(id=900)
        names = {'_readiness_authorized_member', '_send_readiness_broadcast'}
        nodes = [node for node in ast.parse(guided.BOT.read_text()).body if getattr(node, 'name', '') in names]
        self.assertEqual({node.name for node in nodes}, names)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(guided.BOT), 'exec'), self.ns)

    async def http(self, request):
        path = request.url.path
        self.requests.append((request.method, path))
        self.assertEqual(request.headers['authorization'], 'Bot synthetic-bot-token')
        if path == '/api/v10/users/@me':
            return httpx.Response(200, json={'id': '901' if self.wrong_identity else '900', 'bot': True})
        body = json.loads(request.content)
        if path == '/api/v10/users/@me/channels':
            uid = int(body['recipient_id'])
            if self.channel_callback:
                await self.channel_callback(uid)
            return httpx.Response(200, json={'id': str(1000 + uid), 'type': 1,
                'recipients': [{'id': str(uid + 1 if self.wrong_channel else uid)}]})
        self.assertTrue(path.endswith('/messages'))
        channel = path.split('/')[-2]
        uid = int(channel) - 1000
        self.message_requests.append(uid)
        self.assertEqual(body, {'content': campaign.MESSAGE, 'allowed_mentions': {'parse': []}})
        # A genuinely new connection sees the committed claim before the POST.
        self.assertEqual(self.receipts()[uid]['state'], 'pending')
        self.assertEqual(self.receipts()[0]['count'], len(self.receipts()) - 1)
        if self.send_callback:
            await self.send_callback(uid)
        if self.outcome == 'timeout':
            raise httpx.ReadTimeout('synthetic uncertain response')
        if self.outcome != 200:
            return httpx.Response(self.outcome, json={'retry_after': 60})
        return httpx.Response(200, json={'id': str(2000 + uid), 'channel_id': channel, 'author': {'id': '900'}})

    def receipts(self):
        with self.db() as conn:
            rows = conn.execute('SELECT user_id,discord_message_id FROM gbop_shift_deliveries').fetchall()
        return {row['user_id']: json.loads(row['discord_message_id']) for row in rows}

    async def preview(self, **kwargs):
        interaction = guided.SyntheticInteraction()
        await self.ui.command.callback(interaction, readiness=True, **kwargs)
        self.assertEqual(interaction.events, ['defer', 'followup'])
        args = interaction.followup.send.await_args
        self.assertTrue(args.kwargs['ephemeral'])
        self.assertEqual(args.args[0], campaign.MESSAGE)
        view = args.kwargs['view']
        self.assertEqual(view.draft.campaign, campaign.CAMPAIGN)
        return view

    async def confirm(self, view):
        interaction = guided.SyntheticInteraction()
        await view.confirm_send.callback(interaction)
        return interaction

    async def test_optional_readiness_schema_and_preview_have_no_receipt_or_send(self):
        schema = self.ui.command.to_dict(self.ui.tree)
        option = next(item for item in schema['options'] if item['name'] == 'readiness')
        self.assertEqual(option['type'], 5)
        self.assertFalse(option['required'])
        view = await self.preview()
        self.assertEqual(tuple(r.user_id for r in view.draft.recipients), (1, 2, 3))
        self.assertEqual(self.receipts(), {})
        self.assertEqual(self.requests, [])

    async def test_all_pages_and_owner_confirmation_required_before_send(self):
        members = [guided.member(uid) for uid in range(1, 13)]
        self.ui.guild(members)
        with self.db() as conn:
            conn.executemany('INSERT INTO members VALUES(10,?,1,1,0)', [(uid,) for uid in range(4, 13)])
        view = await self.preview()
        await self.confirm(view)
        self.assertEqual(self.receipts(), {})
        self.assertEqual(self.requests, [])
        await view.next_page.callback(guided.SyntheticInteraction())
        rejected = guided.SyntheticInteraction(user_id=1)
        await view.confirm_send.callback(rejected)
        self.assertEqual(self.receipts(), {})
        await self.confirm(view)
        self.assertEqual(self.message_requests, list(range(1, 13)))

    async def test_command_rejects_broader_audience_text_image_or_ambiguous_exclusions(self):
        for kwargs in ({'audience': 'selected'}, {'audience': 'all_server_members'},
                       {'text': 'different'}, {'image': guided.upload()},
                       {'audience': 'all_except'}, {'audience': 'all_except', 'members': 'Alice'}):
            with self.subTest(kwargs=kwargs):
                interaction = guided.SyntheticInteraction()
                await self.ui.command.callback(interaction, readiness=True, **kwargs)
                self.assertEqual(interaction.events, ['send_message'])
        for interaction in (guided.SyntheticInteraction(user_id=1), guided.SyntheticInteraction(guild_id=20)):
            await self.ui.command.callback(interaction, readiness=True)
            self.assertEqual(interaction.events, ['send_message'])
        self.assertEqual(self.requests, [])
        self.assertEqual(self.receipts(), {})

    async def test_reopen_new_command_cannot_resend_or_add_excluded_recipient(self):
        first = await self.preview(audience='all_except', members='2')
        result = await self.confirm(first)
        self.assertIn('**2** confirmed', result.followup.send.await_args.args[0])
        saved = self.receipts()
        self.assertEqual(set(saved), {0, 1, 3})
        self.assertEqual(saved[1]['message_id'], '2001')
        self.assertEqual(saved[1]['channel_id'], '1001')
        self.assertEqual(saved[1]['body_sha256'], campaign.BODY_HASH)
        await self.new_session()  # new SDK view state; every DB connection is closed/reopened
        second = await self.confirm(await self.preview())
        self.assertIn('already started', second.followup.send.await_args.args[0])
        self.assertEqual(self.receipts(), saved)
        self.assertEqual(self.message_requests, [1, 3])

    async def test_concurrent_confirmations_have_one_durable_campaign_winner(self):
        one, two = await self.preview(), await self.preview()
        await asyncio.gather(self.confirm(one), self.confirm(two))
        self.assertEqual(sorted(self.message_requests), [1, 2, 3])
        self.assertEqual(campaign.snapshot(self.db, 10, 999).sent, 3)

    async def test_revocation_between_preview_and_send_is_skipped(self):
        view = await self.preview()
        with self.db() as conn:
            conn.execute('UPDATE members SET revoked=1 WHERE user_id=2')
        await self.confirm(view)
        self.assertEqual(self.message_requests, [1, 3])
        self.assertEqual(self.receipts()[2]['state'], 'skipped')

    async def test_revocation_after_claim_is_rechecked_before_post(self):
        original = self.ns['_readiness_authorized_member']
        calls = {}
        async def authorize(uid):
            calls[uid] = calls.get(uid, 0) + 1
            if uid == 1 and calls[uid] == 3:
                with self.db() as conn:
                    conn.execute('UPDATE members SET revoked=1 WHERE user_id=1')
            return await original(uid)
        self.ns['_readiness_authorized_member'] = authorize
        await self.confirm(await self.preview())
        self.assertEqual(self.message_requests, [2, 3])
        self.assertEqual(self.receipts()[1]['state'], 'skipped')

    async def test_revocation_during_channel_creation_prevents_content_post(self):
        async def revoke(uid):
            if uid == 1:
                with self.db() as conn:
                    conn.execute('UPDATE members SET revoked=1 WHERE user_id=1')
        self.channel_callback = revoke
        await self.confirm(await self.preview())
        self.assertEqual(self.message_requests, [2, 3])
        self.assertEqual(self.receipts()[1]['state'], 'skipped')

    async def test_access_unavailable_in_complete_preflight_never_claims_or_sends(self):
        view = await self.preview()
        original = self.ns['_readiness_authorized_member']
        async def authorize(uid):
            if uid == 3:
                raise RuntimeError('synthetic access outage')
            return await original(uid)
        self.ns['_readiness_authorized_member'] = authorize
        result = await self.confirm(view)
        self.assertIn('could not be verified', result.followup.send.await_args.args[0])
        self.assertEqual(self.requests, [])
        self.assertEqual(self.receipts(), {})

    async def test_wrong_bot_or_unready_client_never_claims_or_sends(self):
        view = await self.preview()
        self.ns['client'].is_ready.return_value = False
        await self.confirm(view)
        self.assertEqual(self.requests, [])
        self.ns['client'].is_ready.return_value = True
        self.wrong_identity = True
        await self.confirm(await self.preview())
        self.assertEqual(self.receipts(), {})
        self.assertEqual(self.message_requests, [])

    async def test_uncertain_http_never_retries_or_advances_or_resumes_after_reopen(self):
        self.outcome = 'timeout'
        await self.confirm(await self.preview())
        self.assertEqual(self.message_requests, [1])
        self.assertEqual([self.receipts()[uid]['state'] for uid in (1, 2, 3)], ['uncertain', 'queued', 'queued'])
        await self.new_session()
        self.outcome = 200
        await self.confirm(await self.preview())
        self.assertEqual(self.message_requests, [1])

    async def test_rate_limit_stops_campaign_and_later_command_does_not_retry(self):
        self.outcome = 429
        await self.confirm(await self.preview())
        await self.confirm(await self.preview())
        self.assertEqual(self.message_requests, [1])
        self.assertEqual(self.receipts()[1]['state'], 'rate_limited')
        self.assertEqual(self.receipts()[2]['state'], 'queued')

    async def test_closed_dms_are_recorded_without_retry_and_other_members_continue(self):
        self.outcome = 403
        await self.confirm(await self.preview())
        self.assertEqual(self.message_requests, [1, 2, 3])
        self.assertEqual(campaign.snapshot(self.db, 10, 999).blocked, 3)
        await self.confirm(await self.preview())
        self.assertEqual(self.message_requests, [1, 2, 3])

    async def test_lost_claim_commit_reply_prevents_all_delivery_and_preserves_snapshot(self):
        real_claim = campaign.claim_campaign
        def uncertain(*args):
            real_claim(*args)
            raise RuntimeError('synthetic lost commit reply')
        with patch.object(campaign, 'claim_campaign', side_effect=uncertain):
            await self.confirm(await self.preview())
        self.assertEqual(self.message_requests, [])
        self.assertEqual(campaign.snapshot(self.db, 10, 999).queued, 3)
        await self.confirm(await self.preview())
        self.assertEqual(self.message_requests, [])

    async def test_atomic_audience_claim_rolls_back_if_any_receipt_insert_fails(self):
        with self.db() as conn:
            conn.execute('''CREATE TRIGGER fixture_fail_third BEFORE INSERT ON gbop_shift_deliveries
                WHEN NEW.user_id=3 BEGIN SELECT RAISE(ABORT,'synthetic receipt constraint'); END''')
        await self.confirm(await self.preview())
        self.assertEqual(self.receipts(), {})
        self.assertEqual(self.message_requests, [])
        with self.db() as conn:
            conn.execute('DROP TRIGGER fixture_fail_third')
        await self.confirm(await self.preview())
        self.assertEqual(self.message_requests, [1, 2, 3])

    async def test_missing_delivery_schema_fails_closed_without_runtime_ddl(self):
        view = await self.preview()
        with self.db() as conn:
            conn.execute('DROP TABLE gbop_shift_deliveries')
        await self.confirm(view)
        self.assertEqual(self.requests, [])
        with self.db() as conn:
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='gbop_shift_deliveries'").fetchone())

    async def test_lost_success_receipt_preserves_pending_and_never_resends(self):
        original = campaign.transition
        def fail_success(db, guild, uid, before, receipt):
            if receipt.state == 'sent':
                raise RuntimeError('synthetic receipt store unavailable')
            return original(db, guild, uid, before, receipt)
        with patch.object(campaign, 'transition', side_effect=fail_success):
            await self.confirm(await self.preview())
        self.assertEqual(self.message_requests, [1])
        self.assertEqual(campaign.snapshot(self.db, 10, 999).uncertain, 1)
        await self.new_session()
        await self.confirm(await self.preview())
        self.assertEqual(self.message_requests, [1])

    async def test_cancelled_delivery_keeps_claim_and_suppresses_later_send(self):
        async def cancelled(uid):
            raise asyncio.CancelledError()
        self.send_callback = cancelled
        with self.assertRaises(asyncio.CancelledError):
            await self.confirm(await self.preview())
        self.assertEqual(self.receipts()[1]['state'], 'pending')
        self.send_callback = None
        await self.confirm(await self.preview())
        self.assertEqual(self.message_requests, [1])

    async def test_bounded_timeout_returns_owner_feedback_and_retains_uncertain_claim(self):
        async def hangs(uid):
            await asyncio.Event().wait()
        self.send_callback = hangs
        with patch.object(campaign, 'CAMPAIGN_TIMEOUT_SECONDS', 0.2):
            result = await self.confirm(await self.preview())
        self.assertIn('could not be verified', result.followup.send.await_args.args[0])
        self.assertEqual(self.message_requests, [1])
        self.assertEqual(self.receipts()[1]['state'], 'pending')
        self.send_callback = None
        await self.confirm(await self.preview())
        self.assertEqual(self.message_requests, [1])

    async def test_tampered_manifest_or_draft_never_causes_a_send(self):
        view = await self.preview()
        invalid = replace(view.draft, campaign='different-campaign')
        result = await self.ns['_send_owner_broadcast'](invalid)
        self.assertTrue(result.unavailable)
        self.assertEqual(self.requests, [])
        campaign.claim_campaign(self.db, view.draft)
        with self.db() as conn:
            conn.execute('UPDATE gbop_shift_deliveries SET discord_message_id=? WHERE user_id=0',
                         (json.dumps({'owner_id': 123, 'version': 1, 'count': 3}),))
        await self.confirm(view)
        self.assertEqual(self.requests, [])

    async def test_wrong_dm_recipient_blocks_message_request(self):
        self.wrong_channel = True
        await self.confirm(await self.preview())
        self.assertEqual(self.message_requests, [])
        self.assertEqual(self.receipts()[1]['state'], 'uncertain')

    async def test_followup_failure_cannot_repeat_confirmed_campaign(self):
        view = await self.preview()
        interaction = guided.SyntheticInteraction()
        interaction.followup.send.side_effect = TimeoutError('synthetic owner response loss')
        with self.assertRaises(TimeoutError):
            await view.confirm_send.callback(interaction)
        await self.confirm(view)
        await self.confirm(await self.preview())
        self.assertEqual(self.message_requests, [1, 2, 3])


class SingleAttemptHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_5xx_redirect_timeout_and_rate_limit_each_make_one_message_post(self):
        for outcome in (500, 502, 504, 524, 429, 302, 'timeout'):
            with self.subTest(outcome=outcome):
                requests = []
                async def handle(request):
                    requests.append(request.url.path)
                    if request.url.path.endswith('/users/@me'):
                        return httpx.Response(200, json={'id': '900', 'bot': True})
                    if request.url.path.endswith('/users/@me/channels'):
                        return httpx.Response(200, json={'id': '1001', 'type': 1, 'recipients': [{'id': '1'}]})
                    if outcome == 'timeout':
                        raise httpx.ReadTimeout('synthetic')
                    return httpx.Response(outcome, json={'retry_after': 60}, headers={'location': 'https://example.invalid/'})
                sender = campaign.SingleAttemptDM('synthetic', 900, authorize=AsyncMock(return_value=True),
                                                 transport=httpx.MockTransport(handle))
                try:
                    await sender.verify_identity()
                    result = await sender.send_once(1, campaign.MESSAGE)
                finally:
                    await sender.close()
                self.assertEqual(len(requests), 3)
                self.assertEqual(requests.count('/api/v10/channels/1001/messages'), 1)
                self.assertEqual(result.state, 'rate_limited' if outcome == 429 else 'uncertain')


if __name__ == '__main__':
    unittest.main()
