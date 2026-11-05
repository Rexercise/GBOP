"""Financial acceptance and persistent narrative integration using isolated members."""
import json
from copy import deepcopy
from decimal import Decimal
import unittest
import test_contextual_journals as fixture
from gbop_voice_web import journal_coach as coach, journal_accounting as c
from gbop_voice_web.market_conversation import MarketConversation

T=lambda h:f'2026-11-04T{h}:00+00:00'
def fact(n,source='member_confirmed'):
    return dict(value=str(n) if n is not None else None,source=source,quote=f'{n} dollars' if n is not None else 'unknown')
def event(key,n,at,basis='net',fees=None):
    return dict(event_key=key,amount=fact(n),at=at,basis=basis,fees=fees)
def trade(key,opened,closed,pnl,risk='0.5'):
    return dict(id=key,accounting=dict(account_id='a'*32,currency='USD',opened_at=opened,
       position_status='closed',risk_pct=fact(risk),realizations=[event('close',pnl,closed)],realized_complete=True))
def account():
    return dict(id='a'*32,name='Personal',currency='USD',balance=fact('10000'),balance_at=T('08:00'),cash_movements=[])

class ArithmeticTests(unittest.TestCase):
    def test_compounding_original_r_and_return_are_different(self):
        result=c.project(account(),[trade(1,T('09:00'),T('10:00'),'150'),trade(2,T('11:00'),T('12:00'),'-50.75')])
        self.assertEqual([t['initial_risk'] for t in result['trades']],['50','50.75'])
        self.assertEqual([t['calculated_r'] for t in result['trades']],['3','-1'])
        self.assertEqual(result['calculated_r_total'],'2')
        self.assertEqual(result['closing_balance'],'10099.25')
        self.assertEqual(result['return_pct'],'0.9925')
        self.assertEqual(result['completed_trade_count'],2)
    def test_backdated_and_overlapping_risk_basis(self):
        first=trade(1,T('09:00'),T('10:00'),'150')
        overlap=trade(2,T('09:30'),T('11:00'),'-50')
        later=trade(3,T('12:00'),T('13:00'),'50.50')
        result=c.project(account(),[later,overlap,first])
        byid={t['journal_id']:t for t in result['trades']}
        self.assertEqual(byid[2]['initial_risk'],'50')
        self.assertEqual(byid[3]['initial_risk'],'50.5')
        # An explicitly confirmed opening snapshot is never rewritten.
        later['accounting']['opening_balance']=fact('12000')
        self.assertEqual(c.project(account(),[first,later])['trades'][1]['initial_risk'],'60')
    def test_unknown_r_and_reported_only_are_separate(self):
        t=trade(1,T('09:00'),T('10:00'),'150');t['accounting'].pop('risk_pct');t['reported_r']='2'
        r=c.project(account(),[t]);self.assertIsNone(r['calculated_r_total']);self.assertEqual(r['reported_r_total'],'2')
        self.assertEqual(r['r_coverage'],dict(calculated=0,reported_only=1,unknown=0,total=1))
        t['reported_r']=None
        self.assertEqual(c.project(account(),[t])['r_coverage']['unknown'],1)
    def test_partial_runner_extra_risk_uses_original_risk(self):
        t=trade(1,T('09:00'),T('10:00'),'100');d=t['accounting'];d['position_status']='partially_closed'
        d['risk_changes']=[dict(event_key='add',at=T('09:30'),amount=fact('25'),kind='added_risk',note='second entry')]
        r=c.project(account(),[t]);self.assertEqual(r['completed_trade_count'],0);self.assertEqual(r['open_trade_count'],1)
        self.assertEqual(r['closing_balance'],'10100');self.assertEqual(r['trades'][0]['calculated_r'],'2')
        d['position_status']='closed';d['realizations'].append(event('runner','50',T('11:00')))
        self.assertEqual(c.project(account(),[t])['trades'][0]['calculated_r'],'3')
    def test_fees_and_cashflows_do_not_inflate_profit(self):
        a=account();a['cash_movements']=[dict(event_key='deposit',at=T('10:30'),amount=fact('1000'),kind='deposit')]
        first=trade(1,T('09:00'),T('10:00'),'155');first['accounting']['realizations'][0].update(basis='gross',fees=fact('5'))
        second=trade(2,T('11:00'),T('12:00'),'-55.75');second['accounting']['realizations'][0]['fees']=fact('5')
        r=c.project(a,[first,second]);self.assertEqual(r['net_trading_pnl'],'94.25');self.assertEqual(r['cash_movements'],'1000')
        self.assertEqual(r['closing_balance'],'11094.25');self.assertEqual(r['return_pct'],'0.9925')
        self.assertEqual(r['trades'][1]['calculated_r'],'-1')
    def test_unknown_fees_estimates_and_untimed_cash_break_exact_balance(self):
        for change in ('fees','estimate','time','incomplete'):
            with self.subTest(change=change):
                t=trade(1,T('09:00'),T('10:00'),'150')
                e=t['accounting']['realizations'][0]
                if change=='fees':e.update(basis='gross',fees=None)
                if change=='estimate':e['amount']['source']='estimate'
                if change=='time':e['at']=None
                if change=='incomplete':t['accounting']['realized_complete']=False
                r=c.project(account(),[t,trade(2,T('11:00'),T('12:00'),'30')])
                self.assertFalse(r['balance_complete']);self.assertIsNone(r['closing_balance']);self.assertEqual(r['curve'],[])
                self.assertIsNone(r['trades'][1]['initial_risk'])
    def test_invalid_nonfinite_zero_and_half_size(self):
        for value in ('0','-1','NaN','Infinity',True):
            with self.subTest(value=value),self.assertRaises(ValueError):c.validate({'initial_risk':fact(value)})
        with self.assertRaises(ValueError):c.validate({'usual_risk':'half_size'})
        with self.assertRaises(ValueError):c.validate({'initial_risk':fact('50','imported_verified')})
    def test_zero_balance_and_bad_period_do_not_fabricate_returns(self):
        a=account();a['balance']=fact('0')
        r=c.project(a,[]);self.assertEqual(r['closing_balance'],'0');self.assertTrue(r['balance_complete']);self.assertIsNone(r['return_pct'])
        with self.assertRaises(ValueError):c.project(account(),[],start_at=T('12:00'),end_at=T('09:00'))
        with self.assertRaises(ValueError):c.project(account(),[],start_at=T('07:00'))
    def test_incomplete_cash_has_no_exact_total(self):
        t=trade(1,T('09:00'),T('10:00'),'150');t['accounting']['realized_complete']=False
        r=c.project(account(),[t]);self.assertIsNone(r['net_trading_pnl']);self.assertEqual(r['known_net_trading_pnl_subtotal'],'150')
        self.assertIsNone(r['trades'][0]['net_realized']);self.assertIsNone(r['calculated_r_total'])
    def test_other_accounts_do_not_mix(self):
        t=trade(1,T('09:00'),T('10:00'),'150');t['accounting']['account_id']='b'*32
        r=c.project(account(),[t]);self.assertEqual(r['closing_balance'],'10000');self.assertEqual(r['trades'],[])

