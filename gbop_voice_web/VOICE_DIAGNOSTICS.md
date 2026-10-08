# Realtime diagnostics and safe recovery

This change measures response churn without changing the model, voice, VAD,
reasoning effort, full GTOP canon, history limit, or output budgets. It does not
claim token or billing savings. No extra provider call or logging destination is
introduced.

## Reading the metrics

`[GBOP-API-USAGE]` remains one row per observed `response.done`, including stale
and cancelled responses before the interruption fence. Do not also record those
events with `RealtimeUsageRecorder`. Existing numeric usage fields retain their
meaning: cached/modality/reasoning counters are subsets, absent is unknown, and
zero is a supplied zero. Logs are incomplete when events are lost or bounded
deduplication entries expire; reconcile cost estimates with provider accounting.

Response rows now include:

- `request_origin`: `tool_continuation`, `rate_limit_retry`, `automatic_vad`, or
  `unknown`. Local origins require the exact server-generated request token to
  match in bounded memory. `origin_evidence=vad_event_inferred` explicitly marks
  the weaker inference from an observed recent speech-stop event; it is not proof
  of why a response was generated.
- `provider_status_reason`: only known cancellation/incomplete reasons, otherwise
  `unknown`. `local_stale_cause` is separate: local speech interruption, stale
  request, shutdown, connection loss, or exhausted quota is not interchangeable
  with the provider's cancellation reason.
- `created_to_first_audio_ms` and `created_to_done_ms`, measured with a monotonic
  clock when both endpoints were observed. First audio is a nonempty provider
  delta, not actual Discord playback or end-to-end perceived latency.
- Session/connection ages and connection attempts, successful connections,
  reconnects, connection failures, and request-send failures. A successful
  connection means the websocket and local admission succeeded, not that the
  provider accepted every setting or produced an answer.
  Request-send failure means the local send did not complete; it does not prove
  the provider rejected it or that no tokens were consumed.
- `provider_error_kind`: only `quota_exhausted`, `rate_limit_exceeded`,
  `provider_error`, or `unknown`.

`[GBOP-VOICE-DIAGNOSTICS]` holds lifecycle and API-error events without token or
response counters. Do not add these rows to response or token totals.

All new diagnostic fields are allowlisted labels, booleans, or nonnegative
numbers. Response and request IDs stay only in bounded connection-local memory.
No member IDs, transcripts, instructions, journal text, tool arguments, or error
messages enter these metrics. Adjacent legacy Realtime session/response event
logs no longer print member, response/item IDs, or raw provider status details.
Telemetry failure cannot change delivery, retries, or saved-action receipts.

## Reconnect behavior

Every new websocket reauthorizes the same server-owned member/context identity
before binding tool state. Per-connection call-ID caches are replaced, allowing
fresh reads after a reconnect. Old work is fenced, and an old worker cannot write
into the new socket's call cache.
Execution operation identities are also namespaced to the admitted socket,
captured before awaiting work, so a provider call ID reused by a later connection
cannot alias a distinct newly requested execution to an old receipt.

Committed or uncertain journal writes and durable private-delivery receipts are
retained. They are reconciled on a fresh user turn; reconnecting cannot replay a
write or a private send. An uncertain write remains blocked until verified. An
authorization/identity admission failure pauses that same session rather than
entering the transient network retry loop or silently replacing its safety state
on background audio. Explicit resume rechecks access with the barriers intact.

## Exhausted credits

Known exhausted-credit codes/types pause the affected session immediately:
queued audio is discarded, new audio/model requests are blocked, and automatic
reconnect/response retries stop. A bounded private status notice and voice status
explain the pause. A message saying “no credits” without a matching provider code
does not trigger this behavior.

After the owner resolves API billing, the member explicitly uses
`/gbop action:resume` (or `/voice`). Resumption is serialized, reauthorizes access,
and restarts the same session object after prior cleanup. Saved/uncertain-action
barriers remain intact, and paused audio is not replayed. Ordinary speech and
private-room autojoin cannot clear this blocked state. TPM throttling retains the
existing bounded, provider-cooldown-aware retry path; network failures retain
their separate reconnect backoff. No account/billing settings are changed.

## Verification and measurement

Offline checks:

```sh
python -m unittest discover -s tests -p 'test_voice_*.py'
python -m unittest discover -s tests
node tests/test_browser_market_context.js
```

Compare matched scenarios before and after deployment: normal conversation,
long narration, barge-in, delayed tools, network reconnect, exhausted quota,
manual resume, committed/uncertain save recovery, and multiple members. Measure
responses per completed task, nonzero cancelled usage, cache share by modality,
request origin, provider/local cancellation causes, and latency. Keep unknown
origins and missing observations explicit. Require unchanged exact recall,
member isolation, durable drafts, write receipts, and natural interruption.

References:

- [Realtime cost guidance](https://developers.openai.com/api/docs/guides/voice-latency-cost?voice-api=realtime)
- [Realtime conversation events](https://developers.openai.com/api/docs/guides/realtime-conversations)
- [Prompt caching and TPM limits](https://developers.openai.com/api/docs/guides/prompt-caching)
