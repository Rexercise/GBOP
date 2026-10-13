"""Persistent SS selection, authenticated updates, and report filtering."""
from datetime import datetime
import json
import unittest
from unittest.mock import patch

from gbop_voice_web import ss_preferences as pref
from gbop_voice_web import member_intelligence as intel
from gbop_voice_web import midpoint_preferences as midpoint
from gbop_voice_web import weekly_structure as ss
from gbop_voice_web.market_conversation import MarketConversation
from gbop_voice_web.voice_runtime import READ_ONLY_RECOVERY_NAMES, RECOVERY_NAMES
from tests import test_midpoint_preferences as fixture


class SSSymbolTests(unittest.TestCase):
    db = fixture.PreferenceTests.db
    now = int(datetime.fromisoformat('2026-10-13T08:55:00-04:00').timestamp())

    def setUp(self):
        fixture.PreferenceTests.setUp(self)
        with self.db() as conn:
            conn.executescript('''
                CREATE TABLE gbop_ss_reports(asset TEXT,week_start TEXT,report_version TEXT,
                    revision INTEGER,generated_at TEXT,payload TEXT);
                CREATE TABLE gbop_ss_contributions(guild_id INT,user_id INT,asset TEXT,
                    week_start TEXT,report_version TEXT,revision INTEGER,answers TEXT,created_at TEXT);
            ''')
        for asset in ('NAS100', 'XAUUSD', 'XAGUSD'):
            self.store_report(asset)

    def store_report(self, asset):
        report = dict(ok=True, asset=asset, week_start='2026-10-05', report_version='v1',
            report_revision=1, generated_at_utc='2026-10-10T22:00:00Z',
            window=ss.week_window('2026-10-05'),
            observed_ohlc=dict(open=100,high=110,low=90,close=105,body_direction='bullish'),
            coverage=dict(status='unverified_intervals',source_timeframe='M5',unobserved_interval_count=2,
                          nominal_final_bar_present=True,full_broker_week_verified=False),
            closure_vs_previous=dict(relationship='above'))
        with self.db() as conn:
            conn.execute('INSERT INTO gbop_ss_reports VALUES (?,?,?,?,?,?)',
                (asset,'2026-10-05','v1',1,report['generated_at_utc'],json.dumps(report)))

    def update(self, operation='add', symbols=None, *, guild=10, user=20, **extra):
        return intel.intelligence_tool(self.db,guild,user,'update_ss_symbols',
            dict(operation=operation,symbols=['gold'] if symbols is None else symbols,confirmed=True,**extra))

    def read(self, guild=10, user=20):
        return intel.intelligence_tool(self.db,guild,user,'get_ss_symbols',{})['profile']

    def study(self, **args):
        with patch.object(ss.clock,'time',return_value=self.now):
            return intel.intelligence_tool(self.db,10,20,'get_weekly_structure_study',args)

    def test_default_is_nas_only_without_writing_settings(self):
        self.assertEqual(self.read()['symbols'],['NAS100'])
        self.assertFalse(self.read()['configured'])
        self.assertEqual(self.study()['asset'],'NAS100')
        state = ss.pre_shift_structure(self.db,10,20,now=self.now)
        self.assertEqual([r['asset'] for r in state['reports']],['NAS100'])
        message = ss.format_pre_shift_structure(state)
        self.assertIn('NAS100',message)
        self.assertNotIn('XAUUSD',message)
        self.assertNotIn('XAGUSD',message)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM gbop_watch_runtime WHERE id LIKE 'ss_symbols_v1:%'").fetchone()[0],0)

    def test_add_remove_aliases_persist_across_connections(self):
        self.assertEqual(self.update(symbols=['Gold','gold','silver'])['profile']['symbols'],['NAS100','XAUUSD','XAGUSD'])
        self.assertEqual(self.update('remove',['Nas','gold'])['profile']['symbols'],['XAGUSD'])
        self.assertEqual(self.read()['symbols'],['XAGUSD'])
        self.assertEqual(self.study()['asset'],'XAGUSD')
        # A fresh independent connection must see the saved value.
        with self.db() as conn:
            self.assertEqual(pref.read_selection(conn,10,20)['symbols'],['XAGUSD'])

    def test_add_remove_are_idempotent_and_preserve_other_symbols(self):
        for _ in range(2): self.update(symbols=['gold'])
        self.update(symbols=['silver'])
        for _ in range(2): self.update('remove',['gold'])
        self.assertEqual(self.read()['symbols'],['NAS100','XAGUSD'])

    def test_replace_reset_and_explicit_empty_are_distinct(self):
        self.update('replace',['gold'])
        self.assertEqual(self.study()['asset'],'XAUUSD')
        self.update('remove',['gold'])
        self.assertEqual(self.read()['symbols'],[])
        self.assertEqual(self.study()['assets'],[])
        state = ss.pre_shift_structure(self.db,10,20,now=self.now)
        self.assertEqual(state['reports'],[])
        self.assertIn('No SS symbols selected',ss.format_pre_shift_structure(state))
        self.assertEqual(self.update('reset',[])['profile']['symbols'],['NAS100'])
        self.update('replace',[])
        self.assertEqual(self.read()['symbols'],[])

    def test_explicit_one_off_asset_does_not_change_selection(self):
        self.assertEqual(self.study(asset='gold')['asset'],'XAUUSD')
        self.assertEqual(self.read()['symbols'],['NAS100'])
        self.assertEqual(self.study()['asset'],'NAS100')

    def test_multiple_selected_reports_exclude_unsolicited_symbols(self):
        self.update(symbols=['gold'])
        result = self.study()
        self.assertEqual([r['asset'] for r in result['assets']],['NAS100','XAUUSD'])
        state = ss.pre_shift_structure(self.db,10,20,now=self.now)
        self.assertEqual([r['asset'] for r in state['reports']],['NAS100','XAUUSD'])
        self.assertNotIn('XAGUSD',json.dumps(state))

    def test_all_symbols_is_explicit_one_off_and_does_not_save(self):
        self.assertEqual(len(self.study(all_symbols=True)['assets']),9)
        self.assertEqual(self.read()['symbols'],['NAS100'])
        for invalid in (1,'true',{},[]):
            self.assertFalse(self.study(all_symbols=invalid)['ok'])
        self.assertFalse(self.study(asset='gold',all_symbols=True)['ok'])

    def test_missing_selected_report_never_substitutes_another_symbol(self):
        self.update('replace',['BTC'])
        result = self.study()
        self.assertFalse(result['ok'])
        self.assertEqual(result['asset'],'BTCUSD')
        state = ss.pre_shift_structure(self.db,10,20,now=self.now)
        self.assertEqual(state['reports'],[])
        self.assertEqual(state['unavailable_assets'],['BTCUSD'])
        self.assertNotIn('NAS100',json.dumps(state))

    def test_member_and_guild_preferences_are_isolated(self):
        self.update('replace',['gold'])
        self.update('replace',['silver'],user=21)
        self.assertEqual(self.read()['symbols'],['XAUUSD'])
        self.assertEqual(self.read(user=21)['symbols'],['XAGUSD'])
        self.assertEqual(self.read(guild=11)['symbols'],['NAS100'])
        self.assertFalse(self.update(symbols=['gold'],user_id=21)['ok'])

    def test_revoked_inactive_and_unknown_members_cannot_change_selection(self):
        for user in (22,23,24):
            self.assertFalse(self.update(user=user)['ok'])
            self.assertFalse(intel.intelligence_tool(self.db,10,user,'get_ss_symbols',{})['ok'])
        self.update()
        with self.db() as conn:
            conn.execute('UPDATE members SET revoked=1 WHERE guild_id=10 AND user_id=20')
        self.assertFalse(self.update('remove',['NAS'])['ok'])
        with self.db() as conn:
            self.assertEqual(pref.read_selection(conn,10,20)['symbols'],['NAS100','XAUUSD'])

    def test_invalid_or_unconfirmed_changes_do_not_modify_state(self):
        for args in ({'operation':'add','symbols':['INVALID'],'confirmed':True},
                     {'operation':'replace','symbols':['gold'],'confirmed':False},
                     {'operation':'replace','symbols':'gold','confirmed':True},
                     {'operation':'reset','symbols':['gold'],'confirmed':True}):
            self.assertFalse(intel.intelligence_tool(self.db,10,20,'update_ss_symbols',args)['ok'])
        self.assertFalse(self.read()['configured'])

    def test_cancellation_or_identity_change_blocks_queued_updates(self):
        for change in ('cancel','member','session','close'):
            context=MarketConversation((10,20,'test'))
            generation=context.begin_turn('Add gold to my SS report')
            args=midpoint.bind_preference_args(context,'update_ss_symbols',
                 {'operation':'add','symbols':['gold'],'confirmed':True,'_ss_binding':'forged'},generation)
            if change=='cancel': context.begin_turn('cancel')
            elif change=='member': context.owner=(10,21,'test')
            elif change=='session': context.session_id='changed'
            else: context.close()
            self.assertFalse(intel.intelligence_tool(self.db,10,20,'update_ss_symbols',args)['ok'])
        self.assertFalse(self.read()['configured'])

    def test_other_preferences_and_market_runtime_are_untouched(self):
        midpoint.save_preference(self.db,10,20,99,{'midpoint_label':'CE','confirmed':True})
        with self.db() as conn:
            before=[tuple(r) for r in conn.execute('SELECT * FROM gbop_watch_runtime ORDER BY id')]
        self.update()
        with self.db() as conn:
            after=[tuple(r) for r in conn.execute("SELECT * FROM gbop_watch_runtime WHERE id NOT LIKE 'ss_symbols_v1:%' ORDER BY id")]
        self.assertEqual(before,after)

    def test_corrupt_selection_fails_instead_of_resetting_to_nas(self):
        self.update()
        with self.db() as conn:
            conn.execute('UPDATE gbop_watch_runtime SET state=? WHERE id=?',
                         ('{"version":1,"symbols":["INVALID"]}',pref.PREFIX+'10:20'))
        self.assertFalse(intel.intelligence_tool(self.db,10,20,'get_ss_symbols',{})['ok'])
        self.assertFalse(self.update()['ok'])

    def test_text_and_browser_dispatchers_share_the_same_selection(self):
        def dispatch(path,name,owner_key):
            return fixture.function(path,name,dict(db=self.db,GTOP_GUILD_ID=10,
                **{owner_key:99},member_access_error=fixture.member_access_error,
                WATCH_NAMES=set(),MARKET_NAMES=set(),TRADE_ASSIST_NAMES=set(),
                INTELLIGENCE_NAMES=intel.INTELLIGENCE_NAMES,intelligence_tool=intel.intelligence_tool))
        browser=dispatch('gbop_voice_web/server.py','run_tool','OWNER_USER_ID')
        discord=dispatch('bot.py','ai_execute_tool','GTOP_OWNER_USER_ID')
        self.assertTrue(discord(20,'update_ss_symbols',{'operation':'add','symbols':['gold'],'confirmed':True})['ok'])
        self.assertEqual(browser(20,'get_ss_symbols',{})['profile']['symbols'],['NAS100','XAUUSD'])
        self.assertTrue(browser(20,'update_ss_symbols',{'operation':'remove','symbols':['NAS'],'confirmed':True})['ok'])
        self.assertEqual(discord(20,'get_ss_symbols',{})['profile']['symbols'],['XAUUSD'])

    def test_tools_are_shared_and_recovery_cannot_mutate_preferences(self):
        self.assertTrue(pref.NAMES <= intel.INTELLIGENCE_NAMES)
        self.assertIn('get_ss_symbols',READ_ONLY_RECOVERY_NAMES)
        self.assertNotIn('update_ss_symbols',RECOVERY_NAMES)
        for tool in pref.TOOLS:
            self.assertFalse(tool['parameters']['additionalProperties'])
            self.assertNotIn('user_id',tool['parameters']['properties'])
            self.assertNotIn('_ss_binding',tool['parameters']['properties'])


if __name__ == '__main__':
    unittest.main()
