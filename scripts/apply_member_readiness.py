"""One-shot, fail-closed source patch for the reviewed GBOP revision."""
from pathlib import Path
import ast


def change(path, old, new):
    path = Path(path)
    source = path.read_text()
    if source.count(old) != 1:
        raise RuntimeError(f'{path}: expected exactly one reviewed anchor: {old[:95]!r}')
    path.write_text(source.replace(old, new, 1))


for path, owner, tools in [('bot.py', 'GTOP_OWNER_USER_ID', 'GBOP_AI_TOOLS'),
                           ('gbop_voice_web/server.py', 'OWNER_USER_ID', 'TOOLS')]:
    change(path, 'from gbop_voice_web.member_access import member_access_error',
           'from gbop_voice_web.journal_recall import JOURNAL_PROMPT, configure_journal_tools, journal_tool\n'
           'from gbop_voice_web.member_access import member_access_error')
    change(path, '    if name in MARKET_NAMES:\n        return market_tool(db, name, args)',
           f'    if name in {{"get_journal_history", "send_journal_history"}}:\n'
           f'        return journal_tool(db, GTOP_GUILD_ID, user_id, {owner}, name, args)\n'
           '    if name in MARKET_NAMES:\n        return market_tool(db, name, args)')
    change(path, tools + '.extend(MARKET_TOOLS)',
           tools + '.extend(MARKET_TOOLS)\nconfigure_journal_tools(' + tools + ')')
    change(path, '+ "\\n\\n" + MARKET_PROMPT\n)',
           '+ "\\n\\n" + MARKET_PROMPT\n    + "\\n\\n" + JOURNAL_PROMPT\n)')

path = 'gbop_voice_web/market_data.py'
change(path, "NY = ZoneInfo('America/New_York')",
       'from gbop_voice_web.smt_review import PAIRS, SMT_PROMPT, paired_market_review\n\n'
       "NY = ZoneInfo('America/New_York')")
change(path, "MARKET_NAMES = {t['name'] for t in MARKET_TOOLS}",
       "MARKET_TOOLS.append(schema('review_market_smt',\n"
       "    'Compare gold and silver at synchronized 9 oclock bars against their 8 oclock anchors. '\n"
       "    'Returns divergence separately from entry confirmation and later price touches.', {\n"
       "    'asset': {'type': 'string'}, 'correlated_asset': {'type': ['string', 'null']},\n"
       "    'date_ny': {'type': ['string', 'null']}, 'shift': {'type': 'string', 'enum': ['day', 'night']}}))\n"
       "MARKET_NAMES = {t['name'] for t in MARKET_TOOLS}")
change(path, "        result = read_feed(db, args.get('asset'))",
       "        if name == 'review_market_smt':\n            return paired_market_review(db, args)\n"
       "        result = read_feed(db, args.get('asset'))")
change(path, "            result['review'] = session_review(bars, day.isoformat(), shift, step)",
       "            result['review'] = session_review(bars, day.isoformat(), shift, step)\n"
       "            if result['asset'] in PAIRS:\n"
       "                try:\n"
       "                    result['paired_smt'] = paired_market_review(db, {\n"
       "                        'asset': result['asset'], 'date_ny': day.isoformat(), 'shift': shift})\n"
       "                except Exception as exc:\n"
       "                    print('[GBOP-SMT] paired lookup failed:', type(exc).__name__)\n"
       "                    result['paired_smt'] = {'ok': False, 'status': 'paired_lookup_failed',\n"
       "                        'error': 'Paired candles could not be retrieved; SMT is unresolved, not absent.'}")
with Path(path).open('a') as f:
    f.write('\n\nMARKET_PROMPT += "\\n\\n" + SMT_PROMPT\n'
            'LIVE_MARKET_PROMPT += "\\n\\nPreserve the backend paired_smt result. '
            'Later CRT failure does not erase earlier observed SMT. '
            'Delegate journal reads and private sends to the backend; never claim no journal access without trying."\n')

path = 'gbop_voice_web/voice_policy.py'
change(path, 'VOICE_OPERATIONS = """',
       'from gbop_voice_web.journal_recall import JOURNAL_PROMPT\n\nVOICE_OPERATIONS = """')
change(path, 'outside trading lore. Give classifications first with the shortest supported\nreason.',
       'outside trading lore. Answer the latest request directly with the shortest supported\nreason; do not mechanically preface replies with "Classification, not confirmed".')
