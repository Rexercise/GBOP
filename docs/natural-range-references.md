# Natural range references

`resolve_market_range` accepts a member's words, not a range ID. Known text is
resolved before model generation through the existing authenticated backend and
market prefetch path. Audio-only sessions expose the same tool; extraction of
the spoken words and the decision to call it still depend on the voice model.

Examples: “today's gold 9ate8, morning session” and “yesterday's NAS H4 GCT
range.” The returned human description names the market, date, window, New York
timezone, actual broker symbol and MT5 source. Internal identities include the
feed, broker symbol, window kind and exact opening/ending timestamps. No member
is asked to read or supply those identities.

| Named range | New York reference window | Existing evidence reader |
| --- | --- | --- |
| 9ate8 morning / evening | 08:00–09:00 / 20:00–21:00 | H1 CRT; review bounded by noon / midnight or current time |
| Young Lefty morning / evening | 07:00–08:00 / 19:00–20:00 | H1 CRT with the same bounded shift cutoff |
| GCT | 09:00–13:00 | Four-hour window aggregated from bridge bars; cutoff at its close or current time |

These definitions come from `gbop_voice_web/gtop_knowledge.txt`. GCT's four-hour
window is distinct from the 09:00–12:00 Day Shift and from a native broker or
TradingView **Chart H4** candle. The bridge supplies native H1 provenance but no
native H4 provenance; a native Chart H4 request is explicitly unsupported and
never silently replaced by GCT. This feature does not add trading doctrine or
claim GCT setup qualification from its identity alone.

Missing or conflicting selectors produce one focused question. Its reply keeps
the other stated facts within that authenticated conversation. Corrections
override the stated fields; a changed market/date cannot silently inherit an
old morning/evening choice. Multiple-context ambiguity cannot borrow a previous
single-market selection. New speech fences delayed results. Pending clarification
and range selections are process-local, not durable across application restarts
or transferable to another member/session.

The resolver calls the existing read-only `review_market_crt` adapter. A complete
reference and matching asset, broker symbol, source and timestamps are required
before selection. Forming references and missing reference bars remain
unverified. Missing later bars remain unknown observation coverage. Complete
historical windows remain usable when the quote is stale, with `is_live=false`
and feed-health metadata retained. No external feed substitution or market
outcome, execution, journal write, R or P/L inference is introduced. Follow-up
reads fail closed if the selected feed/symbol changes.

## Selected-chart handoff: design only

GBOP cannot currently observe an indicator dot, selected chart range or browser
selection. `DelegateRequest` and the existing authenticated `/api/delegate`
route carry conversation history and session/turn identity, not chart telemetry.
No handoff endpoint, credentials, chart hook or automatic transport is added by
this change.

The smallest usable manual handoff is an indicator-generated human description
that the member explicitly supplies to the existing conversation. The resolver
can then independently retrieve the named window. Building that indicator
export and verifying its correspondence to the selected point are separate work.

A future structured handoff could carry a versioned public-market selector:
`version`, `range_kind`, `range_name`, `source_provider`, `source_symbol`,
`trading_timezone`, `trading_date`, `session`, `window_start_utc`,
`window_end_utc`, `anchor_timeframe`, and `observed_at_utc`. Native Chart H4
would additionally require verified chart-candle opening/closing boundaries.
An indicator's internal range ID could be a diagnostic hint, never authority.

That payload would need an explicitly implemented, bounded input on the existing
authenticated member/session route, with normal turn fencing and eligibility
checks. It must not accept a member ID or credential from the payload. Validate
all fields and timezone/UTC agreement, reject stale or incompatible selectors,
and compare the declared source with the actual bridge source. TradingView feed
prices cannot be treated as equivalent to MT5 prices without verified mapping.
Client-supplied OHLC/outcomes must not become evidence; retrieve them independently.
Cross-origin delivery, indicator export capability, permission/CSRF handling and
user-controlled selection all require implementation and testing before claiming
live linkage. No new access is needed for the current text resolver.

## Local validation

`tests/test_range_reference.py` uses synthetic bars in isolated SQLite tables,
the real market adapter, conversation/prefetch and voice payload paths. An
authenticated backend test substitutes only the model response and member
context; other tests exercise persisted synthetic eligibility and revocation.
No live Discord, TradingView, market provider, real journal or PostgreSQL locking
claim is involved. No schema migration, dependency or infrastructure change is
required. Before release, run the full regression suite and browser simulation;
after an approved release, perform controlled text/voice checks with the actual
feed and supported examples. Source publication/deployment are separate actions.
# Narrative journal integration

Natural range references can accompany `stage_journal_story` narration. The
server preserves the narration in the existing private draft first. Supported
selectors queue `resolve_market_range`; a successful read enriches that same
owned draft with its exact reference and market snapshot. Finalizing uses the
revision-checked draft, never caller-supplied IDs or market facts. No schema,
permission, credential or infrastructure change is required.

The existing draft payload and journal metadata now carry `journal_range`:

- Member wording, resolved selectors, status and any focused clarification.
- On verification: exact range identity, feed/symbol, New York window, source
  snapshot, and available freshness/coverage flags. Historical verified identity
  does not imply a live quote, valid setup, execution, fill or outcome.
- Range correction history; canonical journal audit events retain complete
  before/after metadata. An explicit unverified replacement clears the current
  `market_review` with null; its previous snapshot remains in audit history.

`get_journal_story` can resume an unfinished lookup after reconnect. Its lookup
uses the stored absolute date, so yesterday/today never moves to another date
when a later session resumes it. Text backend prefetch can enrich an already
staged narrative; audio still requires the model to faithfully stage its
transcript and call the returned `range_next_tool`. Neither path finalizes the
journal without the existing explicit save request.

Missing feeds, incomplete anchor coverage, unsupported windows or ambiguous
selectors leave the member's description unverified and do not block narrative
saving. Current verification remains limited to the existing named 9ate8,
Young Lefty and GCT New York windows on the connected MT5 feed. Native Chart H4,
arbitrary custom ranges, other feeds, and selected TradingView chart points
remain unsupported. No chart-selection transport is implemented by this patch.

Another range mention does not silently replace a saved association. Corrections
must identify the intended owned draft/Trade # and be explicit. Draft revision,
conversation generation, current membership checks and a range baseline for the
selected journal reject stale enrichment or competing draft corrections. The
member's unfinished narration remains available if a correction conflicts.

`tests/test_journal_range.py` exercises the real narrative/market/journal handlers
with synthetic M5 bars and file-backed SQLite, including genuine connection
close/reopen. It covers create/save/recall, corrections, retry, missing data,
ambiguity, stale callbacks, competing drafts, member isolation and revocation.
Network access is prohibited. Supplied text/audio transcripts stand in for model
extraction; these are not live Discord, ASR, production feed, PostgreSQL locking
or PostgreSQL RLS tests. Existing PostgreSQL tests remain a separate gate.
