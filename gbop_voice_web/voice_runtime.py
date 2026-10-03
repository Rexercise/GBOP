"""Small, credential-free helpers for Discord Realtime context management."""
from copy import deepcopy
import asyncio
import re


def compact_voice_tool_result(name, result):
    """Page candle tables without discarding summaries or timestamped events.

    The full market tool remains unchanged for other callers. Voice can request
    the next page or a narrower window using inspect_market_candles.
    """
    if name not in {'review_market_session', 'review_market_crt', 'inspect_market_candles'}:
        return result
    result = deepcopy(result)

    # The shift story already contains 8's full CRT evidence. Sending the older
    # 9ate8 view as well duplicates events, coverage and assigned candle tables.
    review = result.get('review', {})
    story = review.get('shift_story')
    if name == 'review_market_session' and isinstance(story, dict):
        ranges = story.get('ranges', [])
        for observation in review.get('observations', []):
            if observation.get('play') == '9ate8' and any(r.get('label') == '9ate8' for r in ranges):
                observation.pop('evidence', None)
                observation['evidence_ref'] = 'shift_story.ranges: label=9ate8'
            elif 'evidence' in observation:
                evidence = observation.pop('evidence')
                observation['evidence_summary'] = {k: evidence[k] for k in (
                    'status', 'observed_direction', 'invalidated_at_ny', 'events') if k in evidence}
                observation['detail_tool'] = 'review_market_crt for this play anchor; assigned candles omitted'
        for row in ranges:
            # Keep OHLC, first extreme times, precision, all events/objectives,
            # body evidence and progression. Repeated coverage extrema and last
            # occurrence metadata remain available via inspect_market_candles.
            anchor = row.get('anchor', {})
            for key in ('high_last_seen', 'low_last_seen', 'high_occurrences', 'low_occurrences'):
                anchor.pop(key, None)
            coverage = row.get('observation_coverage')
            if isinstance(coverage, dict):
                row['observation_coverage'] = {key: coverage[key] for key in (
                    'start_ny', 'end_ny', 'complete', 'source_resolution_seconds',
                    'bar_count', 'missing_bar_count', 'coverage_note') if key in coverage}
        review['voice_detail_note'] = (
            'Whole hourly shift sequence and range events retained. Duplicate 9ate8 evidence '
            'is in shift_story. Per-range coverage extrema and last extreme occurrences '
            'are omitted; use inspect_market_candles for those details. Never infer omitted values.')

        # The narrative is first so the voice reply is grounded in the later
        # outcome before it encounters the opening-play failure and raw evidence.
        if 'recap' in story:
            result['review'] = {'shift_recap': story.pop('recap'), **review}

    def page(value):
        if isinstance(value, dict):
            rows = value.get('candles')
            if isinstance(rows, list) and len(rows) > 4:
                value['candles'] = rows[:4]
                value['next_start_ny'] = rows[4]['start_ny']
                value['voice_page'] = {
                    'returned': 4, 'available_in_requested_window': len(rows),
                    'instruction': 'Partial candle table. Use inspect_market_candles with next_start_ny '
                                   'or a narrower requested time window for further candles. '
                                   'Do not infer omitted candle values or confirmation.',
                }
            for child in value.values():
                page(child)
        elif isinstance(value, list):
            for child in value:
                page(child)
    page(result)
    return result


VOICE_TRUNCATION = {
    'type': 'retention_ratio',
    'retention_ratio': 0.8,
    'token_limits': {'post_instructions': 6000},
}


class VoiceRateLimitRecovery:
    """Bounded response-only recovery; never re-execute a tool or a stale turn."""
    def __init__(self, session):
        self.session = session
        self.task = None
        self.attempts = 0
        self.notified = False

    def cancel(self, reset=False):
        if self.task is not None:
            self.task.cancel()
            self.task = None
        if reset:
            self.attempts = 0
            self.notified = False

    def failed(self, error):
        if error.get('code') != 'rate_limit_exceeded' or self.task is not None:
            return
        if self.session.closed:
            return
        match = re.search(r'try again in (\d+(?:\.\d+)?)s', str(error.get('message', '')), re.I)
        delay = min(60.0, max(2.0, float(match.group(1)) + 1.0)) if match else 15.0
        exhausted = self.attempts >= 2
        self.attempts += 1
        self.task = asyncio.create_task(self._recover(delay, exhausted))

    async def _recover(self, delay, exhausted):
        session = self.session
        turn, websocket = session._voice_turn_count, session.websocket
        try:
            if not self.notified or exhausted:
                self.notified = True
                notice = ('Voice is still rate-limited. Please wait a minute, then ask again.'
                          if exhausted else
                          f'Voice hit a temporary rate limit. I will retry the reply in about {int(delay) + 1} seconds. '
                          'You do not need to repeat your request.')
                try:
                    await asyncio.wait_for(session.member.send(notice), timeout=3)
                except Exception as exc:
                    print('[GBOP-RT-RECOVERY] notice unavailable:', type(exc).__name__)
            if exhausted:
                return
            await asyncio.sleep(delay)
            if session.closed or session.websocket is not websocket or session._voice_turn_count != turn:
                return
            # Existing function outputs are in the conversation. Disable tools
            # during recovery so saves/deletes and other actions cannot repeat.
            await session.send_event({'type': 'response.create', 'response': {
                **getattr(session, '_last_response_options', {}),
                'tool_choice': 'none',
            }})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            session.last_error = 'Voice recovery failed: ' + type(exc).__name__
            print('[GBOP-RT-RECOVERY]', session.last_error)
        finally:
            if self.task is asyncio.current_task():
                self.task = None
