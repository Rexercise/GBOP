"""One-shot exact-source integration, removed by the validating workflow."""
from pathlib import Path
import ast


def replace(path,old,new,count=1):
    file=Path(path); text=file.read_text()
    actual=text.count(old)
    if actual!=count:
        raise RuntimeError(f'{path}: expected {count} integration anchors, found {actual}: {old[:90]!r}')
    file.write_text(text.replace(old,new))

imports='from gbop_voice_web.market_watch import WATCH_TOOLS, WATCH_NAMES, WATCH_PROMPT, watch_tool, init_watches\n'
for path in ('bot.py','gbop_voice_web/server.py'):
    file=Path(path); text=file.read_text(); file.write_text(imports+text)
    replace(path,'    if name in MARKET_NAMES:\n        return market_tool(db, name, args)',
        '    if name in WATCH_NAMES:\n        return watch_tool(db, GTOP_GUILD_ID, user_id, '+('GTOP_OWNER_USER_ID' if path=='bot.py' else 'OWNER_USER_ID')+', name, args)\n    if name in MARKET_NAMES:\n        return market_tool(db, name, args)')
    prefix='GBOP_AI_TOOLS' if path=='bot.py' else 'TOOLS'
    replace(path,prefix+'.extend(MARKET_TOOLS)',prefix+'.extend(MARKET_TOOLS)\n'+prefix+'.extend(WATCH_TOOLS)')
replace('bot.py','def _init_market_db():\n    init_market(db)','def _init_market_db():\n    init_market(db)\n    init_watches(db)')
replace('bot.py','async def on_ready():\n    global GBOP_CHECKIN_TASK',
    'async def on_ready():\n    global GBOP_CHECKIN_TASK\n    from gbop_voice_web.market_watch_runtime import start_watch_runtime\n    start_watch_runtime(client, db, GTOP_GUILD_ID, GTOP_OWNER_USER_ID, GTOP_MEMBER_ROLE_ID)')
replace('gbop_voice_web/server.py','    await asyncio.to_thread(init_market, db)','    await asyncio.to_thread(init_market, db)\n    await asyncio.to_thread(init_watches, db)')

path='gbop_voice_web/market_data.py'
replace(path,'from gbop_voice_web.market_context import PAIRINGS, enrich_smt, LIFECYCLE_PROMPT',
    'from gbop_voice_web.market_context import PAIRINGS, enrich_smt, LIFECYCLE_PROMPT\nfrom gbop_voice_web.market_watch import WATCH_PROMPT')
replace(path,".strip() + '\\n\\n' + LIFECYCLE_PROMPT", ".strip() + '\\n\\n' + LIFECYCLE_PROMPT + '\\n\\n' + WATCH_PROMPT",count=2)
helper='''def latest_available_shift_date(feed, shift):
    """Null date means latest shift represented by CLOSED feed candles, not a future Saturday session."""
    if shift not in ('day', 'night'):
        raise ValueError('shift must be day or night.')
    first, last = (9, 12) if shift == 'day' else (21, 24)
    now = int(time.time())
    dates = []
    for key, step in (('bars', 300), ('bars_m1', 60)):
        for bar in feed.get(key, []):
            moment = datetime.fromtimestamp(bar['time'], NY)
            if bar['time'] + step <= now and first <= moment.hour < last:
                dates.append(moment.date())
    if not dates:
        raise ValueError('No closed candles identify the latest requested shift. Specify a date to inspect retained history.')
    return max(dates).isoformat()


'''
replace(path,'def market_tool(db, name, args):',helper+'def market_tool(db, name, args):')
replace(path,"            day = date.fromisoformat(args.get('date_ny') or datetime.now(NY).date().isoformat())\n            shift = args.get('shift', 'day')",
    "            shift = args.get('shift', 'day')\n            day = date.fromisoformat(args.get('date_ny') or latest_available_shift_date(result, shift))")
