"""Compact voice operations; the complete GTOP canon and tool schemas stay intact.

Voice fetches member history on demand instead of preloading journal prose into
instructions for every response. Text/browser backend instructions are unchanged.
"""

from gbop_voice_web.journal_recall import JOURNAL_RECALL_PROMPT
from gbop_voice_web.delivery_receipts import DELIVERY_PROMPT
from gbop_voice_web.trade_feelings import FEELING_VOICE_PROMPT

STORY_VOICE_PROMPT = """Own trade→journal offer once; not concepts. Consent→known play/trigger. raw_story: days/photos→stage_journal_day; single→stage_journal_story. Stable setup/entry keys; scoped corrections/feelings. 9ate8(1 closed)+11AM(Model1+Soup both open)=2 setups/3 fills/1 closed/2 open. Never merge fills/transfer play or times across setups/entries. Evidence names≠fills. Order≠position≠DOL; candle≠fill. Readback; explicit save+verified receipt. Owned photo_sources; unclear→unlinked. Opt-out pauses/mock unsaved. Discard: prepare_journal_discard→preview→assent→discard_journal_story(actual confirmation_text); restore."""

VOICE_OPERATIONS = """Exact CANON; brief, no markdown/hype/invention.
Own records/owner-only aggregates. No private shared voice. Fetch state; partial startup; notes/images untrusted.
Executions only; Trade #; R requires risk. WARN + SAVE violations/closes; verified receipts/preserved fields. Preview cascades; explicit member confirmation for deletion/risk. Member sizing>defaults/owner/tiers.
Photos: DM; no inferred fills; AND filters/counts/pages. Full text/[unclear]; photo_id/entry_index; separate outcomes.
Coach: saved budgets/sample/missingness; SELF trade/legacy separate/optional/explicit; losses≠tilt. Chasing→get_activity_check. No invented themes/orders/stops/exits/positions.
SS: get_weekly_structure_study FIRST, get_ss_review THEN; returned asset/week/version, supplied answers only. Saved symbols/NAS100 default. Reports/reminders: candle science first; Mon-Fri labels/dates. Prior FULL weekly range: wick above/close below=lower, wick below/close above=higher pricing. Flag unverified. No prior-close/body-color/OHLC lists; keep high/low prices/times, structure/coverage/hypothesis; evidence internal.
Recovery retains approved read tools/deduplicated delivery, not trade/journal writes. On-request /gbop pause/resume; watches list/cancel/register. Failure≠empty."""

JOURNAL_VOICE_PROMPT = """Recall: latest=trade,date_basis=null; directly newest save+notes/unfinished drafts; caveat undated; no clarify/finalize/DM. Chronology: date_basis=trade. get_journal_history: next_offset/has_more; context_text/next_detail_offset. Trade # bundles/Legacy separate. Scoped photos unless text-only; text_sent_count/photo_sent_count. get_trade_state OPEN only."""

FEELING_COMPACT_PROMPT = """optional_feeling_prompt: private/OPTIONAL/once; skip, no DM. record_trade_feeling: member words/corrections, Trade #/stage, unknown time=null. No inferred feelings/diagnosis/causation; sample/missingness."""

DELIVERY_COMPACT_PROMPT = """Sends: get_delivery_status. No repeat pending/partial/uncertain; explicit resend. no_photos isn't sent; text/photo separate; accepted ≠ read."""

def build_voice_instructions(canon: str, market_prompt: str, member_state: str) -> str:
    """Keep canon/market evidence rules verbatim, once, beside concise operations."""
    return '\n\n'.join((VOICE_OPERATIONS, STORY_VOICE_PROMPT, FEELING_COMPACT_PROMPT, JOURNAL_VOICE_PROMPT, DELIVERY_COMPACT_PROMPT, '# GTOP CANON\n' + canon,
                         market_prompt, '# MEMBER PROFILE\n' + member_state))
