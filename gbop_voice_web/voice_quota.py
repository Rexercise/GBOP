"""Stop an exhausted-credit session without retrying speech or record actions."""
import asyncio


QUOTA_CODES = frozenset({'insufficient_quota', 'credit_balance_exhausted',
                         'billing_hard_limit_reached'})
QUOTA_NOTICE = (
    'GBOP voice is paused because its OpenAI API credits are unavailable. '
    'Automatic retries have stopped. Ask the GBOP owner to check API billing, '
    'then use `/gbop action:resume`. Pausing does not undo an admitted save; '
    'check saved state before repeating an interrupted save.'
)
ACCESS_NOTICE = (
    'GBOP voice is paused because its member access or recovery identity could '
    'not be verified. Automatic reconnects have stopped. Use '
    '`/gbop action:resume` to recheck access. Check saved state before repeating '
    'an interrupted save.'
)


def voice_blocked(session):
    return bool(getattr(session, 'quota_blocked', False)
                or getattr(session, 'connection_blocked', False))


def discard_queued_audio(session):
    queue = getattr(session, 'audio_queue', None)
    if queue is not None:
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break


def block_for_connection(session):
    """Keep recovery barriers through denied/transient admission, never replace."""
    session.connection_blocked = True
    session.last_error = ACCESS_NOTICE
    session.ready.clear()
    session.tool_output_pending = False
    source = getattr(session, 'output_source', None)
    if source is not None:
        source.abort()
    discard_queued_audio(session)


def is_quota_error(error):
    """Only provider codes/types, never guessed text or arbitrary HTTP 429s."""
    return isinstance(error, dict) and any(
        isinstance(error.get(field), str) and error[field] in QUOTA_CODES
        for field in ('code', 'type'))


async def block_for_quota(session, error):
    """Mark blocked before any await. The receiver then exits for socket cleanup.

    Keep the session object and its write/delivery barriers. Background speech
    must not recreate a connection or queue audio to replay after credit returns.
    """
    if not is_quota_error(error):
        return False
    if getattr(session, 'quota_blocked', False):
        return True
    session.quota_blocked = True
    session.last_error = QUOTA_NOTICE
    ready = getattr(session, 'ready', None)
    if ready is not None:
        ready.clear()
    session.tool_output_pending = False
    recovery = getattr(session, 'rate_limit_recovery', None)
    if recovery is not None:
        recovery.cancel(reset=True)
    work = getattr(session, 'tool_work', None)
    if work is not None:
        work.cancel()
    delivery = getattr(session, 'market_delivery', None)
    if delivery is not None:
        delivery.cancel()
    source = getattr(session, 'output_source', None)
    if source is not None:
        source.abort()
    discard_queued_audio(session)
    diagnostics = getattr(session, 'diagnostics', None)
    if diagnostics is not None:
        diagnostics.cancel_active('quota_exhausted')
    # This is the only notice for this blocked period, even if several failed
    # responses race. Private status remains available if DMs are disabled.
    notice = asyncio.create_task(_notify_quota(session.member))
    session._quota_notice_task = notice
    # The sender may stop before this receiver. Its cleanup must not cancel the
    # only status notice. The independent task is still bounded to three seconds.
    await asyncio.shield(notice)
    return True


async def _notify_quota(member):
    try:
        await asyncio.wait_for(member.send(QUOTA_NOTICE), timeout=3)
    except Exception:
        pass


async def resume_after_quota(session):
    """Resume quota/admission pause only by command, never audio/autojoin.

    Restart the same object after its old runner has finished, so retained save
    and private-delivery barriers survive. Reauthorization precedes resumption.
    """
    lock = getattr(session, '_quota_resume_lock', None)
    if lock is None:
        lock = session._quota_resume_lock = asyncio.Lock()
    async with lock:
        await _resume_locked(session)


async def _resume_locked(session):
    from gbop_voice_web.voice_runtime import delivery_identity
    if not voice_blocked(session):
        return
    identity = delivery_identity(session)
    context = getattr(session, 'market_context', None)
    authorize = getattr(session, 'authorize_tool', None)
    if authorize is None or await authorize():
        raise RuntimeError('Your voice access could not be verified. Please sign in again.')
    if session.closed:
        raise RuntimeError('This voice session has ended. Join again before resuming.')
    runner = getattr(session, 'runner', None)
    if runner is not None and not runner.done():
        runner.cancel()
        await asyncio.wait_for(asyncio.gather(runner, return_exceptions=True), timeout=5)
    if session.closed:
        raise RuntimeError('This voice session ended while resuming. Join again.')
    if (delivery_identity(session) != identity
            or getattr(session, 'market_context', None) is not context):
        raise RuntimeError('Your voice identity changed while resuming. Please sign in again.')
    session.quota_blocked = False
    session.connection_blocked = False
    session.last_error = None
    session.runner = asyncio.create_task(session.run())