replace(path,"'date_ny': {'type': ['string', 'null']},\n        'shift':", "'date_ny': {'type': ['string', 'null'], 'description': 'Explicit NY date; null selects the latest shift represented by closed feed candles.'},\n        'shift':")

path='gbop_voice_web/candle_lifecycle.py'
replace(path,'from datetime import datetime','from datetime import datetime\nfrom gbop_voice_web.super_soup_classification import classify_super_soup')
replace(path,"                fact.update(_follow(row, side, rows[i+1:], bars, anchor, end, step, invalid_at, mapped))",
    "                fact.update(_follow(row, side, rows[i+1:], bars, anchor, end, step, invalid_at, mapped))\n                fact['super_soup']['classification'] = classify_super_soup(\n                    row, side, rows[i+1:], bars, anchor, end, step, invalid_at, mapped)")
replace(path,"        if body['body_reference_retest']['evidence']:",
    "        classification = body['super_soup'].get('classification', {})\n        if classification.get('formation') in ('clean', 'unclean'):\n            parts.append(f\"Super Soup formation was {classification['formation']}; its Model 1-range CRT outcome was {classification['outcome'].replace('_', ' ')}. Parent-range objectives are assessed separately.\")\n        if body['body_reference_retest']['evidence']:")
replace(path,"'not an assumed stop. Missing data means unverified. Times are candle intervals, not ticks.'",
    "'not an assumed stop. Missing data means unverified. Times are candle intervals, not ticks. '\n                  'Use super_soup.classification for cleanliness, nested CRT variant, outcome and separate parent function.'")

# Keep known absence before CSD separate from a later coverage gap; no sequel is unknown.
path='gbop_voice_web/super_soup_classification.py'
replace(path,"'sequence_gap_at': gap, 'formation': 'unverified' if gap else 'none_observed',",
    "'sequence_gap_at': gap, 'formation': 'unverified' if not sequence or (gap and csd_index is None) else 'none_observed',")

path='gbop_voice_web/voice_runtime.py'
replace(path,"    'review_market_crt', 'inspect_market_candles', 'review_market_smt',",
    "    'review_market_crt', 'inspect_market_candles', 'review_market_smt', 'get_prepared_market_brief',")
replace(path,'RECOVERY_NAMES = READ_ONLY_RECOVERY_NAMES | PRIVATE_DELIVERY_NAMES',
    "RECOVERY_NAMES = READ_ONLY_RECOVERY_NAMES | PRIVATE_DELIVERY_NAMES | frozenset({'manage_market_watch'})")
replace(path,"    if getattr(session, '_recovery_active', False) and name not in RECOVERY_NAMES:",
    "    if getattr(session, '_recovery_active', False) and name == 'manage_market_watch' and args.get('action') not in ('list', 'cancel'):\n        return {'ok': False, 'error': 'New watches are not started during recovery. Existing watches can be listed or cancelled.'}\n    if getattr(session, '_recovery_active', False) and name not in RECOVERY_NAMES:")

