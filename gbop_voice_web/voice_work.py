"""Session-local tool work that never holds up Realtime interruption events."""
import asyncio
import json
import time
from collections import deque
from uuid import uuid4

from gbop_voice_web.voice_runtime import (RECOVERY_NAMES, READ_ONLY_RECOVERY_NAMES, recovery_options,
    delivery_identity, delivery_receipt_fingerprint, JOURNAL_WRITE_NAMES, JOURNAL_WRITE_TERMINAL,
    journal_write_needs_reconciliation)


READ_STATUS_SECONDS = 300
MAX_READ_STATUS_CALLS = 64
WRITE_STATUS_SECONDS = 900


async def create_response(session, options, *, origin='tool_continuation'):
    work = getattr(session, 'tool_work', None)
    if work is not None:
        options = work.response_options(options)
    diagnostics = getattr(session, 'diagnostics', None)
    if diagnostics is not None:
        diagnostics.request_created(options, origin)
    try:
        sent = await session.send_event({'type': 'response.create', 'response': options})
    except BaseException:
        if diagnostics is not None:
            diagnostics.request_failed(options)
        raise
    if sent is False and diagnostics is not None:
        diagnostics.request_failed(options)
    return sent


async def continue_tool_response(session):
    """Consume the pending result exactly once, including tool-only retries."""
    if not session.tool_output_pending:
        return
    session.tool_output_pending = False
    options = getattr(session, '_tool_response_options', {})
    session._tool_response_options = {}
    if getattr(session, '_recovery_active', False):
        options = {**options, **recovery_options(session)}
    session._last_response_options = options
    await create_response(session, options)


