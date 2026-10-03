# On-demand fractal CRT analysis

Use `review_market_fractal` only when a member asks about fractal thesis formation or deeper price analysis. Ordinary shift reviews remain unchanged: start with the 8 o'clock range and proceed chronologically. No automatic graph traversal is attached to a session review or prepared brief.

A complete assigned-timeframe body-purge Model 1 can become a separate child CRT anchor. Supported mappings are Monthly → Daily, Weekly → H4, Daily → H1, H4 → M15 and H1 → M5. There is no guessed mapping below M5 or M15.

## Queries and bounds

Both tools require the original asset, root anchor opening/timeframe and review cutoff. The default review expands one level; explicit depth can be 0–3, with at most 12 nodes and 1–4 children per sibling page. `next_child_from_ny` continues siblings. `node_path` contains the selected child opening timestamp at every level, starting at the original root. The engine revalidates each body-purge identity against its actual parent, instead of accepting an arbitrary child ID.

`inspect_market_fractal_node` returns one focused node's detailed same-timeframe CSD, Super Soup structure/local function and objective evidence. Copy its returned `node_path`, `expected_node_id` and `expected_scope_id` while retaining the original root and cutoff. Its children remain lazy. Additional same-timeframe sequel OHLC is paged in that same detail tool with `following_from_ny`, using `following_candle_page.next_following_from_ny`. Preserve the root/path/scope for these pages too. Do not use the ordinary shift-scoped candle query for graph detail; an unrelated shift may still be selected.

The 90-day query limit is a ceiling, not a claim that 90 days are retained. Missing monthly anchor history returns insufficient evidence and no children. Forming candles, source gaps, unavailable resolutions and absent timeframe mappings remain explicit. Tool calls clip evidence to the current time and requested cutoff; future or unclosed source bars cannot establish identities.

## Independent state and source scope

Each node retains its own range, objectives and anchor-timeframe invalidation. For an inherited Model 1, its same-timeframe CSD/Super Soup lifecycle is independent of the parent. Parent invalidation never erases an already identified child and does not stop that child's later lifecycle. Child failure cannot invalidate its parent. The root itself has no invented Model 1 origin; its same-timeframe Model 1 lifecycle is unassessed unless reached through an evidenced parent path.

A lower-timeframe body-purge is related to a higher-timeframe Super Soup only if the actual containing event, direction and pre-CSD timing support that relation. Same-close order uncertainty, missing coverage, wrong direction and post-CSD cases remain qualified. The two candle identities are never labelled equivalent.

Candle IDs bind asset, configured bridge namespace, actual broker symbol, timeframe and anchor timestamps. Node IDs additionally bind the parent lineage: one physical candle can qualify against two different parent ranges, and each occurrence keeps its correct origin and path. Scope IDs additionally bind the exact root and requested cutoff. Evidence IDs bind the observed candle data/resolution/cutoff, so refreshed evidence can change while candle identity stays stable. The existing bridge does not report broker/account identity; a same-symbol provider change therefore cannot be detected, and cross-provider continuity is not claimed. No account-sensitive data is collected.

All calculations are request-local. Nodes do not authorize a member, start monitoring, send notifications, place orders or edit journals.

## Verification

`python -m unittest discover -s tests -p test_fractal_lineage.py -v`

Synthetic fixtures exercise Monthly → Daily → H1 → M5, Weekly → H4 → M15, both directions of parent/child invalidation independence, late CSD, scoped identity, gaps, no-lookahead, partial monthly history, bounded depth/node count, lazy detail and sibling paging. A retained public-market OHLC replay checks Model 1 identities against the established CRT engine. This is analytical regression coverage, not live voice acceptance or validation of trading profitability.
