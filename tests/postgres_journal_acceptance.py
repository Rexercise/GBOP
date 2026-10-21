"""Opt-in real PostgreSQL checks; never accepts an existing server or database URL.

Run only after approval for a disposable local server and test roles:
  PYTHONPATH=. python tests/postgres_journal_acceptance.py --run --postgres-bin /approved/bin

Not collected by unittest's default test*.py pattern. No driver installation,
downloads, external API calls, production credentials, or persistent service.
The launcher creates its own private Unix-socket cluster and removes it after
stopping PostgreSQL. It leaves the directory intact if shutdown cannot be proven.
TLS, the production server version/roles and Supabase pooler remain separate gates.
"""
import argparse
import ast
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
ADMIN = 'gbop_fixture_admin'
BACKEND = 'gbop_fixture_backend'
DATABASE = 'gbop_journal_fixture'
SYNTHETIC_DATE = '2040-01-01T00:00:00+00:00'


def bootstrap(adapter):
    """Execute the real base-schema functions without importing/running the bot."""
    source = ast.parse((ROOT / 'bot.py').read_text())
    names = ('init_db', 'init_journal_db', 'init_thesis_db', 'init_member_trade_flow_db')
    selected = [next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == name)
                for name in names]
    namespace = {'db': adapter.db}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(ROOT / 'bot.py'), 'exec'), namespace)
    for name in names:
        namespace[name]()
    from gbop_voice_web.journal_coach import init_coach
    init_coach(adapter.db)


@contextmanager
def disposable_postgres(bin_dir):
    """Only a newly initialized private cluster can become the test target."""
    binaries = {name: (bin_dir / name).resolve() for name in ('postgres', 'initdb', 'pg_ctl')}
    for name, executable in binaries.items():
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise RuntimeError(f'Approved PostgreSQL binary missing: {name}')
    # This is a dedicated subprocess, not the user's shell. Remove ambient DB,
    # service, dotenv and owner overrides before importing any application code.
    os.environ.clear()
    os.environ.update(PATH='/usr/bin:/bin:/usr/sbin:/sbin', LANG='C', LC_ALL='C')
    import psycopg
    from psycopg.conninfo import make_conninfo
    version = subprocess.run([str(binaries['postgres']), '--version'], check=True,
                             capture_output=True, text=True, timeout=10).stdout.strip()
    print(f'Isolated database engine: {version}; psycopg {psycopg.__version__}', flush=True)
    root = Path(tempfile.mkdtemp(prefix='gbop-pg-', dir='/tmp')).resolve()
    root.chmod(0o700)
    data, socket_dir = root / 'data', root / 'socket'
    socket_dir.mkdir(mode=0o700)
    admin_info = dict(host=str(socket_dir), port=5432, user=ADMIN, dbname='postgres',
                      connect_timeout=5, application_name='gbop-isolated-journal-test')
    try:
        subprocess.run([str(binaries['initdb']), '-D', str(data), '-U', ADMIN,
                        '--auth-local=trust', '--auth-host=reject', '--no-locale', '-E', 'UTF8'],
                       check=True, capture_output=True, text=True, timeout=30)
        options = (f"-c listen_addresses='' -c unix_socket_directories='{socket_dir}' "
                   '-c unix_socket_permissions=0700 -c max_connections=20 '
                   '-c statement_timeout=10000 -c lock_timeout=5000')
        subprocess.run([str(binaries['pg_ctl']), '-D', str(data), '-l', str(root / 'postgres.log'),
                        '-w', '-t', '15', '-o', options, 'start'],
                       check=True, capture_output=True, text=True, timeout=25)
        with psycopg.connect(**admin_info, autocommit=True) as admin:
            assert admin.execute('SHOW listen_addresses').fetchone()[0] == ''
            assert Path(admin.execute('SHOW data_directory').fetchone()[0]).resolve() == data
            for name in ('anon', 'authenticated'):
                admin.execute(psycopg.sql.SQL('CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS').format(
                    psycopg.sql.Identifier(name)))
            admin.execute(f'CREATE ROLE {BACKEND} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS')
            admin.execute(f'CREATE DATABASE {DATABASE} OWNER {BACKEND}')
        backend_dsn = make_conninfo(**{**admin_info, 'dbname': DATABASE, 'user': BACKEND})
        os.environ['SUPABASE_DB_URL'] = backend_dsn
        # Import exactly the production adapter. No replacement driver, SQL
        # translation, connection fake, or advisory-lock/RLS substitution.
        spec = importlib.util.spec_from_file_location('gbop_isolated_db_compat', ROOT / 'db_compat.py')
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        bootstrap(adapter)
        yield adapter, psycopg, {**admin_info, 'dbname': DATABASE}
    finally:
        stopped = False
        try:
            if (data / 'postmaster.pid').exists():
                subprocess.run([str(binaries['pg_ctl']), '-D', str(data), '-w', '-t', '15',
                                '-m', 'fast', 'stop'], check=True, capture_output=True, text=True, timeout=25)
            stopped = not (data / 'postmaster.pid').exists()
        finally:
            os.environ.pop('SUPABASE_DB_URL', None)
            if stopped:
                shutil.rmtree(root)
                print('Disposable PostgreSQL stopped; cluster and synthetic roles removed.', flush=True)
            else:
                print(f'STOP NOT CONFIRMED: retained isolated cluster at {root}. Do not deploy.',
                      file=sys.stderr, flush=True)
                raise RuntimeError('Disposable PostgreSQL shutdown was not confirmed.')