class AccountJournalTests(unittest.TestCase):
    setUp=fixture.ContextualJournalTests.setUp
    tearDown=fixture.ContextualJournalTests.tearDown
    review=fixture.ContextualJournalTests.review
    def call(self,name,args,text,context=None,user=20):
        context=context or self.context;context.begin_turn(text)
        return context.run(name,args,lambda n,a:coach.coach_tool(self.db,10,user,n,a))
    def make_account(self,name='Personal',currency='USD'):
        data=dict(name=name,currency=currency,balance=fact('10000'),balance_at=T('08:00'),usual_risk_pct=fact('0.5'))
        result=self.call('update_journal_account',dict(action='create',data_json=json.dumps(data)),f'Save my {name} account. 10000 dollars. My usual risk is 0.5 dollars percent.')
        self.assertTrue(result['ok'],result);return result['account']
    def stage(self,account_id):
        financial=trade(1,T('09:00'),T('10:00'),'150')['accounting'];financial['account_id']=account_id
        data=dict(trade_date='2026-11-04',trades=[dict(trade_key='morning',story=dict(asset='NAS100',play='9ate8',direction='Bullish',accounting=financial,
            entries=[dict(entry_index=1,entry_model='Super Soup',status='closed')]))])
        result=self.call('stage_journal_day',dict(day_json=json.dumps(data)),'Journal my NAS 9ate8 Super Soup. Risk 0.5 dollars percent, net 150 dollars.')
        self.assertTrue(result['ok'],result);return result
    def test_persist_save_reload_recall_correct_without_duplicates(self):
        a=self.make_account();draft=self.stage(a['id'])
        r=self.call('save_journal_day',dict(draft_id=draft['draft_id']),'Save it.');self.assertTrue(r['ok'],r)
        fresh=MarketConversation((10,20,'fresh'),auth_provider=(self.db,10,20))
        r=self.call('get_account_performance',dict(account_id=a['id']),'How did I do?',context=fresh)
        self.assertEqual(r['closing_balance'],'10150');self.assertEqual(r['calculated_r_total'],'3')
        correction={'trades':[{'trade_key':'morning','story':{'accounting':{'realizations':[event('close','100',T('10:00'))]}}}]}
        r=self.call('stage_journal_day',dict(draft_id=draft['draft_id'],day_json=json.dumps(correction)),'Correct the morning one: net 100 dollars.')
        self.assertTrue(r['ok'],r)
        saved=self.call('save_journal_day',dict(draft_id=draft['draft_id']),'Save it.');self.assertTrue(saved['ok'],saved)
        again=self.call('save_journal_day',dict(draft_id=draft['draft_id']),'Save it again.');self.assertTrue(again['ok'],again)
        r=self.call('get_account_performance',dict(account_id=a['id']),'Read my account.',context=fresh)
        self.assertEqual(r['closing_balance'],'10100');self.assertEqual(r['calculated_r_total'],'2')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0],1)
        day=json.loads(self.conn.execute("SELECT payload FROM journal_story_drafts WHERE id=?",(draft['draft_id'],)).fetchone()[0])
        self.assertTrue(day['day_trades']['morning']['corrections'])
    def test_ownership_revocation_currency_and_stale_update(self):
        a=self.make_account();other=MarketConversation((10,30,'other'),auth_provider=(self.db,10,30))
        r=self.call('get_account_performance',dict(account_id=a['id']),'Read.',context=other,user=30)
        self.assertFalse(r['ok']);self.assertNotIn('10000',str(r))
        update=dict(action='update',account_id=a['id'],expected_revision=1,data_json=json.dumps({'usual_risk_pct':fact('1')}))
        self.assertTrue(self.call('update_journal_account',update,'Make my risk 1 dollars percent.')['ok'])
        update['data_json']=json.dumps({'usual_risk_pct':fact('2')})
        self.assertFalse(self.call('update_journal_account',update,'Make my risk 2 dollars percent.')['ok'])
        self.conn.execute('UPDATE members SET revoked=1 WHERE user_id=20');self.conn.commit()
        self.assertFalse(self.call('get_journal_accounts',{},'Read.')['ok'])
    def test_create_and_cashflow_retry_are_idempotent(self):
        a=self.make_account();again=self.make_account();self.assertEqual(a['id'],again['id'])
        data=dict(event_key='deposit-nov4',kind='deposit',at=T('10:00'),amount=fact('500'))
        args=dict(action='cash_movement',account_id=a['id'],expected_revision=1,data_json=json.dumps(data))
        r=self.call('update_journal_account',args,'I deposited 500 dollars.');self.assertTrue(r['ok'],r)
        r=self.call('update_journal_account',args,'I deposited 500 dollars.');self.assertTrue(r['replayed'])
        r=self.call('get_account_performance',dict(account_id=a['id']),'Read.')
        self.assertEqual(r['closing_balance'],'10500');self.assertEqual(r['net_trading_pnl'],'0');self.assertEqual(r['return_pct'],'0')
    def test_default_not_applied_without_invocation_and_half_normal_snapshot(self):
        a=self.make_account()
        data=dict(asset='NAS100',play='Young Lefty',accounting=dict(account_id=a['id'],opened_at=T('09:00'),position_status='open'))
        r=self.call('stage_journal_story',dict(story_json=json.dumps(data)),'Journal my Young Lefty, half size.')
        self.assertTrue(r['ok'],r);values=self.context._journal_story['values'];self.assertNotIn('risk_pct',values['accounting'])
        r=self.call('stage_journal_story',dict(story_json=json.dumps({'accounting':{'usual_risk':'half_normal'}})),'I used half my normal risk.')
        self.assertTrue(r['ok'],r);self.assertEqual(self.context._journal_story['values']['accounting']['risk_pct']['value'],'0.25')
        self.make_account('Second','EUR')
        self.assertEqual(self.context._journal_story['values']['accounting']['account_id'],a['id'])
    def test_unresolved_numeric_correction_survives_unrelated_turn(self):
        self.make_account()
        r=self.call('stage_journal_story',dict(story_json=json.dumps({'accounting':{'initial_risk':fact('999')}})),'Journal my 9ate8; I have not stated risk.')
        self.assertIn('Account facts',r['validation_issue'])
        r=self.call('stage_journal_story',dict(story_json='{"context_notes":"I felt calm."}'),'I felt calm.')
        self.assertIn('Account facts',r['validation_issue'])
        r=self.call('stage_journal_story',dict(story_json=json.dumps({'accounting':{'initial_risk':fact(None,'unknown')}})),'Leave risk unknown.')
        self.assertIsNone(r['validation_issue'])
        self.assertTrue(self.call('save_journal_story',dict(draft_id=r['draft_id']),'Save my journal.')['ok'])
    def test_spoken_numbers_and_unknown_preset(self):
        from gbop_voice_web.journal_accounts import _ground
        _ground(dict(value='10000',source='member_confirmed',quote='ten thousand dollars'),'My balance is ten thousand dollars.')
        _ground(dict(value='0.5',source='member_confirmed',quote='half a percent'),'I risked half a percent.')
        a=self.make_account()
        r=self.call('update_journal_account',dict(action='update',account_id=a['id'],expected_revision=1,data_json=json.dumps({'usual_risk_pct':fact(None,'unknown')})),'Leave my usual risk unknown.')
        self.assertTrue(r['ok'],r)
    def test_voice_tools_expose_account_schema_and_safe_recovery(self):
        from gbop_voice_web.journal_day import DAY_TOOLS
        from gbop_voice_web.journal_story import STORY_TOOLS
        from gbop_voice_web.voice_runtime import READ_ONLY_RECOVERY_NAMES,JOURNAL_WRITE_NAMES
        for tool,field in [(DAY_TOOLS[0],'day_json'),(STORY_TOOLS[0],'story_json')]:
            schema=tool['parameters']['properties'][field]['description']
            for term in ('realized_complete','event_key','half size','unknown','initial_risk'):self.assertIn(term,schema)
        self.assertIn('get_account_performance',READ_ONLY_RECOVERY_NAMES)
        self.assertIn('get_journal_accounts',READ_ONLY_RECOVERY_NAMES)
        self.assertIn('update_journal_account',JOURNAL_WRITE_NAMES)
        self.assertNotIn('update_journal_account',READ_ONLY_RECOVERY_NAMES)
    def test_fabricated_numbers_are_not_persisted(self):
        self.make_account()
        r=self.call('stage_journal_story',dict(story_json=json.dumps({'accounting':{'initial_risk':fact('999')}})),'Journal my 9ate8. I did not state risk.')
        self.assertTrue(r['persisted']);self.assertIn('Account facts',r['validation_issue']);self.assertNotIn('999',self.conn.execute('SELECT payload FROM journal_story_drafts').fetchone()[0] if self.conn.execute('SELECT COUNT(*) FROM journal_story_drafts').fetchone()[0] else '')

if __name__=='__main__':unittest.main()
