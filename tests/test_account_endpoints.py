"""Authenticated HTTP route behavior with isolated account records."""
import ast
import asyncio
from pathlib import Path
import unittest
from fastapi import FastAPI,Request,HTTPException
from fastapi.responses import JSONResponse
import test_journal_accounts as accounts

class AccountEndpointTests(unittest.TestCase):
    setUp=accounts.AccountJournalTests.setUp
    tearDown=accounts.AccountJournalTests.tearDown
    review=accounts.AccountJournalTests.review
    call=accounts.AccountJournalTests.call
    make_account=accounts.AccountJournalTests.make_account
    def test_http_routes_bind_session_member_and_deny_foreign_account(self):
        a=self.make_account()
        source=ast.parse((Path(__file__).resolve().parents[1]/'gbop_voice_web/server.py').read_text())
        routes=[n for n in source.body if isinstance(n,ast.AsyncFunctionDef) and n.name in ('api_journal_accounts','api_journal_performance')]
        async def authenticated(request):
            if not request.headers.get('x-fixture-user'):raise HTTPException(status_code=401,detail='Sign in')
            return dict(user_id=request.headers['x-fixture-user'])
        app=FastAPI()
        ns=dict(app=app,asyncio=asyncio,Request=Request,HTTPException=HTTPException,JSONResponse=JSONResponse,db=self.db,GTOP_GUILD_ID=10,require_authenticated_user=authenticated)
        # Fixture SQLite is thread-affine; serialize only the route's DB dispatch.
        class Inline:
            @staticmethod
            async def to_thread(fn,*args):return fn(*args)
        ns['asyncio']=Inline
        exec(compile(ast.Module(body=routes,type_ignores=[]),'server-account-routes','exec'),ns)
        # Invoke on this thread with real Starlette request/response objects.
        def req(user):return Request({'type':'http','headers':[] if user is None else [(b'x-fixture-user',str(user).encode())]})
        with self.assertRaises(HTTPException) as noauth:asyncio.run(ns['api_journal_accounts'](req(None)))
        self.assertEqual(noauth.exception.status_code,401)
        response=asyncio.run(ns['api_journal_accounts'](req(20)));self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertIn(a['id'].encode(),response.body)
        response=asyncio.run(ns['api_journal_accounts'](req(30)));self.assertNotIn(a['id'].encode(),response.body)
        with self.assertRaises(HTTPException) as foreign:asyncio.run(ns['api_journal_performance'](req(30),a['id']))
        self.assertEqual(foreign.exception.status_code,400);self.assertNotIn('10000',foreign.exception.detail)