class PostgresJournalTests(unittest.TestCase):
    adapter = None
    driver = None
    admin_info = None

    def setUp(self):
        from gbop_voice_web import journal_coach, journal_drafts, journal_recall
        from gbop_voice_web.market_conversation import MarketConversation
        self.coach, self.drafts, self.recall = journal_coach, journal_drafts, journal_recall
        self.context_type = MarketConversation
        self.db = self.adapter.db
        self.contexts = []
        with self.db() as conn:
            tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()
            # Names come exclusively from this newly initialized fixture DB.
            statement = self.driver.sql.SQL('TRUNCATE {} RESTART IDENTITY CASCADE').format(
                self.driver.sql.SQL(',').join(self.driver.sql.Identifier(r['tablename']) for r in tables))
            conn._conn.execute(statement)
            for user in (20, 30):
                conn.execute('''INSERT INTO members
                    (guild_id,user_id,username,display_name,activated,leadership_ack,revoked,created_at,updated_at)
                    VALUES (10,?,'synthetic','Synthetic fixture',1,1,0,?,?)''',
                    (user, SYNTHETIC_DATE, SYNTHETIC_DATE))
        self.context = self.fresh()
        self.addCleanup(lambda: [context.close() for context in self.contexts])

    def fresh(self, user=20):
        context = self.context_type((10, user, 'postgres_fixture'), auth_provider=(self.db, 10, user))
        self.contexts.append(context)
        return context

    def action(self, name, args, text, context=None, user=20):
        context = context or self.context
        context.begin_turn(text)
        def runner(n, a):
            if n == 'get_journal_history':
                return self.recall.history(self.db, 10, user, a)
            return self.coach.coach_tool(self.db, 10, user, n, a)
        return context.run(name, args, runner)

    def stage(self, text='Journal my NAS trade. I entered long; opening risk and result R unknown.',
              values=None, draft_id=None, context=None, user=20):
        args = {'story_json': json.dumps(values or {})}
        if draft_id:
            args['draft_id'] = draft_id
        result = self.action('stage_journal_story', args, text, context, user)
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['persisted'], result)
        return result

    def save(self, draft, context=None):
        result = self.action('save_journal_story', {'draft_id': draft['draft_id']},
                             'Save this journal.', context)
        self.assertTrue(result['ok'], result)
        return result

    def count(self, table):
        with self.db() as conn:
            return conn._conn.execute(self.driver.sql.SQL('SELECT count(*) FROM {}').format(
                self.driver.sql.Identifier(table))).fetchone()[0]

    def snapshot(self):
        with self.db() as conn:
            result = {}
            for table in ('members', 'theses', 'journals', 'journal_details', 'journal_story_drafts',
                          'thesis_events', 'thesis_executions'):
                rows = conn._conn.execute(self.driver.sql.SQL('SELECT to_jsonb(t) FROM {} AS t').format(
                    self.driver.sql.Identifier(table))).fetchall()
                result[table] = sorted(json.dumps(r[0], sort_keys=True) for r in rows)
            return result

    def test_real_adapter_unknown_r_saved_correction_and_new_connection_retry(self):
        draft = self.stage(values={'entries': [{'entry_index': 1, 'status': 'open'}]})
        saved = self.save(draft)
        correction = self.action('save_journal_entry', {'trade_number': saved['trade_number'],
            'metadata_json': '{"direction":"Bearish"}'}, 'Correct Trade #1: I was short, not long.')
        self.assertTrue(correction['ok'], correction)
        self.context.close()
        self.context = self.fresh()
        read = self.action('get_journal_story', {'draft_id': draft['draft_id']}, 'Read the same draft.')
        self.assertEqual(read['story']['direction'], 'Bearish')
        self.assertNotIn('risk_r', read['story']['entries'][0])
        before = self.snapshot()
        retried = self.save(draft)
        self.assertTrue(retried['deduplicated'], retried)
        self.assertEqual(retried['trade_number'], saved['trade_number'])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual([self.count(t) for t in ('theses', 'journals', 'journal_story_drafts',
                                                'thesis_executions')], [1, 1, 1, 0])
        row = self.action('get_journal_history', {'trade_number': 1}, 'Read Trade #1.')['journals'][0]
        self.assertEqual(row['trade_facts']['direction'], 'Bearish')
        self.assertIsNone(row['result_r'])
        self.assertIsNone(row['metadata']['recorded_risk'])

    def test_numbered_continuation_reuses_saved_postgres_identity(self):
        draft = self.stage()
        saved = self.save(draft)
        self.context.close()
        self.context = self.fresh()
        self.context.begin_turn('Continue my journal for Trade #1. I felt calm at entry.')
        self.assertIsNone(self.context._journal_capture_error)
        self.assertEqual(self.context._journal_story['id'], draft['draft_id'])
        self.assertEqual(self.save(draft)['trade_number'], saved['trade_number'])
        self.assertEqual([self.count(t) for t in ('theses', 'journals', 'journal_story_drafts')], [1, 1, 1])

    def test_actual_privileges_and_rls_are_separate_from_backend_ownership(self):
        self.stage()
        with self.db() as conn:
            role = conn.execute('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
            self.assertFalse(role['rolsuper'])
            self.assertFalse(role['rolbypassrls'])
            self.assertTrue(conn.execute("SELECT relrowsecurity FROM pg_class WHERE oid='journal_story_drafts'::regclass").fetchone()[0])
            self.assertEqual(conn.execute("SELECT count(*) FROM pg_policies WHERE tablename='journal_story_drafts'").fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT count(*) FROM journal_story_drafts').fetchone()[0], 1)
            for role_name in ('anon', 'authenticated'):
                self.assertFalse(conn.execute("SELECT has_table_privilege(?, 'journal_story_drafts', 'SELECT')", (role_name,)).fetchone()[0])
        for role_name in ('anon', 'authenticated'):
            with self.subTest(role=role_name):
                with self.driver.connect(**self.admin_info) as conn:
                    conn.execute(self.driver.sql.SQL('SET LOCAL ROLE {}').format(self.driver.sql.Identifier(role_name)))
                    with self.assertRaises(self.driver.errors.InsufficientPrivilege):
                        conn.execute('SELECT * FROM journal_story_drafts')
                    conn.rollback()
                # Temporary grants in a rolled-back fixture transaction isolate
                # actual RLS behavior from the production DDL's privilege denial.
                with self.driver.connect(**self.admin_info) as conn:
                    try:
                        conn.execute(self.driver.sql.SQL('GRANT SELECT,INSERT ON journal_story_drafts TO {}').format(
                            self.driver.sql.Identifier(role_name)))
                        conn.execute(self.driver.sql.SQL('SET LOCAL ROLE {}').format(self.driver.sql.Identifier(role_name)))
                        self.assertEqual(conn.execute('SELECT count(*) FROM journal_story_drafts').fetchone()[0], 0)
                        with self.assertRaisesRegex(self.driver.errors.InsufficientPrivilege, 'row-level security'):
                            conn.execute("""INSERT INTO journal_story_drafts
                                (id,guild_id,user_id,status,revision,payload,created_at,updated_at)
                                VALUES ('synthetic-denied',10,20,'unfinished',1,'{}','fixture','fixture')""")
                    finally:
                        conn.rollback()

    def wait_for_lock(self, pid):
        deadline = time.monotonic() + 4
        with self.driver.connect(**self.admin_info, autocommit=True) as observer:
            while time.monotonic() < deadline:
                if observer.execute("SELECT 1 FROM pg_locks WHERE pid=%s AND locktype='advisory' AND NOT granted", (pid,)).fetchone():
                    return
                time.sleep(.01)
        self.fail('The competing PostgreSQL connection never waited for its advisory lock.')

    def blocked_member_write(self, *, revoke):
        from gbop_voice_web.journal_context import JournalBinding, journal_transaction
        waiting, errors, admitted = queue.Queue(), [], []
        other = self.fresh()
        other.begin_turn('Keep my existing journal facts.')
        capability = JournalBinding(other, other.generation)
        @contextmanager
        def observed_db():
            with self.db() as conn:
                waiting.put(conn.execute('SELECT pg_backend_pid()').fetchone()[0])
                yield conn
        def worker():
            try:
                with journal_transaction(observed_db, {'_journal_binding': capability}, 10, 20, serialize=True) as conn:
                    conn.execute("UPDATE members SET display_name='admitted fixture write' WHERE guild_id=10 AND user_id=20")
                    admitted.append(True)
            except Exception as exc:
                errors.append(exc)
        holder = self.db()
        thread = threading.Thread(target=worker, daemon=True)
        try:
            holder.execute('SELECT pg_advisory_xact_lock(?)', (20,))
            with self.db() as different:
                self.assertTrue(different.execute('SELECT pg_try_advisory_xact_lock(?)', (30,)).fetchone()[0])
            thread.start()
            self.wait_for_lock(waiting.get(timeout=3))
            self.assertEqual(admitted, [])
            if revoke:
                holder.execute("UPDATE members SET revoked=1,updated_at='revoked fixture' WHERE guild_id=10 AND user_id=20")
                holder._conn.commit()
            else:
                holder._conn.rollback()
        finally:
            holder._conn.close()
            if thread.ident:
                thread.join(timeout=6)
        self.assertFalse(thread.is_alive(), 'PostgreSQL waiter did not finish.')
        if revoke:
            self.assertEqual(admitted, [])
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], ValueError)
            self.assertIn('revoked', str(errors[0]).lower())
        else:
            self.assertEqual(errors, [])
            self.assertEqual(admitted, [True])
        with self.db() as conn:
            self.assertTrue(conn.execute('SELECT pg_try_advisory_xact_lock(?)', (20,)).fetchone()[0])

    def test_real_lock_blocks_same_member_allows_other_and_releases_on_rollback(self):
        self.blocked_member_write(revoke=False)

    def test_revocation_committed_during_real_lock_wait_rejects_writer(self):
        self.blocked_member_write(revoke=True)

    def test_concurrent_canonical_writers_keep_one_identity(self):
        from gbop_voice_web.unified_journal import create_journal_thesis, ensure_canonical_journal, CANONICAL_EVENT
        with self.db() as conn:
            thesis_id = create_journal_thesis(conn, 10, 20, {'asset': 'NAS100', 'direction': 'Bearish'})
        start, identities, errors = threading.Barrier(2), [], []
        def write(note):
            try:
                start.wait(timeout=3)
                with self.db() as conn:
                    identities.append(ensure_canonical_journal(conn, 10, 20, thesis_id, fields={'study_note': note}))
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=write, args=(note,), daemon=True) for note in ('first fixture', 'second fixture')]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=8)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(identities), 2)
        self.assertEqual(len(set(identities)), 1)
        self.assertEqual(self.count('journals'), 1)
        with self.db() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM thesis_events WHERE event=?', (CANONICAL_EVENT,)).fetchone()[0], 1)

    def test_stale_correction_and_foreign_member_cannot_overwrite(self):
        draft = self.stage()
        old = self.fresh()
        self.action('get_journal_story', {'draft_id': draft['draft_id']}, 'Read my draft.', old)
        self.stage('Correction: I was short, not long.', draft_id=draft['draft_id'])
        before = self.snapshot()
        stale = self.action('stage_journal_story', {'draft_id': draft['draft_id'], 'story_json': '{"direction":"Bullish"}'},
                            'Update the draft.', old)
        self.assertFalse(stale['ok'], stale)
        foreign = self.action('get_journal_story', {'draft_id': draft['draft_id']}, 'Read my draft.', self.fresh(30), 30)
        self.assertFalse(foreign['ok'], foreign)
        self.assertEqual(self.snapshot(), before)

    def test_two_members_concurrently_save_and_recall_only_their_own_journal(self):
        start, results, errors = threading.Barrier(2), {}, []
        contexts = {user: self.fresh(user) for user in (20, 30)}
        def write(user):
            try:
                start.wait(timeout=3)
                context = contexts[user]
                marker = f'Private synthetic account for member {user}.'
                draft = self.stage(values={'context_notes': marker}, context=context, user=user)
                saved = self.action('save_journal_story', {'draft_id': draft['draft_id']},
                                    'Save this journal.', context, user)
                self.assertTrue(saved['ok'], saved)
                recalled = self.action('get_journal_history', {'trade_number': 1},
                                       'Read Trade #1.', context, user)
                self.assertTrue(recalled['ok'], recalled)
                results[user] = saved, recalled['journals']
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=write, args=(user,), daemon=True) for user in (20, 30)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=8)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])
        self.assertEqual(set(results), {20, 30})
        for user, other in ((20, 30), (30, 20)):
            saved, rows = results[user]
            self.assertEqual(saved['trade_number'], 1)
            self.assertEqual(len(rows), 1)
            self.assertIn(f'Private synthetic account for member {user}.', json.dumps(rows))
            self.assertNotIn(f'Private synthetic account for member {other}.', json.dumps(rows))
        self.assertEqual([self.count(t) for t in ('theses', 'journals', 'journal_story_drafts',
                                                'thesis_executions')], [2, 2, 2, 0])

    def test_postgres_constraint_failure_rolls_back_whole_finalization(self):
        draft = self.stage()
        with self.db() as conn:
            conn.execute("ALTER TABLE journal_story_drafts ADD CONSTRAINT fixture_reject_finalize CHECK(status <> 'finalized')")
        before = self.snapshot()
        failed = self.action('save_journal_story', {'draft_id': draft['draft_id']}, 'Save this journal.')
        self.assertFalse(failed['ok'], failed)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.count('journals'), 0)
        self.assertEqual(self.count('theses'), 0)
        with self.db() as conn:
            conn.execute('ALTER TABLE journal_story_drafts DROP CONSTRAINT fixture_reject_finalize')
        refreshed = self.action('get_journal_story', {'draft_id': draft['draft_id']}, 'Read my draft.', self.fresh())
        self.assertTrue(refreshed['ok'], refreshed)
        self.save(refreshed, self.fresh())
        self.assertEqual(self.count('journals'), 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='Explicitly start and remove a disposable local PostgreSQL cluster.')
    parser.add_argument('--postgres-bin', type=Path, help='Approved PostgreSQL bin directory; no installation is performed.')
    args = parser.parse_args()
    if not args.run or args.postgres_bin is None:
        parser.error('Execution requires --run and --postgres-bin after infrastructure approval.')
    sys.path.insert(0, str(ROOT))
    with disposable_postgres(args.postgres_bin) as (adapter, driver, admin_info):
        PostgresJournalTests.adapter = adapter
        PostgresJournalTests.driver = driver
        PostgresJournalTests.admin_info = admin_info
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(PostgresJournalTests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
