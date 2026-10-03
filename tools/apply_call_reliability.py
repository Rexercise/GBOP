"""Apply reviewed, exact-match edits to the existing GBOP implementation.

Used only on the repair branch. Every replacement must match once; unexpected
source drift aborts before any production branch is changed.
"""
from pathlib import Path


def replace(source, old, new):
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f'Expected one source match, found {count}: {old[:100]!r}')
    return source.replace(old, new, 1)


changes = {}
p = Path('gbop_voice_web/market_data.py')
s = p.read_text()
s = replace(s, 'from gbop_voice_web.shift_review import review_shift',
            'from gbop_voice_web.shift_review import review_shift\nfrom gbop_voice_web.smt_evidence import compare_ranges')
s = replace(s, 'MARKET_TOOLS = [', '''MARKET_TOOLS = [
    schema('review_market_smt', 'Compare two positively related instruments against the SAME selected anchor and matched observation bars. Returns observed range SMT independently from later CRT invalidation, entry confirmation and execution. For gold/silver shift recaps the comparison is included automatically.', {
        'asset': {'type': 'string'}, 'other_asset': {'type': 'string'},
        'anchor_start_ny': {'type': 'string'}, 'anchor_end_ny': {'type': 'string'},
        'through_ny': {'type': 'string'}}),''')
s = replace(s, 'def market_tool(db, name, args):', '''def paired_smt_review(db, asset, other_asset, anchor_start, anchor_end, through):
    """Read both broker histories without exposing quotes or member records."""
    try:
        feeds = [read_feed(db, asset_name(value)) for value in (asset, other_asset)]
        if any(not feed.get('ok') for feed in feeds):
            return {'status': 'insufficient_matched_data', 'events': [],
                    'reason': 'Both instruments need stored broker candles before SMT can be checked.'}
        left, left_step = history_bars(db, feeds[0], anchor_start, through)
        right, right_step = history_bars(db, feeds[1], anchor_start, through)
        comparison = compare_ranges(left, right, anchor_start, anchor_end, through,
                                    left_step, right_step, feeds[0]['asset'], feeds[1]['asset'])
        comparison['sources'] = [{'asset': feed['asset'], 'symbol': feed['symbol'],
                                  'source': feed['source']} for feed in feeds]
        return comparison
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return {'status': 'insufficient_matched_data', 'events': [], 'reason': str(exc)}
    except Exception as exc:
        print('[GBOP-SMT] paired history unavailable:', type(exc).__name__)
        return {'status': 'temporarily_unavailable', 'events': [],
                'reason': 'The paired history lookup failed; no SMT conclusion can be made.'}


def market_tool(db, name, args):''')
s = replace(s, "        result = read_feed(db, args.get('asset'))", '''        if name == 'review_market_smt':
            comparison = paired_smt_review(db, args.get('asset'), args.get('other_asset'),
                parse_time(args['anchor_start_ny']), parse_time(args['anchor_end_ny']),
                parse_time(args['through_ny']))
            return {'ok': True, 'review': comparison}
        result = read_feed(db, args.get('asset'))''')
s = replace(s, "            result['review'] = session_review(bars, day.isoformat(), shift, step)", '''            result['review'] = session_review(bars, day.isoformat(), shift, step)
            partner = {'XAUUSD': 'XAGUSD', 'XAGUSD': 'XAUUSD'}.get(result['asset'])
            if partner:
                result['review']['paired_smt'] = paired_smt_review(
                    db, result['asset'], partner, start + 3600, start + 7200, end)''')
s = replace(s, '''For "what did price do today/this shift?", use review_market_session and lead with
shift_story.recap.spoken_summary (voice may expose this as shift_recap.spoken_summary),
not only observations[0]. It is an evidence-built complete answer: paraphrase
naturally while retaining later ranges and outcomes. Its chapters provide detail.''', '''For "what did price do today/this shift?", use review_market_session. Gold/silver
reviews include paired_smt automatically: incorporate its observed divergence in
chronological order, then distinguish the single-market CRT chapters. Use
shift_story.recap (voice may expose shift_recap) for those chapters, not only
observations[0]. A failed later bullish reversal does not cancel an earlier bearish
SMT leg. Never repeat a summary that contradicts the paired timestamped evidence.
For other selected anchors use review_market_smt with both instruments and identical
anchor boundaries. Its scope is selected-range SMT, not all discretionary swing SMT.
SMT observation, valid entry model, and subsequent market/trade outcome are separate.
An observed paired divergence does NOT require both CRTs to deliver or remain valid
later. Later invalidation cannot erase the historical divergence. Check missing
matched coverage; do not infer the non-sweeping side from one market alone. Target
touches are market observations, not proof of an active CRT, an entry, or profit.''')
s = replace(s, 'Range candidates are not confirmed CSD, Super Soup, SMT, Blessed Thief entries, signals,',
            'Single-market range candidates do not establish SMT; use paired evidence.\nRange candidates are not confirmed CSD, Super Soup, Blessed Thief entries, signals,')
