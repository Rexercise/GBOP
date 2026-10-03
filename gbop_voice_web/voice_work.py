"""Session-local tool work that never holds up Realtime interruption events."""
import asyncio
from collections import deque
from uuid import uuid4

from gbop_voice_web.voice_runtime import RECOVERY_NAMES, recovery_options


async def create_response(session, options):
    work = getattr(session, 'tool_work', None)
    if work is not None:
        options = work.response_options(options)
    return await session.send_event({'type': 'response.create', 'response': options})


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
            self.cancel()

    def cancel(self):
        self.generation += 1
        self.response_finished = False
        self.stale_responses.extend(self.responses)
        self.responses.clear()
        for task in self.tasks:
            task.cancel()
        self.tasks.clear()
        self.tail = None
        self._stop_progress()

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
        previous = self.tail or self.operation
        task = asyncio.create_task(self._run(item, scope, previous))
        self.tasks.add(task)
        self.tail = task
        # One text-only private notice per user turn, with no model/TTS call.
        if (item['name'] in RECOVERY_NAMES and self.progress is None
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
