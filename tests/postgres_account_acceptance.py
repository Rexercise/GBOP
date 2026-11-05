"""Real account persistence tests in a disposable local PostgreSQL cluster only."""
import argparse
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from postgres_journal_acceptance import PostgresJournalTests, disposable_postgres
from test_journal_accounts import fact, trade, T, event

class PostgresAccountTests(PostgresJournalTests):
    def make_account(self,user=20,context=None):
        result=self.action('update_journal_account',{'action':'create','data_json':json.dumps(dict(name='Synthetic acceptance',currency='USD',balance=fact('10000'),balance_at=T('08:00'),usual_risk_pct=fact('0.5')))},'Save my Synthetic acceptance account with 10000 dollars and usual risk 0.5 dollars percent.',context,user)
        self.assertTrue(result['ok'],result)
        return result['account']

    def test_account_day_save_fresh_recall_correction_retry_and_isolation(self):
        a=self.make_account()
        rows=[]
        for key,opened,closed,pnl in [('morning','09:00','10:00','150'),('later','11:00','12:00','-50.75')]:
            data=trade(key,T(opened),T(closed),pnl)['accounting'];data['account_id']=a['id']
            rows.append(dict(trade_key=key,story=dict(asset='NAS100',play='9ate8' if key=='morning' else 'Young Lefty',accounting=data)))
        staged=self.action('stage_journal_day',{'day_json':json.dumps(dict(trade_date='2026-11-04',trades=rows))},'Journal my day: 9ate8 net 150 dollars, later Young Lefty net -50.75 dollars. Risk was 0.5 dollars percent on each.')
        self.assertTrue(staged['ok'],staged)
        saved=self.action('save_journal_day',{'draft_id':staged['draft_id']},'Save my day.')
        self.assertTrue(saved['ok'],saved)
        fresh=self.fresh()
        r=self.action('get_account_performance',{'account_id':a['id']},'Read my performance.',fresh)
        self.assertEqual((r['closing_balance'],r['calculated_r_total'],r['return_pct']),('10099.25','2','0.9925'))
        self.assertEqual(self.count('journals'),2)
        self.assertEqual(self.count('thesis_executions'),0)
        # A new connection reads the same account; a different member cannot.
        other=self.fresh(30)
        denied=self.action('get_account_performance',{'account_id':a['id']},'Read.',other,30)
        self.assertFalse(denied['ok']);self.assertNotIn('10099',str(denied))
        b=self.make_account(30,other);self.assertNotEqual(a['id'],b['id'])
        self.assertEqual(self.action('get_account_performance',{'account_id':b['id']},'Read.',other,30)['closing_balance'],'10000')
        change=dict(trades=[dict(trade_key='morning',story=dict(accounting=dict(realizations=[event('close','100',T('10:00'))])))])
        corrected=self.action('stage_journal_day',{'draft_id':staged['draft_id'],'day_json':json.dumps(change)},'Correct morning net to 100 dollars.',fresh)
        self.assertTrue(corrected['ok'],corrected)
        for _ in range(2):
            self.assertTrue(self.action('save_journal_day',{'draft_id':staged['draft_id']},'Save my corrected day.',fresh)['ok'])
        r=self.action('get_account_performance',{'account_id':a['id']},'Read my corrected performance.',self.fresh())
        self.assertEqual(r['closing_balance'],'10049.25');self.assertEqual(len(r['trades']),2)
        self.assertEqual([t['initial_risk'] for t in r['trades']],['50','50.5'])
        self.assertEqual(self.count('journals'),2)
        with self.db() as conn:
            payload=json.loads(conn.execute('SELECT payload FROM journal_story_drafts WHERE id=?',(staged['draft_id'],)).fetchone()[0])
            self.assertTrue(payload['day_trades']['morning']['corrections'])

    def test_account_rls_revision_and_movement_retry(self):
        a=self.make_account()
        args=dict(action='cash_movement',account_id=a['id'],expected_revision=1,data_json=json.dumps(dict(event_key='deposit',kind='deposit',at=T('10:30'),amount=fact('500'))))
        self.assertTrue(self.action('update_journal_account',args,'Deposit 500 dollars.')['ok'])
        self.assertTrue(self.action('update_journal_account',args,'Deposit 500 dollars.')['replayed'])
        args['data_json']=json.dumps(dict(event_key='deposit',kind='deposit',at=T('10:30'),amount=fact('600')))
        self.assertFalse(self.action('update_journal_account',args,'Deposit 600 dollars.')['ok'])
        self.assertEqual(self.action('get_account_performance',{'account_id':a['id']},'Read.')['closing_balance'],'10500')
        with self.db() as conn:
            self.assertTrue(conn.execute("SELECT relrowsecurity FROM pg_class WHERE oid='journal_accounts'::regclass").fetchone()[0])
            for role in ('anon','authenticated'):
                self.assertFalse(conn.execute("SELECT has_table_privilege(?,'journal_accounts','SELECT')",(role,)).fetchone()[0])
        for role in ('anon','authenticated'):
            with self.driver.connect(**self.admin_info) as conn:
                conn.execute(self.driver.sql.SQL('SET LOCAL ROLE {}').format(self.driver.sql.Identifier(role)))
                with self.assertRaises(self.driver.errors.InsufficientPrivilege):conn.execute('SELECT * FROM journal_accounts')
                conn.rollback()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',action='store_true');parser.add_argument('--postgres-bin',type=Path);args=parser.parse_args()
    if not args.run or not args.postgres_bin:parser.error('Use --run --postgres-bin for a disposable local cluster only.')
    with disposable_postgres(args.postgres_bin) as (adapter,driver,admin_info):
        PostgresAccountTests.adapter=adapter;PostgresAccountTests.driver=driver;PostgresAccountTests.admin_info=admin_info
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PostgresAccountTests))
    sys.exit(0 if result.wasSuccessful() else 1)