changes[p] = s

p = Path('gbop_voice_web/trade_photos.py')
s = p.read_text()
s = replace(s, 'import uuid\n', 'import uuid\nfrom gbop_voice_web.journal_retrieval import JOURNAL_TOOLS, JOURNAL_NAMES, JOURNAL_PROMPT, journal_tool\n')
s = replace(s, '\n\ndef init_photos(db):', '\n\nPHOTO_PROMPT += "\\n\\n" + JOURNAL_PROMPT\n\ndef init_photos(db):')
s = replace(s, "    if name == 'annotate_trade_photo':", "    if name in JOURNAL_NAMES:\n        return journal_tool(db, guild_id, user_id, name, args)\n    if name == 'annotate_trade_photo':")
s = replace(s, "PHOTO_NAMES = {t['name'] for t in PHOTO_TOOLS}", "PHOTO_TOOLS = JOURNAL_TOOLS + PHOTO_TOOLS\nPHOTO_NAMES = {t['name'] for t in PHOTO_TOOLS}")
s = replace(s, "            clauses.append('p.id IN (SELECT photo_id FROM journal_details WHERE journal_id=? AND guild_id=? AND user_id=?)')\n            params.extend([jid,guild_id,user_id])", """            clauses.append('(p.id IN (SELECT photo_id FROM journal_details WHERE journal_id=? AND guild_id=? AND user_id=?) OR p.thesis_id IN (SELECT thesis_id FROM journals WHERE id=? AND guild_id=? AND user_id=?))')
            params.extend([jid,guild_id,user_id,jid,guild_id,user_id])""")
changes[p] = s

p = Path('gbop_voice_web/voice_policy.py')
s = p.read_text()
s = replace(s, 'outside trading lore. Give classifications first with the shortest supported\nreason.', 'outside trading lore. Answer the actual request first, with the shortest supported\nreason. Use classifications only for setup questions, not journal requests.')
s = replace(s, 'VOICE_OPERATIONS = """', 'from gbop_voice_web.journal_retrieval import JOURNAL_PROMPT\n\nVOICE_OPERATIONS = """')
s = replace(s, '''Clear speech in their authorized room addresses you. New member speech stops the''', '''When corrected, recheck the disputed fact, not the member's confidence. Correct
an error plainly instead of repeating "classification, not confirmed". Distinguish
observed divergence, unverified entry, and later outcome. Stop a market explanation
when the member changes the task to journals. Do not infer an interruption failure
from silence or muted audio. Do not claim to have checked without tool evidence.
Clear speech in their authorized room addresses you. New member speech stops the''')
s = replace(s, '''are /gbop action:pause and /gbop action:resume. For tools-disabled rate-limit recovery,
answer the latest unanswered request only from verified existing evidence/outputs;
do not repeat an action or claim a new one. If required evidence is absent, explain
that it could not be completed and ask the member to repeat the request.''', '''are /gbop action:pause and /gbop action:resume. Rate-limit recovery allows audited
reads but never retries saves, deletions or DMs. Retrieve missing journal/market
facts with available read tools. If delivery is blocked, distinguish successful
retrieval from an unsent message; never claim generic lack of journal access.
Answer the latest request, not the old debate. Do not invent a completed action.''')
s = replace(s, '\n\ndef build_voice_instructions', '\n\nVOICE_OPERATIONS += "\\n\\n" + JOURNAL_PROMPT\n\ndef build_voice_instructions')
changes[p] = s

