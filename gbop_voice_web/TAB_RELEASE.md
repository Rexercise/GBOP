# TAB market release — October 3, 2026

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

SPX collector support is included in this source release, but changing the hosted bot does not update the separate Windows collector or its config.json. SPX availability must be confirmed from received broker history; no substitute symbol is invented.