Path('gbop_voice_web/gtop_tab.txt').write_text('''GTOP TAB REFINEMENT — 3 OCTOBER 2026
The Model 1 is the qualifying assigned-timeframe body-purge candle. Identify it by its own open/close times, OHLC and purged boundary before discussing CSD, Super Soup or an execution. Confirmation and failure never erase candle identity.
SUPER SOUP STRUCTURE AND FUNCTION
Treat the Model 1 candle as its own CRT on the SAME assigned timeframe. Subsequent candles may soup it immediately or after inside bars. Record what each candle did: inside bar, wick sweep, body close outside, return inside, repeated soup, distribution or invalidation.
Use super_soup.classification.formation for cleanliness and .outcome for the nested CRT outcome. Clean means a supported sweep/inside-close CRT treatment, not an automatic win. An unclean body-sweep sequence can still perform the intended parent-range function. A clean soup can fail. Keep absence, incomplete evidence, pending development, partial delivery and failure distinct.
V4 is one inside bar before manipulation; V5 is two or more. V6 requires re-souping the soup candle's extreme with return inside, not merely re-touching the original Model 1 boundary. V1/V2/V3 distribution labels require ordered opposing-liquidity evidence. The next candle need not be the soup candle. Same-source-bar sweep/delivery or same-assigned-candle soup/CSD order may remain unresolved; state the observed form without inventing entry timing.
Use nested_crt_objectives for the Model 1 candle's own midpoint/opposite side. Use parent_range_function for the original selected range's objectives. Name which objective you mean. Do not call a failed nested CRT a successful entry just because the parent later distributed, and do not erase later directional function. Directional delivery without a qualifying sweep is not a Super Soup.
Read each correlated asset's own objectives, including the boneless asset, from paired evidence. Do not infer a local Model 1, trade or institutional intent from SMT.
LIVE WATCHES AND PREPARATION
Watch registration, listing and cancellation use authenticated member-scoped tools. Alerts go privately to that member in Discord. Watches end at the returned shift cutoff, use closed candles, pause on stale feeds and do not place orders. Never claim a watch was registered without a successful result. Never claim continuous availability on the current hosting plan.
The prepared briefing is a timestamped saved analysis of connected feeds, not proof that a future or missing candle was observed. Read its date and as-of time. Refresh through market tools when finer/newer evidence is requested. Do not present a missing comparison feed as no SMT.
''')
replace('gbop_voice_web/gtop_protocol.py','"gtop_boneless.txt", "gtop_lifecycle.txt")',
    '"gtop_boneless.txt", "gtop_lifecycle.txt", "gtop_tab.txt")')

Path('gbop_voice_web/TAB_RELEASE.md').write_text('''# TAB market release — October 3, 2026

## Member flow
Ask for a shift recap naturally. GBOP can read a prepared briefing and refresh detailed market evidence. Ask which Model 1 candle formed, then separately ask about CSD, Super Soup cleanliness, the nested CRT variant and parent objectives.

For future alerts, explicitly request: "Watch NAS during the day shift and DM me when a body soup forms." `manage_market_watch` returns the actual watch ID, range scope, shift and expiry. Null anchor follows selected H1 ranges starting at 8 and their later promotions. An explicit anchor watches that CRT instead. Ask "Show my watches" or "Stop my watches" to list or cancel.

Alerts are private Discord DMs, not unsolicited voice playback. Scans run about every 30 seconds plus processing time, use closed candles, and depend on the existing service/feed being available. M5 body events cannot be established until their five-minute candle closes. No historical events before subscription are sent. The shift-end event has a short delivery grace period. A stale feed pauses the watch; list watches to inspect status.

## Guards
Six active watches per member and 24 total; one leased worker; persistent event IDs and atomic claims; membership plus live Discord role checks before delivery. Uncertain sends are not automatically replayed. New registrations are blocked if no fresh runtime heartbeat exists. Browser and Discord routes recheck authenticated member access. Runtime tables have RLS and no public/client privileges. Watches never edit journals, risk profiles or broker orders.

## Prepared coverage
One changed asset/shift is prepared per scan in rotation, with paired evidence where available. Prepared rows are capped at 128 KiB and retained seven days. Exact evidence remains available on demand. Missing SPX, for example, is explicitly unavailable rather than substituted with US30.

## Acceptance
Automated regression covers candle identity, clean/delivered, clean/failed, unclean/functional, no soup, V1–V6, gaps, same-bar uncertainty, member separation, stale feeds, cancellation, restarts, access revocation and ambiguous delivery. Human voice acceptance is separate: ask about the same NAS Model 1, its subsequent candles and Super Soup; then request the latest journal/photos. Do not broaden member access based only on a server health check.
''')
for path in ('bot.py','gbop_voice_web/server.py','gbop_voice_web/market_data.py','gbop_voice_web/candle_lifecycle.py','gbop_voice_web/voice_runtime.py'):
    ast.parse(Path(path).read_text())
print('TAB integration applied to isolated branch; full regression must pass before commit.')