change(path, 'For tools-disabled rate-limit recovery,\nanswer the latest unanswered request only from verified existing evidence/outputs;\ndo not repeat an action or claim a new one. If required evidence is absent, explain\nthat it could not be completed and ask the member to repeat the request.',
       'During rate-limit recovery, read-only journal and market tools remain available.\nUse those reads when evidence is missing; never invent a generic lack of journal access.\nWrites and DM sends are deliberately blocked during recovery to prevent duplicate actions.\nIf a send is blocked, read the requested records and explain delivery needs a fresh request.\nNever claim a save, send, or deletion without a successful tool result.')
change(path, "                         market_prompt, '# STARTUP MEMBER PROFILE\\n' + member_state))",
       "                         market_prompt, JOURNAL_PROMPT, '# STARTUP MEMBER PROFILE\\n' + member_state))")

path = 'gbop_voice_web/voice_runtime.py'
change(path, "class VoiceRateLimitRecovery:",
       "VOICE_READ_ONLY_TOOLS = frozenset({\n"
       "    'get_journal_history', 'get_trade_state', 'get_risk_profile', 'get_member_plan',\n"
       "    'get_member_dashboard', 'get_trade_assist', 'get_shift_plans', 'get_performance_review',\n"
       "    'find_journal_setups', 'get_activity_check', 'get_ss_review',\n"
       "    'get_market_price', 'review_market_session', 'review_market_crt',\n"
       "    'review_market_smt', 'inspect_market_candles', 'list_trade_photos',\n"
       "})\n\n\nclass VoiceRateLimitRecovery:")
change(path, '        self.notified = False\n\n    def cancel',
       '        self.notified = False\n        self.recovery_turn = None\n\n'
       '    def active_for_current_turn(self):\n'
       '        return self.recovery_turn is not None and self.recovery_turn == self.session._voice_turn_count\n\n'
       '    def allows_tool(self, name):\n'
       '        return not self.active_for_current_turn() or name in VOICE_READ_ONLY_TOOLS\n\n'
       '    def response_options(self):\n'
       '        tools = [t for t in getattr(self.session, "_voice_tools", [])\n'
       '                 if t.get("name") in VOICE_READ_ONLY_TOOLS]\n'
       '        return {**getattr(self.session, "_last_response_options", {}),\n'
       '                "tools": tools, "tool_choice": "auto" if tools else "none"}\n\n'
       '    def cancel')
change(path, "            # Existing function outputs are in the conversation. Disable tools\n"
             "            # during recovery so saves/deletes and other actions cannot repeat.\n"
             "            await session.send_event({'type': 'response.create', 'response': {\n"
             "                **getattr(session, '_last_response_options', {}),\n"
             "                'tool_choice': 'none',\n"
             "            }})",
       "            # Restore safe reads, never replay writes or private sends.\n"
       "            self.recovery_turn = turn\n"
       "            await session.send_event({'type': 'response.create',\n"
       "                                      'response': self.response_options()})")

path = 'bot.py'
change(path, '    def session_update(self):\n        return {',
       '    def session_update(self):\n'
       '        self._voice_tools = [{k: v for k, v in tool.items() if k != "strict"} for tool in GBOP_AI_TOOLS]\n'
       '        return {')
change(path, '"tools": [{k: v for k, v in tool.items() if k != "strict"} for tool in GBOP_AI_TOOLS],',
       '"tools": self._voice_tools,')
change(path, '            result = await asyncio.to_thread(\n                ai_execute_tool,\n'
             '                self.member.id,\n                name,\n                args,\n            )',
       '            if not self.rate_limit_recovery.allows_tool(name):\n'
       '                result = {"ok": False, "status": "recovery_read_only",\n'
       '                    "error": "Recovery allows journal/market reads, not saves, deletes or sends. '
       'Read the requested records now; a fresh member request is needed for delivery or changes."}\n'
       '            else:\n'
       '                result = await asyncio.to_thread(\n                    ai_execute_tool,\n'
       '                    self.member.id,\n                    name,\n                    args,\n                )')
change(path, '        # The verified result is already in the conversation. Rebuilding every',
       '        if self.rate_limit_recovery.active_for_current_turn():\n'
       '            self._tool_response_options.update(self.rate_limit_recovery.response_options())\n'
       '        # The verified result is already in the conversation. Rebuilding every')
change(path, 'print("[GBOP-VOICE-POLICY] compact-v1 instructions_chars=",',
       'print("[GBOP-VOICE-POLICY] member-readiness-v1 instructions_chars=",')

for path in ['bot.py', 'gbop_voice_web/server.py', 'gbop_voice_web/market_data.py',
             'gbop_voice_web/voice_policy.py', 'gbop_voice_web/voice_runtime.py']:
    ast.parse(Path(path).read_text())
print('Reviewed member-readiness source patch applied; no credentials, schema or member records changed.')
