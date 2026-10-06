"""Production Discord handlers, synthetic storage and authenticated button stubs."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import time
import unittest

from gbop_voice_web.deletion import delete_trade_records
from gbop_voice_web.trade_numbers import trade_record_id
import test_journal_delete as deletion_fixture

BOT = Path(__file__).resolve().parents[1] / 'bot.py'


class DeletionConfirmationUITests(unittest.TestCase):
    def test_discord_standalone_delete_cleans_derived_evidence(self):
        self.fixture.seed_coaching()
        self.conn.execute('UPDATE journals SET thesis_id=NULL WHERE id=1')
        preview = self.ns['get_journal_delete_preview'](20, 1)
        result = self.ns['delete_owned_journal'](20, 1, preview['fingerprint'])
        self.assertTrue(result['ok'])
        self.assertIsNone(self.conn.execute("SELECT 1 FROM gbop_coaching_observations WHERE guild_id=10 AND user_id=20 AND source_key='journal:1'").fetchone())
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM gbop_coaching_observations WHERE guild_id=10 AND user_id=20 AND source_key='risk:1'").fetchone())

    def test_discord_trade_delete_reports_derived_cleanup(self):
        self.fixture.seed_coaching()
        preview = self.ns['get_trade_delete_preview'](20, 41)
        self.assertEqual(preview['coaching_observations'], 2)
        view = self.ns['DeleteTradeView'](20, preview)
        asyncio.run(view.confirm_delete(self.interaction, None))
        self.assertIn('2 derived coaching observation(s)', self.interaction.response.edit_message.call_args.kwargs['content'])

    def setUp(self):
        self.fixture = deletion_fixture.JournalDeleteTests()
        self.fixture.setUp()
        self.conn = self.fixture.conn
        self.db = self.fixture.ns['db']
        class View:
            def __init__(self, timeout):
                self.timeout = timeout
            def stop(self):
                pass
        discord = NS(Interaction=object, ButtonStyle=NS(danger=1, secondary=2),
            ui=NS(View=View, Button=object, button=lambda **kw: lambda fn: fn))
        names = {'select_member_journal', 'get_journal_delete_preview', 'delete_owned_journal',
            'get_trade_delete_preview', 'permanently_delete_trade', 'DeleteJournalView', 'DeleteTradeView',
            'ai_delete_journal', 'ai_delete_trade', 'editjournal', 'deletejournal', 'deletetrade'}
        nodes = [node for node in ast.parse(BOT.read_text()).body if isinstance(node,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in names]
        for node in nodes:
            node.decorator_list = []
        self.ns = dict(discord=discord, db=self.db, GTOP_GUILD_ID=10, time=time,
            delete_trade_records=delete_trade_records, require_member=AsyncMock(return_value=True),
            trade_id_from_number=lambda user, number: trade_record_id(self.db, 10, user, number),
            EditJournalModal=lambda row: dict(row),
            get_member_journal=lambda user, rid: self.conn.execute(
                'SELECT * FROM journals WHERE id=? AND guild_id=10 AND user_id=?', (rid, user)).fetchone())
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(BOT), 'exec'), self.ns)
        self.interaction = NS(user=NS(id=20), response=NS(send_message=AsyncMock(),
            edit_message=AsyncMock(), send_modal=AsyncMock()))

    def tearDown(self):
        self.fixture.tearDown()

    def test_trade_button_keeps_raw_identity_after_an_earlier_trade_disappears(self):
        self.conn.execute('INSERT INTO theses(id,guild_id,user_id) VALUES(40,10,20),(43,10,20)')
        preview = self.ns['get_trade_delete_preview'](20, 41)
        self.assertEqual(preview['trade_id'], 2)
        view = self.ns['DeleteTradeView'](20, preview)
        with self.db() as conn:
            delete_trade_records(conn, 10, 20, 40)
        asyncio.run(view.confirm_delete(self.interaction, None))
        self.assertIsNone(self.conn.execute('SELECT id FROM theses WHERE id=41').fetchone())
        self.assertIsNotNone(self.conn.execute('SELECT id FROM theses WHERE id=43').fetchone())

    def test_both_buttons_refuse_new_linked_records(self):
        for kind in ('Trade', 'Journal'):
            preview = self.ns['get_' + kind.lower() + '_delete_preview'](20, 41 if kind == 'Trade' else 1)
            view = self.ns['Delete' + kind + 'View'](20, preview)
            self.conn.execute('INSERT INTO thesis_executions(thesis_id,guild_id,user_id) VALUES(41,10,20)')
            asyncio.run(view.confirm_delete(self.interaction, None))
            self.assertIn('changed', self.interaction.response.edit_message.call_args.kwargs['content'])
            self.assertIsNotNone(self.conn.execute('SELECT id FROM theses WHERE id=41').fetchone())

    def test_both_buttons_expire_and_verify_owner(self):
        for kind in ('Trade', 'Journal'):
            preview = self.ns['get_' + kind.lower() + '_delete_preview'](20, 41 if kind == 'Trade' else 1)
            view = self.ns['Delete' + kind + 'View'](20, preview)
            stranger = NS(user=NS(id=30), response=NS(send_message=AsyncMock()))
            self.assertFalse(asyncio.run(view.interaction_check(stranger)))
            view.expires_at = 0
            asyncio.run(view.confirm_delete(self.interaction, None))
            self.assertIn('expired', self.interaction.response.edit_message.call_args.kwargs['content'])
            self.assertIsNotNone(self.conn.execute('SELECT id FROM theses WHERE id=41').fetchone())

    def test_button_expiry_while_waiting_on_member_lock_is_rechecked(self):
        for kind in ('Trade', 'Journal'):
            self.fixture.before_execute = None
            preview = self.ns['get_' + kind.lower() + '_delete_preview'](20, 41 if kind == 'Trade' else 1)
            current = [0]
            self.ns['time'] = NS(monotonic=lambda: current[0])
            view = self.ns['Delete' + kind + 'View'](20, preview)
            def hook(sql, params):
                if 'pg_advisory_xact_lock' in sql:
                    current[0] = 61
            self.fixture.before_execute = hook
            asyncio.run(view.confirm_delete(self.interaction, None))
            self.assertIn('expired', self.interaction.response.edit_message.call_args.kwargs['content'])
            self.assertIsNotNone(self.conn.execute('SELECT id FROM theses WHERE id=41').fetchone())

    def test_member_revocation_rechecked_inside_delete(self):
        preview = self.ns['get_journal_delete_preview'](20, 1)
        self.conn.execute('UPDATE members SET revoked=1 WHERE user_id=20')
        view = self.ns['DeleteJournalView'](20, preview)
        asyncio.run(view.confirm_delete(self.interaction, None))
        self.assertIn('revoked', self.interaction.response.edit_message.call_args.kwargs['content'])
        self.assertIsNotNone(self.conn.execute('SELECT id FROM theses WHERE id=41').fetchone())

    def test_helpers_cannot_delete_without_a_matching_snapshot(self):
        self.assertFalse(self.ns['delete_owned_journal'](20, 1)['ok'])
        self.assertFalse(self.ns['permanently_delete_trade'](20, 41)['ok'])
        self.assertIsNotNone(self.conn.execute('SELECT id FROM theses WHERE id=41').fetchone())

    def test_ai_confirm_returns_click_workflow_and_does_not_delete(self):
        for name, args in [('ai_delete_journal', {'journal_id': 1}), ('ai_delete_trade', {'trade_id': 1})]:
            for confirm in (False, True):
                result = self.ns[name](20, {**args, 'confirm': confirm})
                self.assertTrue(result['requires_click_confirmation'])
                self.assertNotIn('deleted', result)
                self.assertNotIn('fingerprint', result['preview'])
                self.assertTrue(result['command'].startswith('/delete'))
                self.assertIsNotNone(self.conn.execute('SELECT id FROM theses WHERE id=41').fetchone())

    def test_slash_selectors_preserve_ambiguous_journal_error(self):
        self.conn.execute("INSERT INTO journals(id,guild_id,user_id,description) VALUES(0,10,20,'Old standalone')")
        for command in ('editjournal', 'deletejournal'):
            asyncio.run(self.ns[command](self.interaction, journal_id=1))
            error = self.interaction.response.send_message.call_args.args[0]
            self.assertIn('old history or a different Trade #', error)
        trade = self.ns['select_member_journal'](20, trade_number=1)
        legacy = self.ns['select_member_journal'](20, legacy_journal_number=1)
        self.assertEqual(trade['record_id'], 1)
        self.assertEqual(legacy['record_id'], 0)
        self.assertFalse(self.ns['select_member_journal'](20, journal_id=1, legacy_journal_number=1)['ok'])

    def test_slash_explicit_trade_selector_shows_existing_journal(self):
        asyncio.run(self.ns['editjournal'](self.interaction, trade_number=1))
        self.assertEqual(self.interaction.response.send_modal.call_args.args[0]['id'], 1)
        asyncio.run(self.ns['deletejournal'](self.interaction, trade_number=1))
        view = self.interaction.response.send_message.call_args.kwargs['view']
        self.assertEqual(view.journal_id, 1)
        self.assertIsNotNone(self.conn.execute('SELECT id FROM theses WHERE id=41').fetchone())

    def test_legacy_linked_history_is_not_silently_retargeted_for_edit(self):
        self.conn.execute("INSERT INTO journals(id,guild_id,user_id,description,thesis_id) VALUES(4,10,20,'Old history',41)")
        self.conn.execute("INSERT INTO thesis_events(thesis_id,guild_id,user_id,event,details) VALUES(41,10,20,'journal_canonical_v1','{\"journal_id\":1}')")
        asyncio.run(self.ns['editjournal'](self.interaction, legacy_journal_number=2))
        self.assertIn('retained legacy history', self.interaction.response.send_message.call_args.args[0])
        self.interaction.response.send_modal.assert_not_called()


if __name__ == '__main__':
    unittest.main()
