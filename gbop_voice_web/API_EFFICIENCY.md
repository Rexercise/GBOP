# API efficiency: lossless first step

## Changes

- Discord text and browser backend function outputs now use compact JSON, matching
  the existing Realtime serialization. This removes separator whitespace only.
  Decoding produces the same object, including every string, timestamp, number,
  scope, warning, consent marker, and delivery receipt.
- Each affected member-context builder no longer fetches a recent-journal result
  that it never used. The actual journal context builder, profile, open trades,
  preferences, intelligence, and trade plan remain unchanged. This removes one
  database read per invocation, not one OpenAI request.
- Provider usage is logged through the numeric allowlist in `api_usage.py`.

Models, voice settings, GTOP canon, tools, history, saved records, interruption
fences, write confirmation, and PR61 synopsis/evidence compaction are unchanged.
The existing Realtime retention ratio is still 0.8 with a 6,000-token
post-instruction conversation limit. No extra model call, paid service, database
migration, new logging destination, prompt cache configuration, or credential is
introduced.

## Reading usage logs

Look for `[GBOP-API-USAGE]`. `surface` identifies `discord_text`,
`browser_backend`, or `discord_realtime`. Only fixed surface/status labels,
`response_events: 1`, `usage_reported`, and known nonnegative integer token
counters are emitted. Response IDs are held only in a bounded connection-local
Realtime duplicate filter; they are never included in this log. Content,
metadata, arguments, member identities, record IDs, and exception text are not
logged by this helper. A failed metrics sink cannot replace a successful answer
or receipt.

- Realtime usage is recorded before interruption filtering, so a stale or
  cancelled response with usage still contributes to the observed totals.
- Responses SDK usage uses plural `input_tokens_details` and
  `output_tokens_details`; Realtime uses singular `input_token_details` and
  `output_token_details`. The helper accepts the fields for the relevant API.
- Zero is a real value. Missing or malformed counts remain absent. Cached,
  audio/text/image, and reasoning counters are subsets of total usage. Do not
  add them to totals. Cache-write tokens are a separate supplied detail.
- `response_events` counts observed response completions/returned objects. It
  does not establish billable request count or user turn count. Failed requests
  without a response, disconnected events, and old duplicate IDs outside the
  bounded filter can make these logs incomplete for accounting.
- Browser-backend counts cover delegated Responses calls, not the browser's
  entire live voice session. Compare actual provider usage/billing before
  claiming project-wide savings. Logging is descriptive, with no budget cutoff.

## Reproducible offline checks

Run:

```sh
python -m unittest discover -s tests -p test_api_efficiency.py
python tests/benchmark_api_efficiency.py
python -m unittest discover -s tests
node tests/test_browser_market_context.js
```

The benchmark uses synthetic shift, later-range, incomplete-coverage,
multiple-context, feeling-confirmation, delivery-receipt, and interrupted-read
fixtures. It checks exact decoded equality and non-mutation, then measures the
actual serialized tool output and embedded request-item bytes before and after.
It makes no provider calls. It measures neither model tokens nor dollar savings,
and excludes fixed prompts, schemas, audio, and other parts of the request.
Realtime output was already compact, so it gets no serialization savings from
this change.

Reference documentation:

- [OpenAI voice cost optimization](https://developers.openai.com/api/docs/guides/voice-latency-cost?voice-api=realtime)
- [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
- [Realtime response usage schema](https://developers.openai.com/api/reference/resources/realtime/server-events)
- [Responses usage schema](https://developers.openai.com/api/reference/resources/responses/methods/retrieve)