p = Path('gbop_voice_web/voice_runtime.py')
s = p.read_text()
s = replace(s, 'import re\n', 'import re\nfrom gbop_voice_web.recovery_policy import READ_ONLY_TOOLS, recovery_options, recovery_denial\n')
s = replace(s, '    """Bounded response-only recovery; never re-execute a tool or a stale turn."""',
            '    """Bounded read-only recovery; never retry mutations or a stale turn."""')
s = replace(s, '        self.session = session\n', '        self.session = session\n        self.read_only_turn = None\n')
s = replace(s, '    def cancel(self, reset=False):', '''    @property
    def read_only_active(self):
        return self.read_only_turn is not None and self.read_only_turn == self.session._voice_turn_count

    def options(self, options=None):
        if self.read_only_active:
            return recovery_options(getattr(self.session, '_voice_tools', []), options)
        return options or {}

    def tool_denial(self, name):
        return recovery_denial(name) if self.read_only_active and name not in READ_ONLY_TOOLS else None

    def cancel(self, reset=False):''')
s = replace(s, '''            # Existing function outputs are in the conversation. Disable tools
            # during recovery so saves/deletes and other actions cannot repeat.
            await session.send_event({'type': 'response.create', 'response': {
                **getattr(session, '_last_response_options', {}),
                'tool_choice': 'none',
            }})''', '''            # Reads can recover missing facts. Keep every response in this turn
            # read-only, including subsequent function-output replies. A new
            # speech turn increments the turn counter and restores normal tools.
            self.read_only_turn = turn
            await session.send_event({'type': 'response.create', 'response':
                self.options(getattr(session, '_last_response_options', {}))})''')
changes[p] = s

p = Path('bot.py')
s = p.read_text()
s = replace(s, '        self._last_response_options = {}\n        self._voice_turn_count = 0',
            '''        self._last_response_options = {}
        self._voice_tools = [{k: v for k, v in tool.items() if k != "strict"} for tool in GBOP_AI_TOOLS]
        self._voice_turn_count = 0''')
s = replace(s, '''                "tools": [{k: v for k, v in tool.items() if k != "strict"} for tool in GBOP_AI_TOOLS],''',
            '''                "tools": self._voice_tools,''')
s = replace(s, '''        try:
            result = await asyncio.to_thread(
                ai_execute_tool,
                self.member.id,
                name,
                args,
            )
        except Exception as exc:
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}''', '''        result = self.rate_limit_recovery.tool_denial(name)
        if result is None:
            try:
                result = await asyncio.to_thread(
                    ai_execute_tool,
                    self.member.id,
                    name,
                    args,
                )
            except Exception as exc:
                print('[GBOP-RT-TOOL] lookup failed:', name, type(exc).__name__)
                result = {'ok': False, 'error_code': 'tool_unavailable',
                          'error': 'The requested action could not be completed. This is a temporary tool error, not evidence that saved records are absent.'}''')
s = replace(s, '''                    await self.send_event({"type": "response.create", 'response': options})''',
            '''                    await self.send_event({"type": "response.create", 'response': self.rate_limit_recovery.options(options)})''')
changes[p] = s

p = Path('gbop_voice_web/gtop_knowledge.txt')
s = p.read_text()
s += '''\n\nSMT — MATCHED DIVERGENCE, ENTRY, AND OUTCOME ARE SEPARATE
For a positively related pair, compare equivalent anchors at the same moment.
One instrument sweeping its anchor buy-side while the other has not swept its
own buy-side is bearish relative divergence; sell-side nonconfirmation is bullish.
For 9ate8, compare the 8 o'clock anchors during the 9 o'clock manipulation.
Use matched evidence for BOTH instruments. This is not confirmed from one chart.
A later CRT invalidation, different directional range outcome, or failed trade
cannot retroactively erase a divergence already established by matched evidence.
Do not require both markets to deliver their CRT objectives to recognize SMT.
SMT is contextual evidence, not automatic CSD, Super Soup, execution or profit.
A later sweep by the other instrument changes the continuing context, not the
historical fact that it had not confirmed at the earlier matched moment.
'''
changes[p] = s

for p, source in changes.items():
    if p.suffix == '.py':
        compile(source, str(p), 'exec')
for p, source in changes.items():
    p.write_text(source)
print('Applied exact-match call reliability edits to:', ', '.join(map(str, changes)))