class VoiceToolWork:
    """Serialize tools away from the reader; cancel/fence their late results.

    Cancelling an await cannot roll back an already-running synchronous action.
    guarded_voice_tool retains its uncertain-outcome entry in that case, so a
    retry does not repeat a private delivery or record change.
    """
    def __init__(self, session, *, progress_delay=4.0, sleep=None):
        self.session = session
        self.progress_delay = progress_delay
        self.sleep = sleep
        self.tasks = set()
        self.tail = None
        self.operation = None
        self.progress = None
        self.progress_scope = None
        self.generation = 0
        self.response_finished = False
        self.seen_calls = deque(maxlen=256)
        self.responses = set()
        self.stale_responses = deque(maxlen=64)
        self.request_scopes = {}
        self.delivery_recovery = None
        self.read_calls = {}
        self.read_status = None
        self.write_status = None
        self.write_barriers = {}

    @property
    def pending(self):
        return bool(self.tasks)

    def scope(self):
        return (self.session._voice_turn_count, self.session.websocket, self.generation)

    def current(self, scope):
        return not self.session.closed and self.session.websocket is not None and self.scope() == scope

    def accepts(self, event):
        response_id = event.get('response_id') or (event.get('response') or {}).get('id')
        if event.get('type') == 'response.created' and self.stale_request(event.get('response') or {}):
            if response_id:
                self.stale_responses.append(response_id)
            return False
        return response_id is None or response_id not in self.stale_responses

    def response_options(self, options):
        # Realtime echoes response metadata, including on response.created.
        # This fences a retry sent before barge-in but acknowledged after it.
        token = uuid4().hex
        self.request_scopes[token] = self.scope()
        if len(self.request_scopes) > 64:
            self.request_scopes.pop(next(iter(self.request_scopes)))
        return {**options, 'metadata': {**(options.get('metadata') or {}), 'gbop_request': token}}

    def stale_request(self, response):
        token = (response.get('metadata') or {}).get('gbop_request')
        return token is not None and (token not in self.request_scopes
                                     or not self.current(self.request_scopes[token]))

    def response_created(self, response):
        if response.get('id'):
            self.responses.add(response['id'])
        self.response_finished = False

    def response_done(self, response):
        response_id = response.get('id')
        if response_id:
            self.responses.discard(response_id)
            self.stale_responses.append(response_id)
        self.response_finished = response.get('status') in ('completed', 'incomplete')
        if response.get('status') in ('cancelled', 'failed'):
            self.cancel(preserve_read_status=True)

    def cancel(self, *, preserve_read_status=False, member_speech=False):
        context = getattr(self.session, 'market_context', None)
        if context is not None:
            if member_speech:
                context.invalidate(member_speech=True)
            else:
                context.invalidate()
        self.generation += 1
        self.response_finished = False
        self.stale_responses.extend(self.responses)
        self.responses.clear()
        for task in self.tasks:
            task.cancel()
        self.tasks.clear()
        self.tail = None
        self._stop_progress()
        if self.delivery_recovery is not None:
            self.delivery_recovery.cancel()
            self.delivery_recovery = None
        if self.read_status is not None:
            self.read_status.cancel()
            self.read_status = None
        if self.write_status is not None:
            self.write_status.cancel()
            self.write_status = None
        self.write_barriers.clear()
        write = getattr(self.session, '_journal_write_recovery', None)
        if write is not None and not write['reconciled']:
            write.setdefault('interrupted_at', time.monotonic())
        if preserve_read_status:
            for entry in self.read_calls.values():
                entry.setdefault('interrupted_at', time.monotonic())
        else:
            self.read_calls.clear()

    def result_sending(self, call_id):
        """Consume before transport, which may queue output before it returns.

        Cancellation under backpressure leaves acceptance uncertain. Never
        append a second, contradictory output to that same function call.
        """
        self.read_calls.pop(call_id, None)

    def _read_status_current(self, entry, scope):
        identity = delivery_identity(self.session)
        member, owner, session_id = identity
        context = getattr(self.session, 'market_context', None)
        return bool(self.current(scope) and member and len(owner) >= 2 and owner[1] == member
                    and session_id and not getattr(context, 'closed', False)
                    and identity == entry['identity'] and self.session.websocket is entry['websocket']
                    and self.session._voice_turn_count > entry['turn']
                    and 'interrupted_at' in entry
                    and time.monotonic() - entry['interrupted_at'] <= READ_STATUS_SECONDS)

    def recover_reads(self):
        """Close interrupted read calls with metadata only, without starting work.

        The shielded worker can still finish, but its output is fenced. Do not
        leave the model believing a usable result or reply is still on the way.
        No records, arguments, private sends, or response.create enter this path.
        """
        scope = self.scope()
        if any(self._read_status_current(entry, scope) for entry in self.read_calls.values()):
            if self.read_status is None or self.read_status.done():
                self.read_status = asyncio.create_task(self._report_interrupted_reads(scope))

    async def _report_interrupted_reads(self, scope):
        try:
            authorize = getattr(self.session, 'authorize_tool', None)
            if authorize is None:
                return
            for call_id, entry in list(self.read_calls.items()):
                if not self._read_status_current(entry, scope):
                    continue
                if await authorize() or not self._read_status_current(entry, scope):
                    return
                result = {
                    'ok': False, 'status': 'read_interrupted', 'tool': entry['tool'],
                    'result_available': False,
                    'error': 'This read was interrupted before a result was delivered. '
                             'No result or reply will arrive from this call. The underlying '
                             'worker may still finish, but its output is discarded. Do not '
                             'claim this call is still fetching an answer, or retry it '
                             'automatically. A fresh read requires a new user request.',
                }
                # Consume before transport: an interrupted/uncertain send must
                # never duplicate a function output on another barge-in.
                self.read_calls.pop(call_id, None)
                await self.session.send_event({'type': 'conversation.item.create', 'item': {
                    'type': 'function_call_output', 'call_id': call_id,
                    'output': json.dumps(result, separators=(',', ':'))}})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print('[GBOP-READ-STATUS] unavailable:', type(exc).__name__)
        finally:
            if self.read_status is asyncio.current_task():
                self.read_status = None

    def _write_status_current(self, entry, scope):
        if (not entry or not self.current(scope)
                or entry is not getattr(self.session, '_journal_write_recovery', None)):
            return False
        identity = delivery_identity(self.session)
        member, owner, session_id = identity
        context = getattr(self.session, 'market_context', None)
        return bool(member and len(owner) >= 2 and owner[1] == member and session_id
                    and not getattr(context, 'closed', False)
                    and identity == entry['identity'] and self.session.websocket is entry['websocket']
                    and self.session._voice_turn_count > entry['turn']
                    and 'interrupted_at' in entry and not entry['reconciled']
                    and time.monotonic() - entry['interrupted_at'] <= WRITE_STATUS_SECONDS)

    def recover_writes(self):
        """Retain committed save facts across barge-in, without replay or speech."""
        entry = getattr(self.session, '_journal_write_recovery', None)
        scope = self.scope()
        if not self._write_status_current(entry, scope):
            return
        if self.write_status is None or self.write_status.done():
            self.write_status = asyncio.create_task(self._report_interrupted_write(entry, scope))

    async def _report_interrupted_write(self, entry, scope):
        try:
            authorize = getattr(self.session, 'authorize_tool', None)
            if authorize is None:
                return
            # Pending can become saved while auth or transport is awaited. Drain
            # the newest snapshot in this task so that change cannot be lost.
            while self._write_status_current(entry, scope):
                if await authorize() or not self._write_status_current(entry, scope):
                    return
                outcome = dict(entry['outcome'])
                fingerprint = json.dumps(outcome, sort_keys=True, separators=(',', ':'))
                if fingerprint == entry['published']:
                    return
                text = ('JOURNAL WRITE STATUS CONTEXT. Metadata for the earlier interrupted '
                        'record change, not a new request. Use only for that save or its status. '
                        'Saved means the database transaction committed: report the actual Trade # '
                        'and replace any earlier waiting claim. If discarded=true or draft_status=discarded, say the draft was discarded and can be restored; never resume it. If restored=true, say the unfinished draft was restored, not finalized. Otherwise if draft_status is unfinished, say the '
                        'unfinished journal was saved; do not claim it is finalized or invent a Trade #. '
                        'Pending means its worker has not '
                        'finished; it does not promise a reply. Uncertain means a save may have '
                        'happened and no outcome is verified. Never repeat open/add/save '
                        'automatically. A distinct new user request is required for a new write '
                        'after reconciliation. This does not assert that optional risk checks '
                        'finished or that a journal contains every requested detail. Outcome JSON: '
                        + json.dumps(outcome, separators=(',', ':')))
                sent = await self.session.send_event({'type': 'conversation.item.create', 'item': {
                    'type': 'message', 'role': 'system',
                    'content': [{'type': 'input_text', 'text': text}]}})
                if sent is False or not self._write_status_current(entry, scope):
                    return
                entry['published'] = fingerprint
                if outcome['status'] in JOURNAL_WRITE_TERMINAL and outcome == entry['outcome']:
                    entry['reconciled'] = True
                    entry['reconciled_turn'] = self.session._voice_turn_count
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print('[GBOP-WRITE-STATUS] unavailable:', type(exc).__name__)
        finally:
            if self.write_status is asyncio.current_task():
                self.write_status = None

    def recover_delivery(self):
        """Publish exact interrupted-operation facts in current context, silently.

        Audio-only turns have no local transcript to classify. This contextual
        evidence explicitly applies only to questions about that prior delivery;
        it neither assumes status intent nor starts a reply. The reader stays free
        to handle new speech while authentication/storage work is in flight.
        """
        entry = getattr(self.session, '_delivery_recovery', None)
        scope = self.scope()
        if not self._recovery_current(entry, scope):
            return
        if self.delivery_recovery is not None and not self.delivery_recovery.done():
            return
        self.delivery_recovery = asyncio.create_task(self._recover_delivery(entry, scope))

    def _recovery_current(self, entry, scope):
        from gbop_voice_web.delivery_receipts import REUSE_SECONDS
        if not entry or not self.current(scope) or entry is not getattr(self.session, '_delivery_recovery', None):
            return False
        identity = delivery_identity(self.session)
        member, owner, session_id = identity
        context = getattr(self.session, 'market_context', None)
        return bool(member and len(owner) >= 2 and owner[1] == member and session_id
                    and not getattr(context, 'closed', False)
                    and identity == entry['identity'] and self.session.websocket is entry['websocket']
                    and self.session._voice_turn_count > entry['turn']
                    and time.monotonic() - entry['started'] <= REUSE_SECONDS
                    and not entry['reported_terminal'] and entry.get('reader')
                    and (entry.get('receipt') or {}).get('receipt_id'))

    async def _recover_delivery(self, entry, scope):
        try:
            authorize = getattr(self.session, 'authorize_tool', None)
            if authorize is None or not self._recovery_current(entry, scope) or await authorize():
                return
            if not self._recovery_current(entry, scope):
                return
            receipt_id = entry['receipt']['receipt_id']
            try:
                result = await entry['reader'](receipt_id)
            except Exception:
                result = None
            if not self._recovery_current(entry, scope) or await authorize():
                return
            if not self._recovery_current(entry, scope):
                return
            from gbop_voice_web.delivery_receipts import SAFE_FIELDS, TERMINAL
            candidates = result.get('receipts', []) if isinstance(result, dict) and result.get('ok') else []
            receipt = next((r for r in candidates if isinstance(r, dict)
                            and r.get('receipt_id') == receipt_id and r.get('tool') == entry['tool']), None)
            if receipt is None or receipt.get('status') not in TERMINAL | {'pending'}:
                receipt = {'ok': False, 'status': 'unknown', 'receipt_id': receipt_id,
                           'tool': entry['tool'], 'error': 'No current outcome could be verified. This does not mean nothing was sent.'}
            receipt = {k: v for k, v in receipt.items() if k in SAFE_FIELDS}
            fingerprint = delivery_receipt_fingerprint(receipt)
            if fingerprint == entry['published']:
                return
            text = ('PRIVATE DELIVERY STATUS CONTEXT. This is a read-only receipt for the earlier '
                    'interrupted request, not a new request or send. Use it only if the current user '
                    'asks about that delivery. A terminal receipt replaces earlier waiting claims. '
                    'Keep partial, pending, unknown and uncertain outcomes explicit; do not promise '
                    'that unconfirmed work is still progressing. No automatic resend. Accepted '
                    'messages do not prove the member read them. Receipt JSON: '
                    + json.dumps(receipt, separators=(',', ':')))
            sent = await self.session.send_event({'type': 'conversation.item.create', 'item': {
                'type': 'message', 'role': 'system',
                'content': [{'type': 'input_text', 'text': text}]}})
            if sent is not False and self._recovery_current(entry, scope):
                entry['published'] = fingerprint
                entry['reported_terminal'] = receipt.get('status') in TERMINAL
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A failed read or context write is not a failed send. Keep the exact
            # operation available for a later current-turn read, never resend it.
            print('[GBOP-DELIVERY-RECOVERY] unavailable:', type(exc).__name__)
        finally:
            if self.delivery_recovery is asyncio.current_task():
                self.delivery_recovery = None

    def _stop_progress(self):
        if self.progress is not None:
            self.progress.cancel()
            self.progress = None

    def start(self, item):
        call_id = item.get('call_id')
        if not call_id or not item.get('name') or call_id in self.seen_calls:
            return
        self.seen_calls.append(call_id)
        scope = self.scope()
        write = getattr(self.session, '_journal_write_recovery', None)
        queued_same_turn = bool(write and self.tail is not None and not self.tail.done()
            and write['turn'] == self.session._voice_turn_count
            and 'interrupted_at' not in write
            and write['identity'] == delivery_identity(self.session)
            and write['websocket'] is self.session.websocket)
        if (item['name'] in JOURNAL_WRITE_NAMES
                and journal_write_needs_reconciliation(write, self.session._voice_turn_count)
                and not queued_same_turn):
            # Preserve admission barriers for interrupted/older-turn retries.
            # An uninterrupted same-turn call instead waits for its scheduled
            # predecessor. guarded_voice_tool still requires that predecessor's
            # verified result to have reached context before executing this one.
            self.write_barriers[call_id] = write
            if len(self.write_barriers) > 256:
                self.write_barriers.pop(next(iter(self.write_barriers)))
        if item['name'] in READ_ONLY_RECOVERY_NAMES:
            self.read_calls[call_id] = dict(tool=item['name'], identity=delivery_identity(self.session),
                websocket=self.session.websocket, turn=self.session._voice_turn_count)
            if len(self.read_calls) > MAX_READ_STATUS_CALLS:
                self.read_calls.pop(next(iter(self.read_calls)))
        previous = self.tail or self.operation
        task = asyncio.create_task(self._run(item, scope, previous))
        self.tasks.add(task)
        self.tail = task
        # One text-only private notice per user turn, with no model/TTS call.
        if (item['name'] in RECOVERY_NAMES and item['name'] != 'get_journal_history' and self.progress is None
                and self.progress_scope != scope[:2]):
            self.progress = asyncio.create_task(self._progress(scope))

    async def _progress(self, scope):
        try:
            await (self.sleep or asyncio.sleep)(self.progress_delay)
            if not self.current(scope) or not self.pending:
                return
            self.progress_scope = scope[:2]
            recovery = getattr(self.session, 'rate_limit_recovery', None)
            if recovery is not None and recovery.notified:
                return
            await asyncio.wait_for(self.session.member.send(
                "I'm still checking your request."), timeout=3)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print('[GBOP-RT-PROGRESS] notice unavailable:', type(exc).__name__)
        finally:
            if self.progress is asyncio.current_task():
                self.progress = None

    async def _run(self, item, scope, previous):
        task = asyncio.current_task()
        try:
            if previous is not None:
                await asyncio.shield(previous)
            if self.current(scope):
                await self.session.execute_tool(item)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.current(scope):
                self.session.last_error = 'Voice tool failed: ' + type(exc).__name__
                print('[GBOP-RT-TOOL]', self.session.last_error)
        finally:
            try:
                if self.current(scope) and self.tasks == {task}:
                    self._stop_progress()
                    if self.response_finished:
                        await continue_tool_response(self.session)
            finally:
                # Keep the continuation send cancellable until it finishes.
                self.tasks.discard(task)

    async def run_tool(self, scope, runner):
        # Only shield the tool action. Outbound results and reply requests remain
        # cancellable, so an interrupted send cannot revive an old reply.
        self.operation = asyncio.create_task(self._operate(scope, runner))
        return await asyncio.shield(self.operation)

    async def _operate(self, scope, runner):
        try:
            if self.current(scope):
                return await runner()
            return {'ok': False, 'error': 'This voice request is no longer current.'}
        except Exception as exc:
            # The operation can outlive its cancelled waiter. Consume failures
            # here as well so they cannot become unobserved background errors.
            return {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}
        finally:
            if self.operation is asyncio.current_task():
                self.operation = None
